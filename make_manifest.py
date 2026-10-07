"""List every swisstopo image OKA bot has uploaded, with what the Wikidata linking step needs.

Reads the bot's uploads from Commons and the swisstopo metadata of each image,
and writes a CSV: Commons title and MediaInfo ID, inventory number, date, survey
area and station, camera position and heading, the image footprint (bbox and
centre), municipality of the footprint centre, and the stereo partner.

Usage: python make_manifest.py uploads.csv
"""
import csv
import json
import re
import sys

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
    images = [i for i in bot_uploads() if re.search(r"Swisstopo (\d+)\.tif$", i["title"])]
    ids = {}
    for k in range(0, len(images), 50):
        chunk = [i["title"] for i in images[k:k + 50]]
        r = json.loads(s.http(s.COMMONS_API, dict(action="query", titles="|".join(chunk), format="json")))
        ids.update({p["title"]: f"M{p['pageid']}" for p in r["query"]["pages"].values()})
    fields = ["commons_title", "mediainfo_id", "uploaded", "inventory_number", "date", "survey_area", "station",
              "camera_lat", "camera_lon", "camera_altitude_m", "heading_deg", "footprint_west", "footprint_south",
              "footprint_east", "footprint_north", "footprint_centre_lat", "footprint_centre_lon",
              "municipality", "municipality_qid", "canton", "stereo_partner", "smapshot_id", "swisstopo_url"]
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fields)
        w.writeheader()
        for n, img in enumerate(images, 1):
            num = re.search(r"Swisstopo (\d+)\.tif$", img["title"]).group(1)
            m = s.metadata(num)
            j, b = m["json"], m["bbox"]
            area, _, station = (p.strip() for p in m["PLACE"].partition("|"))
            cam_lat, cam_lon = s.lv95_to_wgs84(m["E"], m["N"])
            c_lon, c_lat = round((b[0] + b[2]) / 2, 5), round((b[1] + b[3]) / 2, 5)
            canton, muni, bfs = s.canton_and_municipality(c_lon, c_lat)
            w.writerow(dict(
                commons_title=img["title"], mediainfo_id=ids.get(img["title"], ""), uploaded=img["timestamp"],
                inventory_number=num, date=j.get("DATE_FULL", ""), survey_area=area, station=station,
                camera_lat=cam_lat, camera_lon=cam_lon, camera_altitude_m=m["Z"], heading_deg=m["KAPPA"],
                footprint_west=b[0], footprint_south=b[1], footprint_east=b[2], footprint_north=b[3],
                footprint_centre_lat=c_lat, footprint_centre_lon=c_lon, municipality=muni,
                municipality_qid=s.municipality_wikidata(bfs)[0] or "", canton=canton,
                stereo_partner=j.get("STEREO_PARTNER") or "", smapshot_id=s.layer_attributes(num).get("smapshot_id", ""),
                swisstopo_url=f"https://data.geo.admin.ch/browser/index.html#/collections/{s.COLLECTION}/items/{s.fid(num)}"))
            print(f"{n}/{len(images)} {img['title']}", flush=True)


if __name__ == "__main__":
    main()
