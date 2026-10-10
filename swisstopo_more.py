"""Page and structured-data builders for swisstopo's other image collections.

Aerial images (black and white, colour, infrared, oblique) and technical images. Shares the helpers of
swisstopo.py (places, dates, structured data); values the metadata does not support are left out,
never guessed (e.g. focal lengths that cannot be millimetres).
"""
import json

import swisstopo as s

AERIAL = {
    "bw": dict(collection="ch.swisstopo.lubis-luftbilder_schwarzweiss", label="black-and-white aerial",
               root="Black and white aerial photographs by swisstopo",
               parents=["Aerial photographs by swisstopo", "Black and white aerial photographs of Switzerland"],
               technique="{{Technique|photograph|adj=black and white}}", en="Black-and-white aerial photograph",
               de="Schwarzweiss-Luftbild", fr="Photographie aérienne en noir et blanc"),
    "colour": dict(collection="ch.swisstopo.lubis-luftbilder_farbe", label="colour aerial",
                   root="Color aerial photographs by swisstopo",
                   parents=["Aerial photographs by swisstopo", "Aerial photographs of Switzerland"],
                   technique="{{Technique|photograph|adj=color}}", en="Colour aerial photograph",
                   de="Farb-Luftbild", fr="Photographie aérienne en couleurs"),
    "ir": dict(collection="ch.swisstopo.lubis-luftbilder_infrarot", label="infrared aerial",
               root="Infrared aerial photographs by swisstopo",
               parents=["Aerial photographs by swisstopo", "Infrared aerial photography"],
               technique="{{Technique|infrared photograph}}", en="Infrared (false-colour) aerial photograph",
               de="Infrarot-Luftbild (Falschfarben)", fr="Photographie aérienne infrarouge (fausses couleurs)"),
    "oblique": dict(collection="ch.swisstopo.lubis-luftbilder_schraegaufnahmen", label="oblique aerial",
                    root="Oblique aerial photographs by swisstopo",
                    parents=["Aerial photographs by swisstopo", "Aerial photographs of Switzerland"],
                    technique=None, en="Oblique aerial photograph", de="Schräg-Luftbild",
                    fr="Photographie aérienne oblique"),
}
TECH = dict(collection="ch.swisstopo.technische-aufnahmen", root="Technical images by swisstopo",
            parents=["Photographs by swisstopo", "Surveying in Switzerland", "Glass plate negatives"])
# swisstopo technical-image categories -> (English label, Wikidata item depicted) where known
TECH_CATEGORIES = {"Vermessungspunkte Inland": ("survey points in Switzerland", "Q131862")}

FILM, AERIAL_PHOTOGRAPH, GLASS_NEGATIVE = "Q6293", "Q113670888", "Q57408632"
FILM_MEDIUM = {  # swisstopo FILM_TYPE -> localized medium
    "black and white": "{{Technique|photograph|adj=black and white}}",
    "colored": "{{Technique|photograph|adj=color}}",
    "infrared false-colored": "{{en|1=infrared false-colour photograph}} {{de|1=Infrarot-Falschfarbenfotografie}} "
                              "{{fr|1=photographie infrarouge en fausses couleurs}}",
}
LANG = {"GE": "fr", "VD": "fr", "NE": "fr", "JU": "fr", "FR": "fr", "TI": "it"}


def _plans(root, muni, muni_cat, year, en, de):
    place = muni_cat or muni
    return [
        dict(name=f"{root} of {s._clean(place)}", sortkey=s._clean(place),
             parents=[f"{root} by municipality"] + ([muni_cat] if muni_cat else []),
             en=f"{en} by swisstopo showing {muni}.", de=f"{de} von swisstopo, die {muni} zeigen."),
        dict(name=f"{root} in {year}", sortkey=year,
             parents=[f"{root} by year", f"{year} photographs of Switzerland"],
             en=f"{en} by swisstopo taken in {year}.", de=f"{de} von swisstopo aus dem Jahr {year}."),
    ]


def _fields(pairs):
    return "\n                      ".join(f"{{{{Information field|name={k}|value={v}}}}}" for k, v in pairs if v)


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return int(v) if v.is_integer() else v


