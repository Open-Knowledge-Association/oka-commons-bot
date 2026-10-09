"""Check that Commons renders a preview (640 px thumbnail) for every uploaded file of a batch.

Usage: python check_thumbs.py batch.json      Prints one line per failure and a summary.
"""
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import swisstopo as s


def thumb_ok(title):
    """True if Commons renders a preview at 640 px, or (a one-off failure of that size) at 320 and 1280 px."""
    if _renders(title, 640):
        return True
    if _renders(title, 320) and _renders(title, 1280):
        print(f"640 px preview failed but 320 and 1280 px render: {title}", flush=True)
        return True
    return False


def _renders(title, width):
    r = json.loads(s.http(s.COMMONS_API, dict(action="query", titles=title, prop="imageinfo", iiprop="url",
                                               iiurlwidth=width, format="json")))
    info = list(r["query"]["pages"].values())[0].get("imageinfo", [{}])[0]
    if "thumburl" not in info:
        return False
    for _ in range(3):  # thumbnails render on first request; give the renderer a moment
        code, ctype, size = s.status(info["thumburl"])
        if code == 200 and ctype.startswith("image/") and size > 5000:
            return True
        time.sleep(5)
    return False


def main():
    pages = [p for p in json.load(open(sys.argv[1], encoding="utf-8")) if p.get("uploaded")]
    with ThreadPoolExecutor(8) as pool:
        ok = list(pool.map(thumb_ok, [p["title"] for p in pages]))
    failed = [p["title"] for p, good in zip(pages, ok) if not good]
    for t in failed:
        print("no preview:", t)
    print(f"previews: {len(pages) - len(failed)}/{len(pages)} render")


if __name__ == "__main__":
    main()
