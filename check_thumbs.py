"""Check that Commons renders a preview (640 px thumbnail) for every uploaded file of a batch.

Usage: python check_thumbs.py batch.json      Prints one line per failure and a summary.
"""
import json
import subprocess
import sys
import time

import swisstopo as s


def thumb_ok(title):
    r = json.loads(s.http(s.COMMONS_API, dict(action="query", titles=title, prop="imageinfo", iiprop="url",
                                               iiurlwidth=640, format="json")))
    info = list(r["query"]["pages"].values())[0].get("imageinfo", [{}])[0]
    if "thumburl" not in info:
        return False
    for _ in range(3):  # thumbnails render on first request; give the renderer a moment
        out = subprocess.run(["curl", "-s", "-o", "NUL", "-w", "%{http_code} %{content_type} %{size_download}",
                              "-A", s.UA, info["thumburl"]], capture_output=True, text=True).stdout.split()
        if len(out) == 3 and out[0] == "200" and out[1].startswith("image/") and int(out[2]) > 5000:
            return True
        time.sleep(5)
    return False


def main():
    pages = [p for p in json.load(open(sys.argv[1], encoding="utf-8")) if p.get("uploaded")]
    failed = [p["title"] for p in pages if not thumb_ok(p["title"])]
    for t in failed:
        print("no preview:", t)
    print(f"previews: {len(pages) - len(failed)}/{len(pages)} render")


if __name__ == "__main__":
    main()
