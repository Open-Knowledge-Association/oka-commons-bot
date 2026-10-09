"""Fetch swisstopo terrestrial images and build their Commons file pages."""
import datetime
import json
import re
import subprocess
import threading
import time
import urllib.parse

UA = "OKA-bot/0.1 (https://commons.wikimedia.org/wiki/User:OKA_bot; jz@oka.wiki)"
COLLECTION = "ch.swisstopo.lubis-terrestrische_aufnahmen"
DATA = f"https://data.geo.admin.ch/{COLLECTION}"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"

# canton code -> (English name as used in Commons categories, German name)
CANTONS = {
    "AG": ("Aargau", "Aargau"), "AI": ("Appenzell Innerrhoden", "Appenzell Innerrhoden"),
    "AR": ("Appenzell Ausserrhoden", "Appenzell Ausserrhoden"), "BE": ("Bern", "Bern"),
    "BL": ("Basel-Landschaft", "Basel-Landschaft"), "BS": ("Basel-Stadt", "Basel-Stadt"),
    "FR": ("Fribourg", "Freiburg"), "GE": ("Geneva", "Genf"), "GL": ("Glarus", "Glarus"),
    "GR": ("Graubünden", "Graubünden"), "JU": ("Jura", "Jura"), "LU": ("Lucerne", "Luzern"),
    "NE": ("Neuchâtel", "Neuenburg"), "NW": ("Nidwalden", "Nidwalden"), "OW": ("Obwalden", "Obwalden"),
    "SG": ("St. Gallen", "St. Gallen"), "SH": ("Schaffhausen", "Schaffhausen"), "SO": ("Solothurn", "Solothurn"),
    "SZ": ("Schwyz", "Schwyz"), "TG": ("Thurgau", "Thurgau"), "TI": ("Ticino", "Tessin"), "UR": ("Uri", "Uri"),
    "VD": ("Vaud", "Waadt"), "VS": ("Valais", "Wallis"), "ZG": ("Zug", "Zug"), "ZH": ("Zürich", "Zürich"),
}


def http(url, params=None, binary=False):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    for attempt in range(4):  # curl retries transient HTTP errors itself; this also covers failed connections
        try:
            out = subprocess.run(["curl", "-sfL", "--retry", "3", "-A", UA, url], capture_output=True,
                                 check=True).stdout
            break
        except subprocess.CalledProcessError:
            if attempt == 3:
                raise
            time.sleep(5 * 3 ** attempt)
    if binary:
        return out
    try:
        return out.decode("utf-8-sig")
    except UnicodeDecodeError:
        return out.decode("latin-1")


def fid(num):
    return f"lubis-terrestrische_aufnahmen_{num[:-6].zfill(3)}-{num[-6:-3]}-{num[-3:]}"


def metadata(num):
    rows = http(f"{DATA}/{fid(num)}/{fid(num)}.csv").splitlines()
    meta = dict(zip(rows[0].split(";"), rows[1].split(";")))
    item = json.loads(http(f"https://data.geo.admin.ch/api/stac/v0.9/collections/{COLLECTION}/items/{fid(num)}"))
    meta["bbox"] = item["bbox"]
    meta["json"] = json.loads(http(f"{DATA}/{fid(num)}/{fid(num)}.json"))
    return meta


def lv95_to_wgs84(e, n):
    r = json.loads(http("https://geodesy.geo.admin.ch/reframe/lv95towgs84", dict(easting=e, northing=n, format="json")))
    return round(float(r["northing"]), 5), round(float(r["easting"]), 5)


def boundary(lon, lat, layer):
    r = json.loads(http("https://api3.geo.admin.ch/rest/services/api/MapServer/identify", dict(
        geometry=f"{lon},{lat}", geometryType="esriGeometryPoint", layers=f"all:{layer}", sr=4326,
        tolerance=0, returnGeometry="false", imageDisplay="100,100,96", mapExtent="0,0,1,1")))
    return [x["attributes"] for x in r["results"]]


# neighbouring countries: code -> (English name as used in Commons categories, German name)
COUNTRIES = {"ch": ("Switzerland", "Schweiz"), "it": ("Italy", "Italien"), "fr": ("France", "Frankreich"),
             "de": ("Germany", "Deutschland"), "at": ("Austria", "Österreich"), "li": ("Liechtenstein", "Liechtenstein")}
_NOMINATIM_LOCK = threading.Lock()
_NOMINATIM_LAST = [0.0]


