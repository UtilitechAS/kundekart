#!/usr/bin/env python3
"""
Bygger site/kunder.geojson (én markør per land+postnummer) og avvik.md.

Kilder:
  - PrestaShop Webservice: leveringsadresser for alle ordrer
  - data/lectronz.csv: statisk liste (country,postcode,city)
  - data/corrections.csv: manuelle rettelser
Koordinater:
  - GeoNames postnummerfiler (per land, lastes ned til .cache/)
  - Nominatim (OpenStreetMap) som siste utvei, resultater caches i data/nominatim_cache.json
Kun standardbiblioteket brukes.
"""
import csv, difflib, io, json, math, os, re, sys, time, unicodedata, urllib.parse, urllib.request, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
CACHE = os.path.join(ROOT, ".cache")
SITE = os.path.join(ROOT, "site")
UA = "amsleser-kundekart/1.0 (+https://github.com/UtilitechAS/kundekart)"

# Land med kun sifre i postnummeret: alt annet enn sifre fjernes (f.eks. "DK-2100", "SE 123 45").
NUMERIC = set("NO DK SE FI AX DE AT CH IS CY ES SI HU LT PL FR FO HR ZA IT BE LU EE LV CZ SK GR PT".split())
WRONG_COUNTRY_CANDIDATES = ["NO", "SE", "DK", "FI", "DE", "AT", "CH"]
CITY_MOVE_KM = 25  # sted og postnummer lenger fra hverandre enn dette => bruk stedet, meld avvik


# ---------- hjelpefunksjoner ----------
def norm_pc(country, pc):
    pc = (pc or "").upper()
    if country in NUMERIC:
        return re.sub(r"\D", "", pc)
    return re.sub(r"[\s\-]", "", pc)


def norm_name(s):
    s = (s or "").lower().replace("æ", "ae").replace("ø", "o").replace("å", "a").replace("ß", "ss")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\b(n|s|v|o|k|c|nv|sv|ost|vest|nord|syd)\b$", "", s.strip())  # "Aarhus N", "København K"
    return " ".join(s.split())


def name_match(a, b):
    a, b = norm_name(a), norm_name(b)
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.8