def build_aerial(kind, item):
    """item: a STAC feature (id, assets, bbox) with its CSV metadata in item['meta']."""
    cfg, m = AERIAL[kind], item["meta"]
    inv, year = m["INVENTORY_NUMBER"], m["YEAR"]
    when = s.parse_date(m["ACQUIRED"]) if m.get("ACQUIRED") not in (None, "", "N/A") else s.parse_date(year)
    b = item["bbox"]
    obj_lon, obj_lat = round((b[0] + b[2]) / 2, 5), round((b[1] + b[3]) / 2, 5)
    cam_lat, cam_lon = s.lv95_to_wgs84(m["E"], m["N"])
    shown = s.place_of(obj_lon, obj_lat)
    muni, muni_qid, muni_cat = shown["muni"], shown["qid"], shown["cat"]
    where_en = f"{muni}, canton of {shown['canton']}" if shown["canton"] else f"{muni}, {s.COUNTRIES[shown['country']][0]}"
    where_de = f"{muni}, Kanton {shown['canton_de']}" if shown["canton"] else f"{muni}, {s.COUNTRIES[shown['country']][1]}"
    place = m.get("PLACE") if m.get("PLACE") not in (None, "", "N/A") else None
    title = f"File:{s._clean_filename(muni)} - Swisstopo {cfg['label']} photograph {inv}.tif"
    label = place or f"{muni} ({year})"
    lang = LANG.get(shown.get("code"), "de")
    fl = _num(m.get("FOCAL_LENGTH"))
    side = _num(m["DIMENSION"].split(" x ")[0]) if " x " in m.get("DIMENSION", "") else None
    alt = _num(m.get("Z"))
    asset = lambda suffix: next((h for n, h in item["assets"].items() if n.endswith(suffix)), None)
    calibration = next((h for n, h in item["assets"].items() if "calibration" in n), None)
    fields = [("Series", m.get("SERIES") if m.get("SERIES") not in ("", "N/A") else None),
              ("Place (swisstopo)", place),
              ("Camera", m.get("CAMERA") if m.get("CAMERA") not in ("", "N/A") else None),
              ("Focal length", f"{fl} mm" if fl and fl >= 50 else None),   # smaller values are not millimetres
              ("Flight altitude", f"{alt} m" if alt else None),
              ("Film", m.get("FILM_TYPE") if m.get("FILM_TYPE") not in ("", "N/A") else None),
              ("Stereo partner", f"swisstopo inventory number {m['STEREO_PARTNER']}"
               if m.get("STEREO_PARTNER") not in (None, "", "N/A") else None),
              ("Camera calibration", f"[{calibration} swisstopo calibration report (PDF)]" if calibration else None)]
    url = f"https://data.geo.admin.ch/browser/index.html#/collections/{cfg['collection']}/items/{item['id']}"
    view = "oblique" if kind == "oblique" else "vertical"
    plans = [dict(name=cfg["root"], sortkey=None, parents=cfg["parents"],
                  en=f"{cfg['en']}s by the Swiss Federal Office of Topography (swisstopo).",
                  de=f"{cfg['de']}er des Bundesamts für Landestopografie (swisstopo).")] + \
        _plans(cfg["root"], muni, muni_cat, when["year"], cfg["en"] + "s", cfg["de"] + "er")
    cats = [p["name"] for p in plans[1:]] + ["Photos uploaded by OKA.wiki"]
    text = f"""=={{{{int:filedesc}}}}==
{{{{Photograph
 |photographer      = Q685592
 |title             = {{{{Title|{label}|lang={lang}}}}}
 |description       = {{{{en|1={cfg['en']} ({view}) by swisstopo{', ' + place if place else ''}. Area shown: {where_en}.}}}}
                      {{{{de|1={cfg['de']} ({'Schrägaufnahme' if view == 'oblique' else 'Senkrechtaufnahme'}) von swisstopo{', ' + place if place else ''}. Abgebildetes Gebiet: {where_de}.}}}}
 |depicted place    = {muni_qid or where_en}
 |date              = {when['wikitext']}
 |medium            = {FILM_MEDIUM.get(m.get('FILM_TYPE'), '')}
 |dimensions        = {f"{{{{Size|unit=cm|height={side}|width={side}}}}}" if side else ''}
 |institution       = {{{{Institution:Swisstopo}}}}
 |accession number  = {inv}
 |source            = [{url} swisstopo geo.admin.ch browser]; reduced-resolution overviews removed from the source GeoTIFF without re-encoding
 |permission        = {{{{Attribution-Swisstopo}}}}
 |other_fields      = {_fields(fields)}
}}}}
{{{{Location|{cam_lat}|{cam_lon}|region:CH}}}}
{{{{Object location|{obj_lat}|{obj_lon}|region:{shown['country'].upper()}}}}}

""" + "\n".join(f"[[Category:{c}]]" for c in cats) + "\n"
    sdc = s.structured_data(num=inv, when=when, depicted=muni_qid, created_at=s.place_of(cam_lon, cam_lat)["qid"],
                            url=url, camera=(cam_lat, cam_lon, None), area=(obj_lat, obj_lon),
                            captions={"en": f"{cfg['en']} by swisstopo, {label}, {when['year']}",
                                      "de": f"{cfg['de']} von swisstopo, {label}, {when['year']}",
                                      "fr": f"{cfg['fr']} de swisstopo, {label}, {when['year']}"},
                            material=FILM, instance_of=AERIAL_PHOTOGRAPH)
    return dict(num=inv, title=title, text=text, sdc=sdc, categories=plans, tif_url=asset("_2056.tif") or asset(".tif"))