def place_of(lon, lat):
    """Municipality, canton or country, Wikidata item and Commons category for a point.

    Inside Switzerland the swisstopo boundaries are used; outside (photos looking across the border)
    OpenStreetMap's administrative boundaries via Nominatim (at most one request per second).
    """
    cantons = boundary(lon, lat, "ch.swisstopo.swissboundaries3d-kanton-flaeche.fill")
    if cantons:
        current = [a for a in boundary(lon, lat, "ch.swisstopo.swissboundaries3d-gemeinde-flaeche.fill")
                   if a.get("is_current_jahr")]
        if not current:
            raise ValueError(f"no current municipality at {lat},{lon}")
        qid, cat = municipality_wikidata(str(current[0]["gde_nr"]))
        canton_en, canton_de = CANTONS[cantons[0]["ak"]]
        return dict(country="ch", muni=current[0]["gemname"], qid=qid, cat=cat, canton=canton_en, canton_de=canton_de,
                    code=cantons[0]["ak"])
    with _NOMINATIM_LOCK:
        wait = _NOMINATIM_LAST[0] + 1.1 - time.time()
        if wait > 0:
            time.sleep(wait)
        r = json.loads(http("https://nominatim.openstreetmap.org/reverse", dict(
            lat=lat, lon=lon, zoom=10, format="jsonv2", extratags=1, **{"accept-language": "en"})))
        _NOMINATIM_LAST[0] = time.time()
    code = r.get("address", {}).get("country_code")
    if code not in COUNTRIES or code == "ch":
        raise ValueError(f"place at {lat},{lon} not resolved (country {code})")
    qid = r.get("extratags", {}).get("wikidata")
    return dict(country=code, muni=r.get("name"), qid=qid, cat=commons_category(qid) if qid else None,
                canton=None, canton_de=None, code=None)


def commons_category(qid):
    """Commons category (P373) of a Wikidata item."""
    r = json.loads(http("https://www.wikidata.org/w/api.php", dict(action="wbgetclaims", entity=qid, property="P373",
                                                                    format="json")))
    claims = r.get("claims", {}).get("P373", [])
    return claims[0]["mainsnak"]["datavalue"]["value"] if claims else None


_WD = {}


def municipality_wikidata(bfs):
    """Wikidata item and Commons category (P373) of a Swiss municipality, by BFS number (P771)."""
    if bfs not in _WD:
        q = f'SELECT ?m ?cat WHERE {{ ?m wdt:P771 "{bfs}" . OPTIONAL {{ ?m wdt:P373 ?cat }} }}'
        r = json.loads(http("https://query.wikidata.org/sparql", dict(query=q, format="json")))["results"]["bindings"]
        _WD[bfs] = (r[0]["m"]["value"].rsplit("/", 1)[1], r[0].get("cat", {}).get("value")) if len(r) == 1 else (None, None)
    return _WD[bfs]


def commons_query(params, attempts=6):
    """Commons API query that retries rate limits and transient errors (5 s, 15 s, 45 s, 2 min ...)."""
    for attempt in range(attempts):
        try:
            r = json.loads(http(COMMONS_API, {**params, "maxlag": 5}))
            if "query" in r or ("error" not in r and "batchcomplete" in r):
                return r
            reason = r.get("error", {}).get("code", "no query result")
        except (subprocess.CalledProcessError, ValueError) as e:
            reason = type(e).__name__
        if attempt == attempts - 1:
            raise RuntimeError(f"Commons query failed after {attempts} attempts ({reason}): {params}")
        wait = min(5 * 3 ** attempt, 120)
        print(f"Commons query: {reason}; retrying in {wait} s", flush=True)
        time.sleep(wait)


def existing_categories(names):
    r = commons_query(dict(action="query", titles="|".join("Category:" + n for n in names), format="json"))
    return {p["title"][9:] for p in r["query"]["pages"].values() if "missing" not in p}


def on_commons(num, sha1):
    """True if the source file or any file mentioning this inventory number is already on Commons."""
    r = commons_query(dict(action="query", list="allimages", aisha1=sha1, format="json"))
    if r["query"]["allimages"]:
        return True
    r = commons_query(dict(action="query", list="search", srnamespace=6, format="json",
                           srsearch=f'"Swisstopo {num}" OR insource:"{fid(num)}"'))
    return bool(r["query"]["search"])


