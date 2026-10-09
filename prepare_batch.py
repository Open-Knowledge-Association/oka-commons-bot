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
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import swisstopo as s
from strip_overviews import strip_overviews
from verify_strip import verify

WORK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "work")
WORKERS = 8  # images prepared in parallel (network-bound)


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
    for attempt in range(6):  # the parser occasionally answers with an empty or error response
        out = subprocess.run(["curl", "-s", "--retry", "3", "-A", s.UA, s.COMMONS_API, "--data-urlencode", "action=parse",
                              "--data-urlencode", "format=json", "--data-urlencode", "prop=categories|text",
                              "--data-urlencode", "title=" + title, "--data-urlencode", "text=" + text],
                             capture_output=True).stdout
        try:
            p = json.loads(out)["parse"]
            break
        except (ValueError, KeyError):
            if attempt == 5:
                raise
            time.sleep(min(5 * 3 ** attempt, 120))
    cats = p["categories"]
    # our own subcategories are created just before the batch uploads (create_categories.py)
    ours = s.ROOT_CATEGORY.replace(" ", "_")
    problems = [c["*"] for c in cats if ("missing" in c and not c["*"].startswith(ours))
                or re.search("rroneous|error|without|lacking|invalid", c["*"], re.I)]
    if re.search(r'class="(?:error|scribunto-error)', p["text"]["*"]):
        problems.append("template error")
    return problems


MAX_HEADING_GAP = 45  # degrees between the recorded heading and the direction of the photographed area
MIN_TIFF_BYTES = 100_000  # smaller than any real scan
MAX_ATTEMPTS = 3      # preparation attempts for an image set aside by a failed build or check


def retryable(p):
    """Set aside only because a build or check failed (not uploaded, not a duplicate, not a data problem)."""
    probs = p.get("problems") or []
    return (not p.get("uploaded") and not p.get("already_on_commons") and bool(probs)
            and all(x.startswith(("build failed", "file check failed")) for x in probs))


def heading_problems(p):
    """Flag records whose recorded camera heading does not point at the photographed area."""
    claims = {c["mainsnak"]["property"]: c for c in p["sdc"]["claims"]}
    cam, area = claims["P1259"], claims["P9149"]
    lat1, lon1 = cam["mainsnak"]["datavalue"]["value"]["latitude"], cam["mainsnak"]["datavalue"]["value"]["longitude"]
    lat2, lon2 = area["mainsnak"]["datavalue"]["value"]["latitude"], area["mainsnak"]["datavalue"]["value"]["longitude"]
    if "P7787" not in cam.get("qualifiers", {}):
        return []  # no heading recorded: nothing to check
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
        if not os.path.exists(src) or os.path.getsize(src) < MIN_TIFF_BYTES:
            data = s.http(p["tif_url"], binary=True)
            if len(data) < MIN_TIFF_BYTES:  # the server occasionally answers with an empty body
                raise ValueError(f"download returned only {len(data)} bytes")
            with open(src, "wb") as f:
                f.write(data)
        sha = hashlib.sha1(open(src, "rb").read()).hexdigest()
        p["already_on_commons"] = s.on_commons(num, sha)
        strip_overviews(src, dst)
        p["verified"] = verify(src, dst)
        p["stripped"] = dst
        p["source_sha1"] = sha
        if p["verified"]:
            os.remove(src)  # the verified stripped copy is all the upload needs
        p["problems"] = dry_run(p["title"], p["text"]) + heading_problems(p)
    except Exception as e:
        p.update(verified=False, problems=[f"file check failed: {type(e).__name__}: {e}"])
        p.setdefault("already_on_commons", False)
    return p


def main():
    out_path, count = sys.argv[1], int(sys.argv[2])
    os.makedirs(WORK, exist_ok=True)

    # Images claimed by another batch file (uploaded or pending) are never picked again. Images only ever
    # set aside for a fixable reason (a failed build or check) are retried, up to MAX_ATTEMPTS times.
    claimed, earlier, attempts = set(), [], {}
    for path in glob.glob("batch*.json") + ["test10_v3.json"]:
        if os.path.abspath(path) == os.path.abspath(out_path) or not os.path.exists(path):
            continue
        pages = json.load(open(path, encoding="utf-8"))
        earlier += pages
        for p in pages:
            if retryable(p):
                attempts[p["num"]] = attempts.get(p["num"], 0) + 1
            else:
                claimed.add(p["num"])
    retry = [n for n, k in attempts.items() if n not in claimed and k < MAX_ATTEMPTS]
    claimed.update(attempts)  # retried below or given up; not picked as new images

    # 1. set-aside images to retry, missing stereo partners of files already uploaded, then new images
    nums = retry[:count]
    print(f"{len(nums)} earlier set-aside images retried", flush=True)
    n_retry = len(nums)
    for p in earlier:
        n = p.get("partner")
        if n and n not in nums and n not in claimed and f"inventory number {n}" in p["text"] and not s.commons_title(n):
            nums.append(n)
    print(f"{len(nums) - n_retry} missing stereo partners added", flush=True)
    for num in collection_order_cached():
        if len(nums) >= count:
            break
        if num in nums or num in claimed:
            continue
        if s.commons_title(num):  # uploaded by someone else, or before batch files existed
            continue
        nums.append(num)

    with ThreadPoolExecutor(WORKERS) as pool:
        titles = dict(zip(nums, pool.map(_title_or_none, nums)))

        # 2. build, download, strip, verify, dry-run (WORKERS images at a time)
        def job(num):
            try:
                partner = str(s.metadata(num)["json"].get("STEREO_PARTNER") or "")
            except Exception:
                partner = ""
            partner_title = titles.get(partner) or (s.commons_title(partner) if partner else None)
            return prepare_one(num, partner_title)

        pages, done = [None] * len(nums), 0
        futures = {pool.submit(job, n): i for i, n in enumerate(nums)}
        for fut in as_completed(futures):
            p = pages[futures[fut]] = fut.result()
            done += 1
            print(f"{done}/{len(nums)} {p['title']} | duplicate: {p['already_on_commons']} | verified: {p['verified']}"
                  f" | problems: {p['problems']}", flush=True)
    tmp = out_path + ".tmp"
    json.dump(pages, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, out_path)

    bad = [p["num"] for p in pages if p["already_on_commons"] or not p["verified"] or p["problems"]]
    print(f"prepared {len(pages)}; not ready: {bad}")


def _title_or_none(num):
    try:
        return s.file_title(s.metadata(num)["PLACE"], num)
    except Exception:
        return None  # recorded as a problem when the image is prepared


ORDER_CACHE = "collection_order.json"


def collection_order_cached():
    """swisstopo's item order, fetched once (about 570 STAC pages) and kept locally."""
    if not os.path.exists(ORDER_CACHE):
        order = list(collection_order())
        json.dump(order, open(ORDER_CACHE, "w"))
    return json.load(open(ORDER_CACHE))


if __name__ == "__main__":
    main()
