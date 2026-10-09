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
        sort = f"|{plan['sortkey']}" if plan.get("sortkey") and (p.endswith((" by municipality", " by year"))
                                                                  or p.startswith(s.ROOT_CATEGORY)) else ""
        lines.append(f"[[Category:{p}{sort}]]")
    return "\n".join(lines) + "\n"


def ensure(site, plans):
    """Create every category in plans that does not exist yet (plus the collection's "by municipality" and
    "by year" overview categories); returns the names created."""
    import pywikibot

    plans = list({p["name"]: p for p in plans}.values())
    metas = {}
    for p in plans:
        for q in p["parents"]:
            for suffix, label, de in ((" by municipality", "municipality", "Gemeinde"), (" by year", "year", "Jahr")):
                if q.endswith(suffix):
                    root = q[:-len(suffix)]
                    metas[q] = dict(name=q, sortkey=label.capitalize(), parents=[root],
                                    en=f"{root}, by {label}.", de=f"{root}, nach {de}.")
    is_sub = lambda p: any(q.endswith((" by municipality", " by year")) for q in p["parents"])
    order = [p for p in plans if not is_sub(p)] + list(metas.values()) + [p for p in plans if is_sub(p)]
    parents = {q for p in order for q in p["parents"]}
    decades = {f"{q[:3]}0s photographs of Switzerland" for q in parents if q[:4].isdigit()}
    have = _existing({p["name"] for p in order} | parents | decades)
    created = []
    for plan in order:   # collection roots first, then overview categories, then subcategories
        if plan["name"] in have:
            continue
        page = pywikibot.Category(site, plan["name"])
        if page.exists():
            have.add(plan["name"])
            continue
        page.text = page_text(plan, have)
        page.save(summary="Category for swisstopo photographs uploaded by OKA bot "
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
