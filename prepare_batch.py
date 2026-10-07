"""Prepare the next batch of swisstopo terrestrial images for upload.

Walks the collection in swisstopo's order, skips images already on Commons,
builds each page (stereo partners in the same batch are linked by file name),
downloads the TIFF, removes the overviews without re-encoding, verifies the
result and dry-runs the page through the Commons parser.

Usage: python prepare_batch.py OUT.json COUNT
"""
import glob
import hashlib
import json
import math
import os
import re
import subprocess
import sys

import swisstopo as s
from strip_overviews import strip_overviews
from verify_strip import verify

WORK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "work")


def collection_order():
    url = f"https://data.geo.admin.ch/api/stac/v0.9/collections/{s.COLLECTION}/items"
    params = {"limit": 100}
    while url:
        d = json.loads(s.http(url, params))
        params = None
        for f in d["features"]:
            yield str(int(f["id"].rsplit("_", 1)[1].replace("-", "")))
        url = next((l["href"] for l in d["links"] if l["rel"] == "next"), None)


def dry_run(title, text):
    out = subprocess.run(["curl", "-s", "--retry", "3", "-A", s.UA, s.COMMONS_API, "--data-urlencode", "action=parse",
                          "--data-urlencode", "format=json", "--data-urlencode", "prop=categories|text",
                          "--data-urlencode", "title=" + title, "--data-urlencode", "text=" + text],
                         capture_output=True).stdout
    p = json.loads(out)["parse"]
    cats = p["categories"]
    problems = [c["*"] for c in cats if "missing" in c or re.search("rroneous|error|without|lacking|invalid", c["*"], re.I)]
    if re.search(r'class="(?:error|scribunto-error)', p["text"]["*"]):
        problems.append("template error")
    return problems


MAX_HEADING_GAP = 45  # degrees between the recorded heading and the direction of the photographed area


def heading_problems(p):
    """Flag records whose recorded camera heading does not point at the photographed area."""
    claims = {c["mainsnak"]["property"]: c for c in p["sdc"]["claims"]}
    cam, area = claims["P1259"], claims["P9149"]
    lat1, lon1 = cam["mainsnak"]["datavalue"]["value"]["latitude"], cam["mainsnak"]["datavalue"]["value"]["longitude"]
    lat2, lon2 = area["mainsnak"]["datavalue"]["value"]["latitude"], area["mainsnak"]["datavalue"]["value"]["longitude"]
    heading = float(cam["qualifiers"]["P7787"][0]["datavalue"]["value"]["amount"])
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    bearing = math.degrees(math.atan2(math.sin(dl) * math.cos(p2),
                                      math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl))) % 360
    gap = abs((bearing - heading + 180) % 360 - 180)
    return [f"heading {heading:.0f} deg but photographed area lies at {bearing:.0f} deg"] if gap > MAX_HEADING_GAP else []


def prepare_one(num, partner_title):
    """Build, download, strip, verify and dry-run one image; problems are recorded, never raised."""
    try:
        p = s.build(num, collection_category="Terrestrial photographs by swisstopo", partner_title=partner_title)
    except Exception as e:  # odd record (missing date, outside Switzerland, ...): set aside for review
        return dict(num=num, title=None, text="", partner="", already_on_commons=False, verified=False,
                    problems=[f"build failed: {type(e).__name__}: {e}"])
    src, dst = os.path.join(WORK, f"{num}.tif"), os.path.join(WORK, f"{num}.stripped.tif")
    try:
        if not os.path.exists(src):
            with open(src, "wb") as f:
                f.write(s.http(p["tif_url"], binary=True))
        sha = hashlib.sha1(open(src, "rb").read()).hexdigest()
        p["already_on_commons"] = s.on_commons(num, sha)
        strip_overviews(src, dst)
        p["verified"] = verify(src, dst)
        p["stripped"] = dst
        p["problems"] = dry_run(p["title"], p["text"]) + heading_problems(p)
    except Exception as e:
        p.update(verified=False, problems=[f"file check failed: {type(e).__name__}: {e}"])
        p.setdefault("already_on_commons", False)
    return p


def main():
    out_path, count = sys.argv[1], int(sys.argv[2])
    os.makedirs(WORK, exist_ok=True)

    # 1. missing stereo partners of files already uploaded, then the next images not yet on Commons
    nums = []
    for path in glob.glob("batch*.json") + ["test10_v3.json"]:
        if os.path.abspath(path) == os.path.abspath(out_path) or not os.path.exists(path):
            continue
        for p in json.load(open(path, encoding="utf-8")):
            n = p.get("partner")
            if n and n not in nums and f"inventory number {n}" in p["text"] and not s.commons_title(n):
                nums.append(n)
    print(f"{len(nums)} missing stereo partners added first", flush=True)
    for num in collection_order():
        if num in nums:
            continue
        if s.commons_title(num):
            continue
        nums.append(num)
        if len(nums) == count:
            break
    titles = {}
    for n in nums:
        try:
            titles[n] = s.file_title(s.metadata(n)["PLACE"], n)
        except Exception:
            pass  # recorded as a problem when the image is prepared

    # 2. build, download, strip, verify, dry-run
    pages = []
    for i, num in enumerate(nums):
        try:
            partner = str(s.metadata(num)["json"].get("STEREO_PARTNER") or "")
        except Exception:
            partner = ""
        partner_title = titles.get(partner) or (s.commons_title(partner) if partner else None)
        p = prepare_one(num, partner_title)
        pages.append(p)
        print(f"{i + 1}/{len(nums)} {p['title']} | duplicate: {p['already_on_commons']} | verified: {p['verified']}"
              f" | problems: {p['problems']}", flush=True)
        json.dump(pages, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    bad = [p["num"] for p in pages if p["already_on_commons"] or not p["verified"] or p["problems"]]
    print(f"prepared {len(pages)}; not ready: {bad}")


if __name__ == "__main__":
    main()