def file_title(place, num):
    """Commons file name; characters not allowed in page titles are dropped (only from the name)."""
    clean = lambda x: re.sub(r"\s+", " ", re.sub(r"[\[\]#<>|{}]", "", x)).strip()
    area, _, station = (clean(p) for p in place.partition("|"))
    return f"File:{area} - {station} - Swisstopo {num}.tif" if station else f"File:{area} - Swisstopo {num}.tif"


def parse_date(text):
    """swisstopo DATE_STRING -> Commons date wikitext and Wikidata time values.

    Handles day (11.08.1915), year only (1924) and day ranges (20.03.-13.04.1922, 05.-07.08.1919).
    Anything else raises ValueError, so the image is set aside instead of guessed.
    """
    text = text.strip()
    iso = lambda d, mth, y: f"{y}-{mth}-{d}"
    if m := re.fullmatch(r"(\d{2})\.(\d{2})\.(\d{4})", text):
        day = iso(*m.groups())
        datetime.date.fromisoformat(day)
        return dict(wikitext=day, time=day, precision=11, year=m[3])
    if m := re.fullmatch(r"(\d{4})", text):
        return dict(wikitext=text, time=f"{text}-00-00", precision=9, year=text)
    if m := re.fullmatch(r"(\d{2})\.(?:(\d{2})\.)?-(\d{2})\.(\d{2})\.(\d{4})", text):
        d1, m1, d2, m2, y = m.groups()
        start, end = iso(d1, m1 or m2, y), iso(d2, m2, y)
        datetime.date.fromisoformat(start), datetime.date.fromisoformat(end)
        return dict(wikitext=f"{{{{other date|between|{start}|{end}}}}}", time=f"{y}-{m2 if m1 in (None, m2) else '00'}-00",
                    precision=10 if m1 in (None, m2) else 9, year=y, earliest=start, latest=end)
    raise ValueError(f"unrecognised date format {text!r}")


def commons_title(num):
    """Title of the Commons file for this inventory number, if one exists."""
    r = commons_query(dict(action="query", list="search", srnamespace=6, format="json",
                           srsearch=f'intitle:"Swisstopo {num}"'))
    hits = [h["title"] for h in r["query"]["search"] if h["title"].endswith(f"Swisstopo {num}.tif")]
    return hits[0] if len(hits) == 1 else None


def layer_attributes(num):
    """Attributes of the image in the geo.admin.ch layer (Smapshot ID, expert sheet)."""
    r = json.loads(http("https://api3.geo.admin.ch/rest/services/api/MapServer/find", dict(
        layer=COLLECTION, searchText=num, searchField="inventarnummer", returnGeometry="false", contains="false")))
    hits = [x["attributes"] for x in r["results"] if str(x["attributes"]["inventarnummer"]) == num]
    return hits[0] if hits else {}


ROOT_CATEGORY = "Terrestrial photographs by swisstopo"


def _clean(name):
    return re.sub(r"\s+", " ", re.sub(r"[\[\]#<>|{}]", "", name)).strip()


def category_plan(area, muni, muni_cat, year, country_en="Switzerland"):
    """The three subcategories a file goes into, with what each category page needs.

    Files sit only in these subcategories (plus hidden tracking categories), so general categories such
    as "1926 photographs of Switzerland" or a municipality get one subcategory link instead of
    thousands of files (suggestion on Commons, 2026-10-09).
    """
    place = muni_cat or muni
    where = muni if country_en == "Switzerland" else f"{muni}, {country_en}"
    return [
        dict(name=f"{ROOT_CATEGORY} - {_clean(area)}", sortkey=_clean(area),
             parents=[ROOT_CATEGORY],
             en=f"Terrestrial survey photographs by swisstopo from the survey area {area}.",
             de=f"Terrestrische Aufnahmen von swisstopo aus dem Aufnahmegebiet {area}."),
        dict(name=f"{ROOT_CATEGORY} of {_clean(place)}", sortkey=_clean(place),
             parents=[f"{ROOT_CATEGORY} by municipality"] + ([muni_cat] if muni_cat else []),
             en=f"Terrestrial survey photographs by swisstopo showing {where}.",
             de=f"Terrestrische Aufnahmen von swisstopo, die {where} zeigen."),
        dict(name=f"{ROOT_CATEGORY} in {year}", sortkey=year,
             parents=[f"{ROOT_CATEGORY} by year", f"{year} photographs of Switzerland"],
             en=f"Terrestrial survey photographs by swisstopo taken in {year}.",
             de=f"Terrestrische Aufnahmen von swisstopo aus dem Jahr {year}."),
    ]


