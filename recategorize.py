"""Move uploaded swisstopo files from general categories into the swisstopo subcategories.

Reads every file in Category:Terrestrial photographs by swisstopo, works out survey area, municipality
(and its Commons category) and year from the live page, and replaces only the category lines with the
subcategories plus the hidden tracking categories. Pages it cannot read unambiguously are skipped and
listed, never guessed.

Usage: python recategorize.py plan                 -> recat_plan.json (no edits)
       python recategorize.py create               -> create the needed category pages
       python recategorize.py edit --shard i/n [--edit-interval S]
"""
import argparse
import hashlib
import json
import re
import sys
import time

import swisstopo as s
from botsession import commons_site

PLAN = "recat_plan.json"
KEEP = ["Files from swisstopo historic", "Photos uploaded by OKA.wiki"]
GENERAL = re.compile(r"^(\d{4}s? photographs of |\d{4}s? in |Canton of .* in the \d{4}s$|Black and white photographs of |"
                     r"Photographs by swisstopo$|Terrestrial photographs by swisstopo)")


def members():
    params = dict(action="query", list="categorymembers", cmtitle=f"Category:{s.ROOT_CATEGORY}", cmtype="file",
                  cmlimit=500, format="json")
    while True:
        r = s.commons_query(params)
        yield from (m["title"] for m in r["query"]["categorymembers"])
        if "continue" not in r:
            return
        params.update(r["continue"])


def texts(titles):
    for k in range(0, len(titles), 50):
        r = s.commons_query(dict(action="query", titles="|".join(titles[k:k + 50]), prop="revisions",
                                 rvprop="content|ids", rvslots="main", format="json"))
        for p in r["query"]["pages"].values():
            rev = p["revisions"][0]
            yield p["title"], rev["slots"]["main"]["*"], rev["revid"]


def new_categories(text):
    """(categories, plan) for a page, or raises ValueError when the page cannot be read unambiguously."""
    title = re.search(r"\{\{Title\|(.+?)\|lang=de\}\}", text)
    area = title.group(1).split(" – ")[0] if title else None
    shown = re.search(r"area shown: (.+?)\.\}\}", text)
    year = re.search(r"\|date\s*=\s*(?:\{\{other date\|between\|)?(\d{4})", text)
    if not (area and shown and year):
        raise ValueError("survey area, area shown or year not found")
    where = shown.group(1)
    if ", canton of " in where:
        muni, country = where.split(", canton of ")[0], "Switzerland"
    else:
        muni, _, country = where.rpartition(", ")
    old = re.findall(r"^\[\[Category:([^\]|]+)(?:\|[^\]]*)?\]\]\s*$", text, re.M)
    specific = [c for c in old if c not in KEEP and not GENERAL.match(c)]
    if len(specific) > 1:
        raise ValueError(f"more than one place category: {specific}")
    plan = s.category_plan(area, muni, specific[0] if specific else None, year.group(1), country)
    return [c["name"] for c in plan] + KEEP, plan


def replace_categories(text, cats):
    body = re.sub(r"^\[\[Category:[^\]]+\]\]\s*\n?", "", text, flags=re.M).rstrip() + "\n\n"
    return body + "\n".join(f"[[Category:{c}]]" for c in cats) + "\n"


def make_plan():
    titles = list(members())
    plan, skipped = [], []
    for title, text, revid in texts(titles):
        try:
            cats, cplan = new_categories(text)
        except ValueError as e:
            skipped.append((title, str(e)))
            continue
        new = replace_categories(text, cats)
        if new.strip() != text.strip():
            plan.append(dict(title=title, revid=revid, new=new, categories=cplan))
    json.dump(dict(edits=plan, skipped=skipped), open(PLAN, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"{len(titles)} files: {len(plan)} to re-categorise, {len(skipped)} skipped", flush=True)
    for t, why in skipped[:20]:
        print("  skipped:", t, "|", why)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["plan", "create", "edit"])
    ap.add_argument("--shard", default="0/1")
    ap.add_argument("--edit-interval", type=float, default=1.0)
    a = ap.parse_args()
    if a.step == "plan":
        return make_plan()
    data = json.load(open(PLAN, encoding="utf-8"))
    with commons_site() as site:
        import pywikibot

        if a.step == "create":
            from create_categories import ensure
            ensure(site, [c for e in data["edits"] for c in e["categories"]])
            return
        shard, shards = (int(x) for x in a.shard.split("/"))
        mine = [e for e in data["edits"] if int(hashlib.md5(e["title"].encode()).hexdigest(), 16) % shards == shard]
        done_path = f"recat-done-{shard}.txt"
        try:
            done = set(open(done_path, encoding="utf-8").read().splitlines())
        except FileNotFoundError:
            done = set()
        last = 0.0
        for e in mine:
            if e["title"] in done:
                continue
            page = pywikibot.FilePage(site, e["title"])
            if page.latest_revision_id != e["revid"]:
                # changed since planning (e.g. a partner link): redo the category swap on the current text
                cats, _ = new_categories(page.text)
                e["new"] = replace_categories(page.text, cats)
            wait = last + a.edit_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            last = time.monotonic()
            page.text = e["new"]
            page.save(summary="Move into swisstopo subcategories (survey area, municipality, year) instead of "
                              "general categories ([[Commons:Bots/Requests/OKA bot]])", bot=True, minor=True, quiet=True)
            with open(done_path, "a", encoding="utf-8") as f:
                f.write(e["title"] + "\n")
            print(f"recategorised {e['title']}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
