"""Bring Mateusztok's 20 swisstopo uploads (164339-164358) up to the OKA bot format.

For each file: upload a new version without the overview pages (lossless, verified),
replace the description page with the current builder's output, and add the
structured data. Agreed with Mateusz via jz (2026-10-07).

Usage: python mateusz.py
"""
import hashlib
import json
import os
import time

import swisstopo as s
from botsession import commons_site, upload_with_retry, write_structured_data
from prepare_batch import WORK, prepare_one

STATE = "mateusz20.json"


def prepare():
    nums = [str(n) for n in range(164339, 164359)]
    pages = []
    for num in nums:
        partner = str(s.metadata(num)["json"].get("STEREO_PARTNER") or "")
        p = prepare_one(num, s.commons_title(partner) if partner else None)
        if p.get("title") != s.commons_title(num):
            p["problems"] = p.get("problems", []) + ["title differs from the existing file"]
        src = os.path.join(WORK, f"{num}.tif")
        if os.path.exists(src):
            p["source_sha1"] = hashlib.sha1(open(src, "rb").read()).hexdigest()
        pages.append(p)
    json.dump(pages, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return pages


def main():
    pages = json.load(open(STATE, encoding="utf-8")) if os.path.exists(STATE) else prepare()
    bad = [p["num"] for p in pages if not p["verified"] or p["problems"]]
    if bad:
        raise SystemExit(f"not ready: {bad}")
    with commons_site() as site:
        import pywikibot

        for p in pages:
            page = pywikibot.FilePage(site, p["title"])
            if not p.get("reuploaded"):
                sha = hashlib.sha1(open(p["stripped"], "rb").read()).hexdigest()
                current = page.latest_file_info.sha1
                if current == sha:
                    pass  # an interrupted earlier run already stored the new version
                elif current != p["source_sha1"]:
                    raise SystemExit(f"{p['title']}: current file is not the swisstopo original; stopping")
                elif not upload_with_retry(site, p["title"], p["stripped"], sha, ignore_warnings=True,
                                           comment="New version: swisstopo GeoTIFF without its reduced-resolution "
                                                   "overview pages (full-resolution tiles copied byte for byte, no re-encoding)"):
                    raise SystemExit(f"{p['title']}: new version failed or SHA-1 mismatch; stopping")
                p["reuploaded"] = True
            if not p.get("edited"):
                page = pywikibot.FilePage(site, p["title"])
                page.text = p["text"]
                page.save(summary="Update description to the OKA bot swisstopo format (localized fields, "
                                  "categories, stereo pair, expert sheet, Smapshot)", bot=True, quiet=True)
                if page.get(force=True).strip() != p["text"].strip():
                    raise SystemExit(f"{p['title']}: saved text mismatch; stopping")
                p["edited"] = True
            if not p.get("sdc_done"):
                write_structured_data(site, p["title"], p["sdc"], "Structured data from swisstopo metadata")
                p["sdc_done"] = True
            json.dump(pages, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print(f"done {p['title']}", flush=True)
            time.sleep(10)


if __name__ == "__main__":
    main()