def build(num, collection_category=ROOT_CATEGORY, partner_title=None):
    m = metadata(num)
    j = m["json"]
    layer = layer_attributes(num)
    area, _, station = (p.strip() for p in m["PLACE"].partition("|"))
    title = file_title(m["PLACE"], num)
    when = parse_date(m["DATE_STRING"])
    year = when["year"]
    b = m["bbox"]
    obj_lon, obj_lat = round((b[0] + b[2]) / 2, 5), round((b[1] + b[3]) / 2, 5)
    cam_lat, cam_lon = lv95_to_wgs84(m["E"], m["N"])
    shown = place_of(obj_lon, obj_lat)
    muni, muni_qid, muni_cat, canton, canton_de = (shown[k] for k in ("muni", "qid", "cat", "canton", "canton_de"))
    country_en, country_de = COUNTRIES[shown["country"]]
    k = float(m["KAPPA"])
    heading = int(k) if k.is_integer() else k

    # plate format: the longer side is the width for landscape scans
    a, c = (float(x) for x in m["DIMENSION"].split(" x "))
    landscape = j["SIZE_PX"]["WIDTH"] >= j["SIZE_PX"]["HEIGHT"]
    h, w = (min(a, c), max(a, c)) if landscape else (max(a, c), min(a, c))
    fmt = lambda x: int(x) if x.is_integer() else x

    plan = category_plan(area, muni, muni_cat, year, country_en)
    cats = [c["name"] for c in plan] + ["Files from swisstopo historic", "Photos uploaded by OKA.wiki"]
    where_de = f"{muni}, Kanton {canton_de}" if canton else f"{muni}, {country_de}"
    where_en = f"{muni}, canton of {canton}" if canton else f"{muni}, {country_en}"

    label = f"{area} – {station}" if station else area
    st_de = f", Station {station}" if station else ""
    st_en = f", station {station}" if station else ""
    fields = [("Camera", m["CAMERA"])] if m["CAMERA"] not in ("", "N/A") else []
    if m["FOCAL_LENGTH"] not in ("", "N/A"):
        fields.append(("Focal length", f"{m['FOCAL_LENGTH']} mm"))
    fields.append(("Camera altitude", f"{int(float(m['Z']))} m"))
    if j.get("STEREO_PARTNER"):
        side = {"L": "left", "R": "right"}.get(j.get("POSITION"), "")
        partner = f"[[:{partner_title}]]" if partner_title else f"swisstopo inventory number {j['STEREO_PARTNER']}"
        base = f"; stereo base {fmt(float(j['BASE_LENGTH_M']))} m" if j.get("BASE_LENGTH_M") else ""
        fields.append(("Stereo pair", f"{side + ' image; ' if side else ''}partner: {partner}{base}"))
    if layer.get("expert_sheet_url"):
        fields.append(("Orientation data", f"[{layer['expert_sheet_url']} swisstopo expert sheet (PDF)]"))
    if layer.get("smapshot_id"):
        fields.append(("3D view", f"[https://smapshot.heig-vd.ch/visit/{layer['smapshot_id']} georeferenced view on Smapshot]"))
    other = "\n                      ".join(f"{{{{Information field|name={k}|value={v}}}}}" for k, v in fields)
    place = muni_qid or (f"{muni}, {canton}, Switzerland" if canton else f"{muni}, {country_en}")
    text = f"""=={{{{int:filedesc}}}}==
{{{{Photograph
 |photographer      = Q685592
 |title             = {{{{Title|{label}|lang=de}}}}
 |description       = {{{{de|1=Terrestrische photogrammetrische Aufnahme, Aufnahmegebiet {area}{st_de}. Aufgenommen Richtung {heading}°; abgebildetes Gebiet: {where_de}.}}}}
                      {{{{en|1=Terrestrial photogrammetric survey photograph, {area} survey{st_en}. Taken facing {heading}°; area shown: {where_en}.}}}}
 |depicted place    = {place}
 |date              = {when['wikitext']}
 |medium            = {{{{Technique|photograph|adj=black and white|on=glass}}}}
 |dimensions        = {{{{Size|unit=cm|height={fmt(h)}|width={fmt(w)}}}}}
 |institution       = {{{{Institution:Swisstopo}}}}
 |accession number  = {m['INVENTORY_NUMBER']}
 |source            = [https://data.geo.admin.ch/browser/index.html#/collections/{COLLECTION}/items/{fid(num)} swisstopo geo.admin.ch browser]; reduced-resolution overviews removed from the source GeoTIFF without re-encoding
 |permission        = {{{{Attribution-Swisstopo}}}}
 |other_fields      = {other}
}}}}
{{{{Location|{cam_lat}|{cam_lon}|region:CH_heading:{heading}}}}}
{{{{Object location|{obj_lat}|{obj_lon}|region:{shown['country'].upper()}}}}}

""" + "\n".join(f"[[Category:{c}]]" for c in cats) + "\n"
    sdc = structured_data(
        num=m["INVENTORY_NUMBER"], when=when, depicted=muni_qid, created_at=place_of(cam_lon, cam_lat)["qid"],
        url=f"https://data.geo.admin.ch/browser/index.html#/collections/{COLLECTION}/items/{fid(num)}",
        camera=(cam_lat, cam_lon, heading), area=(obj_lat, obj_lon),
        captions={
            "en": f"Swisstopo terrestrial survey photograph, {label}, {year}",
            "de": f"Terrestrische Aufnahme von swisstopo, {label}, {year}",
            "fr": f"Prise de vue terrestre de swisstopo, {label}, {year}",
        })
    return dict(num=num, title=title, text=text, sdc=sdc, categories=plan, partner=str(j.get("STEREO_PARTNER") or ""),
                tif_url=f"{DATA}/{fid(num)}/{fid(num)}.tif")


