# Kundekart for amsleser.no

Kart med én markør per postnummer der vi har kunder. Bygges automatisk hver mandag
(og ved endringer i `data/`, `site/` eller `scripts/`) og publiseres til
https://utilitechas.github.io/kundekart/

## Kilder
- PrestaShop Webservice (leveringsadresser på alle ordrer) – nøkkel i secret `PS_API_KEY`, adresse i variabel `PS_API_URL`
- `data/lectronz.csv` – statisk liste fra Lectronz (land, postnummer, sted)

## Koordinater
GeoNames postnummerregister. Postnummer som ikke finnes, eller som ikke passer med stedsnavnet,
plasseres etter stedsnavn eller via OpenStreetMap Nominatim, og havner på avvikslista.

## Avvik
Hver kjøring oppdaterer saken **Kundekart: avvik** under Issues. Du får e-post når nye avvik dukker opp.
Rett ved å legge til en linje i `data/corrections.csv`:

| Kolonne | Bruk |
|---|---|
| country, postcode | Som i avvikslista (ISO-landkode, f.eks. NO, SE, DK) |
| new_postcode | Riktig postnummer |
| lat, lon | Eller faste koordinater (går foran new_postcode) |
| note | Fritekst |

Linje med tomme new_postcode og lat/lon = postnummeret ignoreres.

## Manuell kjøring
Actions → Bygg kundekart → Run workflow.

## Innbygging i nettbutikken
```html
<iframe src="https://utilitechas.github.io/kundekart/" width="100%" height="600" style="border:0" loading="lazy"></iframe>
```
