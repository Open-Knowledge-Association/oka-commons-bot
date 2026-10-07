"""Update the description pages and structured data of files OKA bot uploaded, one at a time.

Refuses to touch a page whose file was not uploaded by OKA bot, or whose
wikitext changed since our last version (structured-data-only edits by other
bots are fine: saving wikitext keeps them). Structured data only adds the
properties a file does not have yet.

Usage: python edit_pages.py batch.json "edit summary" [--delay SECONDS]
"""
import argparse
import json
import sys
import time

from botsession import commons_site, write_structured_data
from upload import DEFERRED_PROPERTIES


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("batch")
    ap.add_argument("summary")
    ap.add_argument("--delay", type=float, default=10.0)
    args = ap.parse_args()
    pages = json.load(open(args.batch, encoding="utf-8"))

    with commons_site() as site:
        import pywikibot

        for i, p in enumerate(pages):
            if p.get("edited") and (p.get("sdc_done") or "sdc" not in p):
                continue
            page = pywikibot.FilePage(site, p["title"])
            if page.oldest_file_info.user != site.username():
                sys.exit(f"{p['title']}: file not uploaded by {site.username()}; stopping")
            if not p.get("edited"):
                if page.text.strip() == p["text"].strip():
                    pass  # already current
                elif page.text.strip() != p["previous_text"].strip():
                    sys.exit(f"{p['title']}: wikitext changed since our last version; stopping")
                else:
                    page.text = p["text"]
                    page.save(summary=args.summary, minor=False, bot=True, quiet=True)
                    if page.get(force=True).strip() != p["text"].strip():
                        sys.exit(f"{p['title']}: saved text does not match; stopping")
                p["edited"] = True
                json.dump(pages, open(args.batch, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            added = []
            if "sdc" in p:
                sdc = {**p["sdc"], "claims": [c for c in p["sdc"]["claims"]
                                              if c["mainsnak"]["property"] not in DEFERRED_PROPERTIES]}
                added = write_structured_data(site, p["title"], sdc,
                                              "Structured data from swisstopo metadata ([[Commons:Bots/Requests/OKA bot]])")
                p["sdc_done"] = True
                json.dump(pages, open(args.batch, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print(f"{i + 1}/{len(pages)} edited and verified, structured data added: {added} | {p['title']}", flush=True)
            if i < len(pages) - 1:
                time.sleep(args.delay)


if __name__ == "__main__":
    main()