SWISSTOPO = "Q685592"            # Federal Office of Topography
PHOTOGRAPHIC_PLATE = "Q1138868"
FILE_ON_INTERNET = "Q74228490"
OPENDATA_SWISS_BY = "Q133802534"  # "Open use. Must provide the source." (swisstopo OGD terms)
DEGREE = "http://www.wikidata.org/entity/Q28390"


def _item(qid):
    return {"value": {"entity-type": "item", "numeric-id": int(qid[1:]), "id": qid}, "type": "wikibase-entityid"}


def _time(day, precision):
    return {"value": {"time": f"+{day}T00:00:00Z", "timezone": 0, "before": 0, "after": 0, "precision": precision,
                      "calendarmodel": "http://www.wikidata.org/entity/Q1985727"}, "type": "time"}


def _snak(prop, datavalue):
    return {"snaktype": "value", "property": prop, "datavalue": datavalue}


def _claim(prop, datavalue, qualifiers=()):
    c = {"mainsnak": _snak(prop, datavalue), "type": "statement", "rank": "normal"}
    if qualifiers:
        c["qualifiers"] = {p: [_snak(p, v)] for p, v in qualifiers}
        c["qualifiers-order"] = [p for p, _ in qualifiers]
    return c


def structured_data(num, when, depicted, created_at, url, camera, area, captions):
    """Commons structured data (wbeditentity payload) for one image."""
    coord = lambda lat, lon: {"value": {"latitude": lat, "longitude": lon, "altitude": None, "precision": 1e-05,
                                        "globe": "http://www.wikidata.org/entity/Q2"}, "type": "globecoordinate"}
    claims = [
        _claim("P170", _item(SWISSTOPO)),                                          # creator
        _claim("P571", _time(when["time"], when["precision"]),                     # inception
               [(q, _time(when[k], 11)) for q, k in (("P1319", "earliest"), ("P1326", "latest")) if k in when]),
        _claim("P195", _item(SWISSTOPO), [("P217", {"value": num, "type": "string"})]),  # collection + inventory no.
        _claim("P186", _item(PHOTOGRAPHIC_PLATE)),                                 # made from material
        _claim("P7482", _item(FILE_ON_INTERNET), [("P973", {"value": url, "type": "string"}),
                                                   ("P137", _item(SWISSTOPO))]),   # source of file
        _claim("P275", _item(OPENDATA_SWISS_BY)),                                  # licence
        _claim("P1259", coord(camera[0], camera[1]),
               [("P7787", {"value": {"amount": f"+{camera[2]}", "unit": DEGREE}, "type": "quantity"})]),
        _claim("P9149", coord(*area)),                                             # coordinates of depicted place
    ]
    if depicted:
        claims.append(_claim("P180", _item(depicted)))                             # depicts
    if created_at:
        claims.append(_claim("P1071", _item(created_at)))                          # location of creation
    labels = {lang: {"language": lang, "value": text} for lang, text in captions.items()}
    return {"labels": labels, "claims": claims}