def km(p, q):
    la1, lo1, la2, lo2 = map(math.radians, (p[0], p[1], q[0], q[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def http_get(url, headers=None, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# ---------- PrestaShop ----------
def ps_get(resource, display):
    base = os.environ["PS_API_URL"].rstrip("/")
    key = os.environ["PS_API_KEY"]
    q = urllib.parse.urlencode({"display": display, "output_format": "JSON", "ws_key": key})
    data = json.loads(http_get(f"{base}/{resource}?{q}"))
    rows = data.get(resource, [])
    return rows if isinstance(rows, list) else []


def load_prestashop():
    countries = {str(c["id"]): c["iso_code"] for c in ps_get("countries", "[id,iso_code]")}
    addresses = {str(a["id"]): a for a in ps_get("addresses", "[id,postcode,city,id_country]")}
    used = {str(o["id_address_delivery"]) for o in ps_get("orders", "[id,id_address_delivery]")}
    out = []
    for aid in used:
        a = addresses.get(aid)
        if a:
            out.append((countries.get(str(a["id_country"]), "??"), a.get("postcode", ""), a.get("city", ""), "PS"))
    print(f"PrestaShop: {len(used)} leveringsadresser, {len(out)} funnet")
    return out


def load_lectronz():
    with open(os.path.join(DATA, "lectronz.csv"), encoding="utf-8") as f:
        return [(r["country"], r["postcode"], r["city"], "Lectronz") for r in csv.DictReader(f)]


def load_corrections():
    """country,postcode,new_postcode,lat,lon,note  –  lat/lon > new_postcode > (tom = ignorer)"""
    p = os.path.join(DATA, "corrections.csv")
    corr = {}
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("country"):
                    corr[(r["country"].strip().upper(), norm_pc(r["country"].strip().upper(), r["postcode"]))] = r
    return corr


# ---------- GeoNames ----------
class GeoNames:
    def __init__(self):
        self.pc = {}      # country -> {norm_pc: [(place, admin2, lat, lon)]}
        self.places = {}  # country -> {norm_name: (lat, lon)}

    def load(self, cc):
        if cc in self.pc:
            return
        os.makedirs(CACHE, exist_ok=True)
        path = os.path.join(CACHE, f"{cc}.zip")
        if not (os.path.exists(path) and zipfile.is_zipfile(path)):
            try:
                blob = http_get(f"https://download.geonames.org/export/zip/{cc}.zip")
            except Exception as e:
                blob, err = b"", str(e)
            else:
                err = f"ikke en zip-fil ({len(blob)} byte, starter med {blob[:60]!r})"
            if blob[:2] == b"PK":
                open(path, "wb").write(blob)
            else:
                print(f"GeoNames: ingen gyldig fil for {cc} – {err}. Faller tilbake til Nominatim.")
                self.pc[cc], self.places[cc] = {}, {}
                return
        pcs, places = {}, {}
        with zipfile.ZipFile(path) as z:
            name = next((n for n in z.namelist() if n.upper() == f"{cc}.TXT"), None)
            text = z.read(name).decode("utf-8") if name else ""
        for line in text.splitlines():
            f = line.split("\t")
            if len(f) < 11 or not f[9] or not f[10]:
                continue
            lat, lon = float(f[9]), float(f[10])
            pcs.setdefault(norm_pc(cc, f[1]), []).append((f[2], f[5], lat, lon))
            for nm in (f[2], f[5]):
                if nm:
                    places.setdefault(norm_name(nm), (lat, lon))
        self.pc[cc], self.places[cc] = pcs, places
        print(f"GeoNames {cc}: {len(pcs)} postnumre")

    def lookup_pc(self, cc, pc):
        self.load(cc)
        hits = self.pc[cc].get(pc)
        if not hits and cc not in NUMERIC:  # NL "1234AB" -> "1234", JE "JE23AB" -> "JE2"
            for n in range(len(pc) - 1, 2, -1):
                hits = self.pc[cc].get(pc[:n])
                if hits:
                    break
        return hits

    def lookup_place(self, cc, city):
        self.load(cc)
        n = norm_name(city)
        if not n:
            return None
        if n in self.places[cc]:
            return self.places[cc][n]
        best = difflib.get_close_matches(n, self.places[cc].keys(), n=1, cutoff=0.88)
        return self.places[cc][best[0]] if best else None


# ---------- Nominatim ----------
class Nominatim:
    def __init__(self):
        self.path = os.path.join(DATA, "nominatim_cache.json")
        self.cache = json.load(open(self.path, encoding="utf-8")) if os.path.exists(self.path) else {}
        self.last = 0

    def query(self, **params):
        key = json.dumps(params, sort_keys=True, ensure_ascii=False)
        if key not in self.cache:
            time.sleep(max(0, 1.1 - (time.time() - self.last)))  # maks 1 forespørsel/sek (bruksvilkår)
            self.last = time.time()
            q = urllib.parse.urlencode({**params, "format": "json", "limit": 1})
            try:
                res = json.loads(http_get(f"https://nominatim.openstreetmap.org/search?{q}"))
                self.cache[key] = [float(res[0]["lat"]), float(res[0]["lon"])] if res else None
                if len(self.cache) % 25 == 0:
                    self.save()  # lagre underveis
            except Exception as e:
                print(f"Nominatim feilet for {params}: {e}")
                return None
        return self.cache[key]

    def save(self):
        json.dump(self.cache, open(self.path, "w", encoding="utf-8"), ensure_ascii=False, indent=0, sort_keys=True)


# ---------- hovedløp ----------
def main():
    rows = load_lectronz()
    if os.environ.get("PS_API_KEY"):
        rows += load_prestashop()
    else:
        print("PS_API_KEY mangler – kjører kun med Lectronz-data")
    corr = load_corrections()
    gn, nom = GeoNames(), Nominatim()

    # Slå sammen til unike (land, postnummer); behold alle stedsnavn for kontroll
    groups = {}
    for cc, pc, city, src in rows:
        cc = (cc or "").strip().upper()
        key = (cc, norm_pc(cc, pc), norm_name(city))  # samme postnr med ulike steder holdes adskilt
        g = groups.setdefault(key, {"raw": pc.strip(), "cities": {}, "src": set()})
        g["cities"][city.strip()] = g["cities"].get(city.strip(), 0) + 1
        g["src"].add(src)

    points, avvik = {}, []
    for (cc, pc, _), g in sorted(groups.items()):
        city = max(g["cities"], key=g["cities"].get)  # mest brukte stedsnavn
        label = f"{cc} {g['raw']} {city}"
        pos = None

        c = corr.get((cc, pc))
        if c:
            if c.get("lat") and c.get("lon"):
                pos = (float(c["lat"]), float(c["lon"]))
            elif c.get("new_postcode"):
                hits = gn.lookup_pc(cc, norm_pc(cc, c["new_postcode"]))
                pos = (sum(h[2] for h in hits) / len(hits), sum(h[3] for h in hits) / len(hits)) if hits else None
            else:
                continue  # ignorer
            if pos:
                points[(round(pos[0], 3), round(pos[1], 3))] = 1
                continue

        if not re.fullmatch(r"[A-Z]{2}", cc):
            avvik.append((label, "ukjent land"))
            continue
        if not pc:
            avvik.append((label, "mangler postnummer"))
            continue

        hits = gn.lookup_pc(cc, pc)
        city_ok = hits and any(name_match(city, h[0]) or name_match(city, h[1]) for h in hits)
        if not city_ok:  # feil land valgt ved registrering? Prøv nabolandene
            for other in WRONG_COUNTRY_CANDIDATES:
                if other == cc:
                    continue
                h2 = gn.lookup_pc(other, norm_pc(other, g["raw"]))
                if h2 and any(name_match(city, h[0]) or name_match(city, h[1]) for h in h2):
                    pos = (sum(h[2] for h in h2) / len(h2), sum(h[3] for h in h2) / len(h2))
                    avvik.append((label, f"feil land i registreringen – plassert i {other}"))
                    break
            if pos:
                points[(round(pos[0], 3), round(pos[1], 3))] = 1
                continue
        if hits:
            pos = (sum(h[2] for h in hits) / len(hits), sum(h[3] for h in hits) / len(hits))
            if not any(name_match(city, h[0]) or name_match(city, h[1]) for h in hits):
                alt = gn.lookup_place(cc, city)
                if alt and km(pos, alt) > CITY_MOVE_KM:
                    avvik.append((label, f"postnummer hører til {hits[0][0]}, {km(pos, alt):.0f} km unna – plassert etter stedsnavn"))
                    pos = alt
        else:
            alt = gn.lookup_place(cc, city)
            if alt:
                avvik.append((label, "ukjent postnummer – plassert etter stedsnavn"))
                pos = alt
            else:
                pos = nom.query(postalcode=g["raw"], countrycodes=cc.lower()) or nom.query(city=city, countrycodes=cc.lower())
                if pos:
                    avvik.append((label, "plassert via Nominatim – kontroller"))
                else:
                    avvik.append((label, "kunne ikke plasseres"))
                    continue
        points[(round(pos[0], 3), round(pos[1], 3))] = 1

    nom.save()
    os.makedirs(SITE, exist_ok=True)
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": {}}
        for lat, lon in sorted(points)]}
    json.dump(fc, open(os.path.join(SITE, "kunder.geojson"), "w"), separators=(",", ":"))

    with open(os.path.join(ROOT, "avvik.md"), "w", encoding="utf-8") as f:
        f.write(f"Kundekart bygget {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}: "
                f"{len(groups)} unike postnr/sted, {len(points)} markører, {len(avvik)} avvik.\n\n")
        if avvik:
            f.write("Rett i `data/corrections.csv` (country,postcode,new_postcode,lat,lon,note). "
                    "Tom new_postcode og lat/lon = ignorer.\n\n| Adresse | Avvik |\n|---|---|\n")
            for lab, msg in avvik:
                f.write(f"| {lab} | {msg} |\n")
    print(f"{len(groups)} unike postnr/sted, {len(points)} markører, {len(avvik)} avvik")


if __name__ == "__main__":
    main()