def build_technical(item):
    m = item["meta"]
    sig, name = m["ORIGINAL_SIGNATURE"], m["IMAGE_TITLE"]
    when = s.parse_date(m["DATE_STRING"])
    lat, lon = s.lv95_to_wgs84(m["EASTING"], m["NORTHING"])
    shown = s.place_of(lon, lat)
    muni, muni_qid, muni_cat = shown["muni"], shown["qid"], shown["cat"]
    cat_en, depicts = TECH_CATEGORIES.get(m["TNA_CATEGORY"], (None, None))
    fmt = m.get("MEDIUM_FORMAT", "")
    h, w = (_num(x) for x in fmt.split(" x ")) if " x " in fmt else (None, None)
    negative = m.get("POLARITY") == "negative" and m.get("MATERIAL") == "Glass"
    url = f"https://data.geo.admin.ch/browser/index.html#/collections/{TECH['collection']}/items/{item['id']}"
    title = f"File:{s._clean_filename(name)} - Swisstopo technical image {sig}.tif"
    plans = [dict(name=TECH["root"], sortkey=None, parents=TECH["parents"],
                  en="Technical photographs (survey points, instruments, work) by the Swiss Federal Office of Topography (swisstopo).",
                  de="Technische Aufnahmen (Vermessungspunkte, Instrumente, Arbeiten) des Bundesamts für Landestopografie (swisstopo).")] + \
        _plans(TECH["root"], muni, muni_cat, when["year"], "Technical images", "Technische Aufnahmen")
    cats = [p["name"] for p in plans[1:]] + ["Photos uploaded by OKA.wiki"]
    fields = [("Collection (swisstopo)", m.get("TNA_COLLECTION")), ("Category (swisstopo)", m.get("TNA_CATEGORY")),
              ("Original ID", m.get("ORIGINAL_ID")),
              ("Author", m.get("AUTHOR") if m.get("AUTHOR") not in ("", "N/A") else None)]
    en_desc = f"Technical photograph by swisstopo: {name}" + (f" ({cat_en})" if cat_en else "") + f", {muni}."
    text = f"""=={{{{int:filedesc}}}}==
{{{{Photograph
 |photographer      = Q685592
 |title             = {{{{Title|{name}|lang=de}}}}
 |description       = {{{{de|1={m.get('IMAGE_DESCRIPTION', '')}}}}}
                      {{{{en|1={en_desc}}}}}
 |depicted place    = {muni_qid or muni}
 |date              = {when['wikitext']}
 |medium            = {'{{Technique|glass plate negative}}' if negative else ''}
 |dimensions        = {f"{{{{Size|unit=cm|height={h}|width={w}}}}}" if h and w else ''}
 |institution       = {{{{Institution:Swisstopo}}}}
 |accession number  = {sig}
 |source            = [{url} swisstopo geo.admin.ch browser]; reduced-resolution overviews removed from the source GeoTIFF without re-encoding
 |permission        = {{{{Attribution-Swisstopo}}}}
 |other_fields      = {_fields(fields)}
}}}}
{{{{Object location|{lat}|{lon}|region:{shown['country'].upper()}}}}}

""" + "\n".join(f"[[Category:{c}]]" for c in cats) + "\n"
    sdc = s.structured_data(num=sig, when=when, depicted=muni_qid, created_at=None, url=url, camera=None,
                            area=(lat, lon), captions={"en": f"Technical image by swisstopo: {name}, {when['year']}",
                                                       "de": f"Technische Aufnahme von swisstopo: {name}, {when['year']}",
                                                       "fr": f"Image technique de swisstopo : {name}, {when['year']}"},
                            material=GLASS_NEGATIVE if negative else None, extra_depicts=depicts)
    return dict(num=sig, title=title, text=text, sdc=sdc, categories=plans, tif_url=m["URL"])


def load_samples(path="work/samples/samples.json"):
    return json.load(open(path, encoding="utf-8"))
