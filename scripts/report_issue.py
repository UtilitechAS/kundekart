#!/usr/bin/env python3
"""Legger avvik.md inn i en åpen GitHub-sak ("Kundekart: avvik"), tildelt ASSIGNEE.
Ny kommentar (=> e-post) bare når det har kommet nye avvik siden forrige kjøring.
Ingen avvik => saken lukkes."""
import json, os, re, urllib.request

REPO, TOKEN = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"]
ASSIGNEE = os.environ.get("ASSIGNEE", "ArnieO")
TITLE = "Kundekart: avvik"
API = f"https://api.github.com/repos/{REPO}"


def gh(method, url, body=None):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read() or "null")


def rows(text):
    return {l for l in (text or "").splitlines() if l.startswith("| ") and not l.startswith("| Adresse")}


MAX_ROWS = 400  # GitHub-saker tåler maks 65 536 tegn

report = open("avvik.md", encoding="utf-8").read()
new = rows(report)
if len(new) > MAX_ROWS:
    keep, n = [], 0
    for line in report.splitlines():
        if line.startswith("| ") and not line.startswith("| Adresse"):
            n += 1
            if n > MAX_ROWS:
                continue
        keep.append(line)
    report = "\n".join(keep) + f"\n\n… og {len(new) - MAX_ROWS} til. Se hele lista i siste kjøring under Actions.\n"
    new = rows(report)
issue = next((i for i in gh("GET", f"{API}/issues?state=open&per_page=100")
              if i["title"] == TITLE and "pull_request" not in i), None)

if not new:
    if issue:
        gh("PATCH", f"{API}/issues/{issue['number']}", {"body": report, "state": "closed"})
    print("Ingen avvik")
elif not issue:
    gh("POST", f"{API}/issues", {"title": TITLE, "body": report, "assignees": [ASSIGNEE]})
    print(f"Ny sak opprettet, {len(new)} avvik")
else:
    added = new - rows(issue["body"])
    gh("PATCH", f"{API}/issues/{issue['number']}", {"body": report})
    if added:
        gh("POST", f"{API}/issues/{issue['number']}/comments",
           {"body": f"@{ASSIGNEE} {len(added)} nye avvik:\n\n| Adresse | Avvik |\n|---|---|\n" + "\n".join(sorted(added)[:MAX_ROWS])})
    print(f"Sak oppdatert, {len(added)} nye avvik")
