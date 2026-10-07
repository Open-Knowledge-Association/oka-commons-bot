"""Upload prepared swisstopo files to Commons as OKA bot, one at a time.

Each upload is checked (SHA-1 on Commons = local file), then its structured
data is added and read back. Stops at the first warning or error; progress is
saved in the batch file, so a rerun continues where it stopped.

Usage: python upload.py batch.json [--delay SECONDS]
"""
import argparse
import hashlib
import json
import sys
import time

from botsession import commons_site, upload_with_retry, write_structured_data

DEFERRED_PROPERTIES = set()  # properties held back for a later Wikidata linking step (none for now)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("batch")
    ap.add_argument("--delay", type=float, default=10.0)
    args = ap.parse_args()
    pages = json.load(open(args.batch, encoding="utf-8"))

    with commons_site() as site:
        import pywikibot

        print("logged in as", site.username(), "| bot flag:", "bot" in site.userinfo["groups"], flush=True)
        for i, p in enumerate(pages):
            if p.get("uploaded") and p.get("sdc_done"):
                continue
            if not p.get("uploaded"):
                if p.get("already_on_commons") or not p.get("verified") or p.get("problems"):
                    reason = "already on Commons" if p.get("already_on_commons") else (p.get("problems") or ["unverified"])
                    print(f"{p['num']}: skipped, not cleared for upload: {reason}", flush=True)
                    continue
                page = pywikibot.FilePage(site, p["title"])
                if page.exists():
                    sys.exit(f"{p['title']} already exists; stopping")
                sha = hashlib.sha1(open(p["stripped"], "rb").read()).hexdigest()
                if not upload_with_retry(site, p["title"], p["stripped"], sha, text=p["text"], ignore_warnings=False,
                                         comment=f"Upload swisstopo terrestrial image {p['num']} ([[Commons:Bots/Requests/OKA bot]])"):
                    sys.exit(f"{p['num']}: upload warning, failure or SHA-1 mismatch; stopping")
                p["uploaded"] = True
                json.dump(pages, open(args.batch, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            # depicts/location of creation are left to the Wikidata linking step (see make_manifest.py)
            sdc = {**p["sdc"], "claims": [c for c in p["sdc"]["claims"]
                                          if c["mainsnak"]["property"] not in DEFERRED_PROPERTIES]}
            added = write_structured_data(site, p["title"], sdc,
                                          "Structured data from swisstopo metadata ([[Commons:Bots/Requests/OKA bot]])")
            p["sdc_done"] = True
            json.dump(pages, open(args.batch, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print(f"{i + 1}/{len(pages)} uploaded and verified, structured data: {len(added)} added | {p['title']}",
                  flush=True)
            if i < len(pages) - 1:
                time.sleep(args.delay)


if __name__ == "__main__":
    main()
