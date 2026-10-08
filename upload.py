"""Upload prepared swisstopo files to Commons as OKA bot.

Each upload is checked (SHA-1 on Commons = local file), then its structured data
is added and read back. Stops at the first non-transient warning or error.

Several processes can share one batch: `--shard i/n` takes every n-th file
(offset i). Each process records progress in its own `<batch>.progress-<i>.jsonl`
file, so a rerun continues where it stopped; `--merge` folds the progress files
into the batch file once all shards have finished (done automatically with one shard).
`--edit-interval` is the minimum number of seconds between two edits of one process
(n processes at 1 s each give at most n edits per second overall).

Usage: python upload.py batch.json [--shard 0/1] [--edit-interval 1] [--delay SECONDS]
       python upload.py batch.json --merge
"""
import argparse
import glob
import hashlib
import json
import os
import sys
import time

from botsession import commons_site, upload_with_retry, write_structured_data

DEFERRED_PROPERTIES = set()  # properties held back for a later Wikidata linking step (none for now)


def progress_files(batch):
    return sorted(glob.glob(f"{batch}.progress-*.jsonl"))


def load(batch):
    """Batch pages with the state recorded in all progress files applied."""
    pages = json.load(open(batch, encoding="utf-8"))
    by_num = {p["num"]: p for p in pages}
    for path in progress_files(batch):
        for line in open(path, encoding="utf-8"):
            if line.strip():
                rec = json.loads(line)
                by_num[rec["num"]].update({k: v for k, v in rec.items() if k != "num"})
    return pages


def merge(batch):
    pages = load(batch)
    tmp = batch + ".tmp"
    json.dump(pages, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, batch)
    for path in progress_files(batch):
        os.remove(path)


class Pacer:
    """Keeps at least `interval` seconds between two edits of this process."""

    def __init__(self, interval):
        self.interval, self.last = interval, 0.0

    def __call__(self):
        wait = self.last + self.interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self.last = time.monotonic()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("batch")
    ap.add_argument("--shard", default="0/1")
    ap.add_argument("--edit-interval", type=float, default=1.0)
    ap.add_argument("--delay", type=float, default=0.0, help="extra pause after each file")
    ap.add_argument("--merge", action="store_true")
    args = ap.parse_args()
    if args.merge:
        merge(args.batch)
        return
    shard, shards = (int(x) for x in args.shard.split("/"))
    pages = load(args.batch)
    mine = [p for i, p in enumerate(pages) if i % shards == shard]
    progress = open(f"{args.batch}.progress-{shard}.jsonl", "a", encoding="utf-8")
    pace = Pacer(args.edit_interval)

    def record(p, **state):
        p.update(state)
        progress.write(json.dumps({"num": p["num"], **state}) + "\n")
        progress.flush()

    try:
        with commons_site() as site:
            import pywikibot

            print(f"[{shard}/{shards}] logged in as {site.username()} | bot flag: {'bot' in site.userinfo['groups']}"
                  f" | {len(mine)} files", flush=True)
            for p in mine:
                if p.get("uploaded") and p.get("sdc_done"):
                    continue
                if not p.get("uploaded"):
                    if p.get("already_on_commons") or not p.get("verified") or p.get("problems"):
                        reason = "already on Commons" if p.get("already_on_commons") else (p.get("problems") or ["unverified"])
                        print(f"{p['num']}: skipped, not cleared for upload: {reason}", flush=True)
                        continue
                    sha = hashlib.sha1(open(p["stripped"], "rb").read()).hexdigest()
                    page = pywikibot.FilePage(site, p["title"])
                    if page.exists():
                        info = page.latest_file_info
                        if info.sha1 == sha and page.oldest_file_info.user == site.username():
                            # an earlier, interrupted request of ours stored it: carry on with structured data
                            print(f"{p['num']}: already uploaded by an interrupted run; continuing", flush=True)
                            record(p, uploaded=True)
                        else:
                            sys.exit(f"{p['title']} already exists with other content; stopping")
                    if not p.get("uploaded"):
                        pace()
                        if not upload_with_retry(site, p["title"], p["stripped"], sha, text=p["text"], ignore_warnings=False,
                                                 comment=f"Upload swisstopo terrestrial image {p['num']} ([[Commons:Bots/Requests/OKA bot]])"):
                            sys.exit(f"{p['num']}: upload warning, failure or SHA-1 mismatch; stopping")
                        record(p, uploaded=True)
                sdc = {**p["sdc"], "claims": [c for c in p["sdc"]["claims"]
                                              if c["mainsnak"]["property"] not in DEFERRED_PROPERTIES]}
                pace()
                added = write_structured_data(site, p["title"], sdc,
                                              "Structured data from swisstopo metadata ([[Commons:Bots/Requests/OKA bot]])")
                record(p, sdc_done=True)
                print(f"uploaded and verified, structured data: {len(added)} added | {p['title']}", flush=True)
                if args.delay:
                    time.sleep(args.delay)
    finally:
        progress.close()
        if shards == 1:
            merge(args.batch)


if __name__ == "__main__":
    main()
