"""List every swisstopo image OKA bot has uploaded, with what the Wikidata linking step needs.

Reads the bot's uploads from Commons and the swisstopo metadata of each image,
and writes a CSV: Commons title and MediaInfo ID, inventory number, date, survey
area and station, camera position and heading, the image footprint (bbox and
centre), municipality of the footprint centre, and the stereo partner.

Usage: python make_manifest.py uploads.csv
"""
import csv
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor

import swisstopo as s


def bot_uploads():
    params = dict(action="query", list="allimages", aiuser="OKA bot", aisort="timestamp", ailimit=500,
                  aiprop="timestamp|sha1", format="json")
    while True:
        r = json.loads(s.http(s.COMMONS_API, params))
        yield from r["query"]["allimages"]
        if "continue" not in r:
            return
        params.update(r["continue"])


def main():
    out = sys.argv[1]
    known = {}
    if os.path.exists(out):  # incremental: rows already in the list are kept, only new uploads are looked up
        known = {r["commons_title"]: r for r in csv.DictReader(open(out, encoding="utf-8"))}
    images = [i for i in bot_uploads() if re.search(r"Swisstopo (\d+)\.tif$", i["title"])]
    new = [i for i in images if i["title"] not in known]
    ids = {}
    for k in range(0, len(new), 50):
        chunk = [i["title"] for i in new[k:k + 50]]
        r = json.loads(s.http(s.COMMONS_API, dict(action="query", titles="|".join(chunk), format="json")))
        ids.update({p["title"]: f"M{p['pageid']}" for p in r["query"]["pages"].values()})

    def row(img):
        num = re.search(r"Swisstopo (\d+)\.tif$", img["title"]).group(1)
        m = s.metadata(num)
        j, b = m["json"], m["bbox"]
        area, _, station = (p.strip() for p in m["PLACE"].partition("|"))
        cam_lat, cam_lon = s.lv95_to_wgs84(m["E"], m["N"])
        c_lon, c_lat = round((b[0] + b[2]) / 2, 5), round((b[1] + b[3]) / 2, 5)
        canton, muni, bfs = s.canton_and_municipality(c_lon, c_lat)
        return dict(
            commons_title=img["title"], mediainfo_id=ids.get(img["title"], ""), uploaded=img["timestamp"],
            inventory_number=num, date=j.get("DATE_FULL", ""), survey_area=area, station=station,
            camera_lat=cam_lat, camera_lon=cam_lon, camera_altitude_m=m["Z"], heading_deg=m["KAPPA"],
            footprint_west=b[0], footprint_south=b[1], footprint_east=b[2], footprint_north=b[3],
            footprint_centre_lat=c_lat, footprint_centre_lon=c_lon, municipality=muni,
            municipality_qid=s.municipality_wikidata(bfs)[0] or "", canton=canton,
            stereo_partner=j.get("STEREO_PARTNER") or "", smapshot_id=s.layer_attributes(num).get("smapshot_id", ""),
            swisstopo_url=f"https://data.geo.admin.ch/browser/index.html#/collections/{s.COLLECTION}/items/{s.fid(num)}")

    with ThreadPoolExecutor(8) as pool:
        rows = dict(zip((i["title"] for i in new), pool.map(row, new)))
    tmp = out + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        for img in images:  # Commons upload order
            w.writerow(known.get(img["title"]) or rows[img["title"]])
    os.replace(tmp, out)
    print(f"{len(images)} files listed ({len(new)} new)", flush=True)


FIELDS = ["commons_title", "mediainfo_id", "uploaded", "inventory_number", "date", "survey_area", "station",
          "camera_lat", "camera_lon", "camera_altitude_m", "heading_deg", "footprint_west", "footprint_south",
          "footprint_east", "footprint_north", "footprint_centre_lat", "footprint_centre_lon",
          "municipality", "municipality_qid", "canton", "stereo_partner", "smapshot_id", "swisstopo_url"]


if __name__ == "__main__":
    main()
