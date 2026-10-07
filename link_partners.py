"""Replace stereo-partner inventory numbers with file links once the partner is on Commons.

Looks at every batch file (batch*.json, test10_v3.json, mateusz20.json) for pages
whose "Stereo pair" field still names the partner by number, and edits the live
page as OKA bot when the partner file now exists. Only that one string changes.

Usage: python link_partners.py
"""
import glob
import json
import time

import swisstopo as s
from botsession import commons_site

STATE = "partners_linked.json"


def candidates():
    seen = set()
    for path in sorted(glob.glob("batch*.json")) + ["test10_v3.json", "mateusz20.json"]:
        try:
            pages = json.load(open(path, encoding="utf-8"))
        except FileNotFoundError:
            continue
        for p in pages:
            if p.get("partner") and f"partner: swisstopo inventory number {p['partner']}" in p["text"] \
                    and p["title"] not in seen and (p.get("uploaded") or p.get("edited")):
                seen.add(p["title"])
                yield p["title"], p["partner"]


def main():
    try:
        done = set(json.load(open(STATE, encoding="utf-8")))
    except FileNotFoundError:
        done = set()
    todo = [(t, n, s.commons_title(n)) for t, n in candidates() if t not in done]
    todo = [x for x in todo if x[2]]
    print(f"{len(todo)} pages can link their partner now", flush=True)
    if not todo:
        return
    with commons_site() as site:
        import pywikibot

        for title, num, partner_title in todo:
            page = pywikibot.FilePage(site, title)
            old = f"partner: swisstopo inventory number {num}"
            if old not in page.text:
                done.add(title)
                continue
            page.text = page.text.replace(old, f"partner: [[:{partner_title}]]", 1)
            page.save(summary="Link stereo partner file", minor=True, bot=True, quiet=True)
            if f"[[:{partner_title}]]" not in page.get(force=True):
                raise SystemExit(f"{title}: partner link not saved; stopping")
            done.add(title)
            json.dump(sorted(done), open(STATE, "w", encoding="utf-8"), indent=0)
            print(f"linked {title} -> {partner_title}", flush=True)
            time.sleep(6)


if __name__ == "__main__":
    main()
