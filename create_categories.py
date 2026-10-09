"""Create the swisstopo subcategories that files are about to use (survey area, municipality, year).

Only categories that will hold files are created. Each page gets an English and German description
and its parent categories; a parent that does not exist is replaced by its decade category (years)
or left out. Existing pages are never changed.

Usage: python create_categories.py batch.json      (categories of that batch's files)
       (or import ensure() with a list of category plans)
"""
import json
import sys
import time

import swisstopo as s
from botsession import commons_site

META = {f"{s.ROOT_CATEGORY} by municipality": "Municipality", f"{s.ROOT_CATEGORY} by year": "Year"}


def _existing(names):
    have = set()
    names = list(names)
    for k in range(0, len(names), 50):
        have |= s.existing_categories(names[k:k + 50])
    return have


def page_text(plan, have):
    parents = []
    for p in plan["parents"]:
        if p in have:
            parents.append(p)
        elif p.endswith("photographs of Switzerland") and p[:4].isdigit():
            decade = f"{p[:3]}0s photographs of Switzerland"
            if decade in have:
                parents.append(decade)
    lines = [f"{{{{en|1={plan['en']}}}}}", f"{{{{de|1={plan['de']}}}}}", ""]
    for p in parents:
        sort = f"|{plan['sortkey']}" if p in META or p == s.ROOT_CATEGORY else ""
        lines.append(f"[[Category:{p}{sort}]]")
    return "\n".join(lines) + "\n"


def ensure(site, plans):
    """Create every category in plans that does not exist yet; returns the names created."""
    import pywikibot

    plans = list({p["name"]: p for p in plans}.values())
    wanted = {p["name"] for p in plans} | set(META)
    parents = {q for p in plans for q in p["parents"]}
    decades = {f"{q[:3]}0s photographs of Switzerland" for q in parents if q[:4].isdigit()}
    have = _existing(wanted | parents | decades)
    created = []
    meta_plans = [dict(name=m, sortkey=label, parents=[s.ROOT_CATEGORY],
                       en=f"Terrestrial survey photographs by swisstopo, by {label.lower()}.",
                       de=f"Terrestrische Aufnahmen von swisstopo, nach {'Gemeinde' if label == 'Municipality' else 'Jahr'}.")
                  for m, label in META.items()]
    for plan in meta_plans + plans:
        if plan["name"] in have:
            continue
        page = pywikibot.Category(site, plan["name"])
        if page.exists():
            have.add(plan["name"])
            continue
        page.text = page_text(plan, have)
        page.save(summary="Category for swisstopo terrestrial photographs uploaded by OKA bot "
                          "([[Commons:Bots/Requests/OKA bot]])", bot=True, quiet=True)
        if not pywikibot.Category(site, plan["name"]).exists():
            raise SystemExit(f"{plan['name']}: not created; stopping")
        have.add(plan["name"])
        created.append(plan["name"])
        print(f"created Category:{plan['name']}", flush=True)
        time.sleep(1)
    return created


def main():
    pages = json.load(open(sys.argv[1], encoding="utf-8"))
    plans = [c for p in pages if p.get("categories") and not p.get("problems") for c in p["categories"]]
    with commons_site() as site:
        created = ensure(site, plans)
    print(f"{len(created)} categories created", flush=True)


if __name__ == "__main__":
    main()
