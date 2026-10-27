"""Upload swisstopo's oblique aerial images and technical images to Commons, batch after batch, unattended.

Approved in Commons:Bots/Requests/OKA bot 2 (full resolution, overviews removed without re-encoding). The vertical
aerial images (kinds colour, ir, bw) go up as the raw scan's built-in reduced level (about 18-22 MP, no re-encoding)
(never the orthophoto), as jz decided on 2026-10-18; their duplicate check is by inventory number only.
For each collection the STAC item list is fetched once and cached (stac-<kind>.json); batches are
named <kind>001.json, <kind>002.json, ... and a finished batch gets a <kind>NNN.done file, so a rerun
continues with the first unfinished batch.

Per batch: prepare (build the page from the item's CSV metadata, skip files already on Commons by title
and by the source's SHA-1, download, strip overviews, verify, dry-run the page) -> create categories ->
upload with N processes (each at most one edit per --edit-interval seconds; files over 95 MiB in 20 MiB
chunks; SHA-1 checked; structured data written and read back; each local file deleted once done) ->
check every preview renders. The next batch is prepared while the current one uploads; downloads wait
while the work folder holds more than DISK_LIMIT bytes.

Stops for review on: a failed step, any upload process stopping (or hanging: the watchdog), any file
without a preview, more than MAX_SET_ASIDE of a batch set aside for reasons other than the record itself
(duplicates do not count), or more than MAX_DATA_ERRORS set aside for errors in swisstopo's records.

Usage: python collection_pipeline.py run oblique technical [--batches 1000] [--size N] [--shards 4] [--edit-interval 1]
       python collection_pipeline.py prepare KIND OUT.json COUNT     (one batch; used by run)
Log: collections.log or --log FILE (one line per batch step; the last line starts with RESULT); details in <batch>.log.
"""
import argparse
import csv
import glob
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import tifffile

import swisstopo as s
import swisstopo_more as more
from strip_overviews import strip_overviews
from verify_strip import verify

REQUEST = "Commons:Bots/Requests/OKA bot 2"
KINDS = {
    # workers: files prepared in parallel. An oblique scan decodes to about 860 MB (17,000 px RGB), so one
    # at a time keeps the job under its 4 GiB next to the upload processes.
    "oblique": dict(collection=more.AERIAL["oblique"]["collection"], size=20, workers=1,
                    build=lambda item: more.build_aerial("oblique", item), label="oblique aerial photograph"),
    "technical": dict(collection=more.TECH["collection"], size=25, workers=2,
                      build=more.build_technical, label="technical image"),
    # Vertical aerial images: reduced-resolution copies only (jz, 2026-10-18), never orthophotos. Since 2026-10-20
    # (jz) the copy is the scan's own built-in reduced level, copied without decoding (light: four at a time).
    **{k: dict(collection=more.AERIAL[k]["collection"], size=200, workers=4, reduced=True,
               label=f"{more.AERIAL[k]['label']} photograph") for k in ("colour", "ir", "bw")},
}
SAMPLES = {"lubis-luftbilder_schraegaufnahmen_000-014-518", "technische-aufnahmen_000-389-690",  # on Commons already
           "lubis-luftbilder_schwarzweiss_000-004-128", "lubis-luftbilder_farbe_000-005-172",
           "lubis-luftbilder_infrarot_000-012-529"}
REDUCED_MB = (0.5, 30)            # a reduced copy outside this size (expected about 2-10 MB) is set aside
MIN_SIDE = 4000                   # the reduced copy is the smallest built-in level at least this many px on its long side
HERE = os.path.dirname(os.path.abspath(__file__))
MIN_TIFF_BYTES = 100_000          # smaller than any real scan
DISK_LIMIT = 2.5e9                # bytes one collection's work folder may hold (the tool's disk is shared)
MAX_ATTEMPTS = 3                  # preparation attempts for a file set aside by a failed build or check
MAX_SET_ASIDE = 0.05
MAX_DATA_ERRORS = 0.20
BAD_TITLE_CHARS = re.compile(r"[/:\\\[\]#<>|{}]")
# The technical images' instrument-photo series (born-digital, 2013; about 5,200 items, 000-606... to 000-612...)
# has another CSV layout: no coordinates and an ACQUISITION_DATE column. Since 2026-10-19 it is built by
# swisstopo_more.build_instrument; items set aside under DEFERRED before that are retried. Records naming another
# author (postcard publishers, photographers) are set aside too: swisstopo's licence and authorship may not apply.
DEFERRED = "deferred: instrument-photo record layout (no coordinates) not handled yet"
THIRD_PARTY = "deferred: author is not swisstopo"
HUNG_AFTER = 20 * 60              # seconds without log output before an upload process counts as hung
PY = sys.executable

# see prepare_batch.py: keeps large image buffers out of glibc's arenas (no-op on Windows)
try:
    import ctypes
    ctypes.CDLL("libc.so.6").mallopt(-3, 1 << 20)  # M_MMAP_THRESHOLD
except OSError:
    pass


# ---------------------------------------------------------------- items and metadata

def items(kind):
    """The collection's STAC items in swisstopo's order (id, bbox, assets as name -> URL), fetched once."""
    cache = os.path.join(HERE, f"stac-{kind}.json")
    if not os.path.exists(cache):
        url, params, out = f"https://data.geo.admin.ch/api/stac/v1/collections/{KINDS[kind]['collection']}/items", \
            {"limit": 100}, []
        while url:
            d = json.loads(s.http(url, params))
            params = None
            # only what the pages need (metadata, the raw scan, a calibration report; no orthophoto or quickview),
            # with the usual URL stored as None: the black-and-white list has 222,000 items
            out += [dict(id=f["id"], bbox=f["bbox"], assets={
                n: (None if a["href"] == _standard_href(kind, f["id"], n) else a["href"]) for n, a in f["assets"].items()
                if n.endswith(".csv") or (n.endswith(".tif") and "_op_" not in n) or "calibration" in n})
                for f in d["features"]]
            url = next((l["href"] for l in d["links"] if l["rel"] == "next"), None)
        tmp = cache + ".tmp"
        json.dump(out, open(tmp, "w", encoding="utf-8"))
        os.replace(tmp, cache)
    out = json.load(open(cache, encoding="utf-8"))
    for it in out:
        it["assets"] = {n: h or _standard_href(kind, it["id"], n) for n, h in it["assets"].items()}
    return out


def _standard_href(kind, item_id, name):
    return f"https://data.geo.admin.ch/{KINDS[kind]['collection']}/{item_id}/{name}"


def read_meta(item):
    """The item's one-row CSV (semicolon-separated, UTF-8) as a dict."""
    url = next(h for n, h in item["assets"].items() if n.endswith(".csv"))
    rows = list(csv.DictReader(io.StringIO(s.http(url)), delimiter=";"))
    if len(rows) != 1:
        raise ValueError(f"metadata CSV has {len(rows)} rows")
    return rows[0]


def title_on_commons(kind, num):
    """True if Commons has a file with this collection's name pattern and number (any place name)."""
    tail = f"Swisstopo {KINDS[kind]['label']} {num}"
    r = s.commons_query(dict(action="query", list="search", srnamespace=6, format="json", srsearch=f'intitle:"{tail}"'))
    return any(re.search(re.escape(tail) + r"\.(tif|jpg)$", h["title"]) for h in r["query"]["search"])


def sha1_on_commons(sha1):
    return bool(s.commons_query(dict(action="query", list="allimages", aisha1=sha1, format="json"))["query"]["allimages"])


# ---------------------------------------------------------------- preparation

def work_dir(kind):
    return os.path.join(HERE, "work", kind)


def disk_used(kind):
    """Bytes in this collection's own work folder. Each job budgets only its own collections, so two jobs never
    wait on each other's files (2026-10-20: a shared budget deadlocked the technical and colour jobs)."""
    return sum(os.path.getsize(f) for f in glob.glob(os.path.join(work_dir(kind), "*")) if os.path.isfile(f))


def wait_for_disk(kind):
    for _ in range(6 * 60):                  # up to 3 hours, while the upload processes free space
        if disk_used(kind) < DISK_LIMIT:
            return
        time.sleep(30)
    raise RuntimeError(f"work folders still above {DISK_LIMIT / 1e9:.0f} GB after 3 hours")


def download(url, path):
    """Stream url to path (via a .part file); returns (SHA-1, bytes). Retries like swisstopo.http."""
    part = path + ".part"
    for attempt in range(4):
        try:
            sha, n = hashlib.sha1(), 0
            with s._SESSION.get(url, stream=True, timeout=300) as r, open(part, "wb") as f:
                r.raise_for_status()
                for chunk in r.iter_content(1 << 20):
                    sha.update(chunk)
                    f.write(chunk)
                    n += len(chunk)
            if n < MIN_TIFF_BYTES:          # the server occasionally answers with an empty body
                raise ValueError(f"download returned only {n} bytes")
            os.replace(part, path)
            return sha.hexdigest(), n
        except (s.requests.RequestException, ValueError):
            if attempt == 3:
                raise
            time.sleep(5 * 3 ** attempt)


def dry_run(title, text, own):
    """Problems the Commons parser reports for the page; our own new categories (own) may still be missing."""
    for attempt in range(6):
        try:
            p = json.loads(s.post(s.COMMONS_API, dict(action="parse", format="json", prop="categories|text",
                                                      title=title, text=text)))["parse"]
            break
        except (ValueError, KeyError, s.requests.RequestException):
            if attempt == 5:
                raise
            time.sleep(min(5 * 3 ** attempt, 120))
    own = {c.replace(" ", "_") for c in own}
    problems = [c["*"] for c in p["categories"] if ("missing" in c and c["*"] not in own)
                or re.search("rroneous|error|without|lacking|invalid", c["*"], re.I)]
    if re.search(r'class="(?:error|scribunto-error)', p["text"]["*"]):
        problems.append("template error")
    return problems


def prepare_one(kind, item):
    """Build, check for duplicates, download, strip, verify and dry-run one file. Problems are recorded, never raised."""
    p = dict(num=item["id"], item_id=item["id"], kind=kind, title=None, text="", already_on_commons=False,
             verified=False, problems=[])
    try:
        meta = read_meta(item)
        if kind == "technical" and (meta.get("AUTHOR") not in (None, "", "N/A", "swisstopo") or meta.get("TNA_CATEGORY") == "Ansichtskarten"):
            p["problems"] = [f"{THIRD_PARTY} ({meta.get('AUTHOR')}, {meta.get('TNA_CATEGORY')})"]
            return p
        if KINDS[kind].get("reduced"):
            return prepare_reduced(kind, item, meta, p)
        if kind == "technical" and "DATE_STRING" not in meta and "ACQUISITION_DATE" in meta:
            built = more.build_instrument({**item, "meta": meta})      # instrument-photo series (born-digital)
        else:
            built = KINDS[kind]["build"]({**item, "meta": meta})
    except Exception as e:  # odd record (date, place outside the known countries, ...): set aside for review
        p["problems"] = [f"build failed: {type(e).__name__}: {e}"]
        return p
    p.update(built, item_id=item["id"], kind=kind, request=REQUEST,
             comment=f"Upload swisstopo {KINDS[kind]['label']} {built['num']} ([[{REQUEST}]])")
    if BAD_TITLE_CHARS.search(p["title"][5:]):
        p["problems"] = [f"file name has a character not allowed in titles: {p['title']}"]
        return p
    src = os.path.join(work_dir(kind), f"{item['id']}.tif")
    dst = os.path.join(work_dir(kind), f"{item['id']}.stripped.tif")
    try:
        if title_on_commons(kind, p["num"]):
            p["already_on_commons"] = True
            return p
        wait_for_disk(kind)
        sha, size = download(p["tif_url"], src)
        p["source_sha1"], p["source_bytes"] = sha, size
        if sha1_on_commons(sha):
            p["already_on_commons"] = True
            os.remove(src)
            return p
        strip_overviews(src, dst)
        p["verified"] = verify(src, dst, decode_source=False)
        p["stripped"] = dst
        os.remove(src)                       # the verified stripped copy is all the upload needs
        p["problems"] = dry_run(p["title"], p["text"], [c["name"] for c in p["categories"]])
    except Exception as e:
        p.update(verified=False, problems=[f"file check failed: {type(e).__name__}: {e}"])
        for f in (src, dst, src + ".part"):
            if os.path.exists(f):
                os.remove(f)
    return p


def _bands(page):
    """The page's pixels one row of tiles (or one strip) at a time, as uint8 arrays (rows, width, samples)."""
    import numpy as np

    sh, sw = page.shape[:2]
    spp = page.shape[2] if len(page.shape) == 3 else 1
    band, band_y = None, -1
    for seg, (_, _, y, x, _), shape in page.segments(maxworkers=1):
        if y != band_y:
            if y < band_y:
                raise ValueError("segments out of order")
            if band is not None:
                yield band
            band, band_y = np.zeros((min(shape[1], sh - y), sw, spp), np.uint8), y
        if seg is not None:                  # None: an empty segment, left black
            w = min(shape[2], sw - x)
            band[:, x:x + w] = seg[0, :band.shape[0], :w]
    if band is not None:
        yield band


def built_in_level(src):
    """(level, (w, h) of that level, (w, h) of the full scan): the smallest of the scan's built-in reduced levels
    whose longer side is at least MIN_SIDE px (level 2 for 17,000 px scans, level 1 for 9,700 px ones)."""
    with tifffile.TiffFile(src) as t:
        sizes = [(p.shape[1], p.shape[0]) for p in t.pages]
    ok = [i for i, (w, h) in enumerate(sizes) if max(w, h) >= MIN_SIDE]
    level = ok[-1] if ok else 0
    return level, sizes[level], sizes[0]


def reduce_scan(src, dst, size):
    """For scans whose reduced levels no viewer reads (Deflate with YCbCr subsampling, which Commons previews as green
    bands): resize the full scan (level 0) with Lanczos to `size`, the chosen level's size, and save a JPEG-compressed
    TIFF (quality 92). The horizontal pass runs one band of tiles at a time, so the full scan is never in memory;
    Pillow resizes in the same two passes, so the result equals a one-shot resize. Checks that the copy decodes to
    the resized pixels."""
    import numpy as np
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = None
    with tifffile.TiffFile(src) as t:
        page = t.pages[0]
        if page.dtype.name != "uint8" or len(page.shape) not in (2, 3) or (len(page.shape) == 3 and page.shape[2] != 3):
            raise ValueError(f"unexpected scan layout {page.shape} {page.dtype}")
        mode = "L" if len(page.shape) == 2 else "RGB"
        parts = [np.asarray(Image.fromarray(b[:, :, 0] if mode == "L" else b).resize((size[0], b.shape[0]), Image.LANCZOS))
                 for b in _bands(page)]
    out = Image.fromarray(np.concatenate(parts)).resize(size, Image.LANCZOS)   # width matches: vertical pass only
    del parts
    out.save(dst, "TIFF", compression="jpeg", quality=92)
    with Image.open(dst) as check:
        check.load()
        if check.size != tuple(size) or check.mode != mode:
            raise ValueError(f"saved copy is {check.size} {check.mode}, expected {tuple(size)} {mode}")
        diff = np.abs(np.asarray(check, np.int16) - np.asarray(out, np.int16)).mean()
    if diff > 4:
        raise ValueError(f"saved copy differs from the resized scan by {diff:.1f} on average")


def prepare_reduced(kind, item, meta, p):
    """Vertical aerial image: duplicate check by inventory number, download the raw scan, copy out its built-in
    reduced level (no decoding or re-encoding; verified tile by tile), build the page and dry-run it."""
    num = p["num"] = meta.get("INVENTORY_NUMBER") or item["id"]
    url = more.raw_scan_url(item)
    if not url or "_op_" in url:
        p["problems"] = ["no raw scan asset"]
        return p
    src = os.path.join(work_dir(kind), f"{item['id']}.tif")
    dst = os.path.join(work_dir(kind), f"{item['id']}.reduced.tif")
    try:
        if title_on_commons(kind, num):
            p["already_on_commons"] = True
            return p
        wait_for_disk(kind)
        p["source_sha1"], p["source_bytes"] = download(url, src)
        level, size, source_size = built_in_level(src)
        with tifffile.TiffFile(src) as t:
            copyable = t.pages[level].compression.name == "JPEG" or level == 0
        if copyable:
            strip_overviews(src, dst, level=level)
            verify(src, dst, level=level)
        else:
            reduce_scan(src, dst, size)
        os.remove(src)
    except Exception as e:
        p.update(problems=[f"file check failed: {type(e).__name__}: {e}"])
        for f in (src, dst, src + ".part"):
            if os.path.exists(f):
                os.remove(f)
        return p
    try:
        built = more.build_aerial(kind, {**item, "meta": meta},
                                  reduced=dict(size=size, source_size=source_size, level=level, resized=not copyable))
    except Exception as e:  # odd record: set aside for review
        os.remove(dst)
        p["problems"] = [f"build failed: {type(e).__name__}: {e}"]
        return p
    p.update(built, item_id=item["id"], kind=kind, request=REQUEST, stripped=dst, verified=True,
             reduced_size=list(size), source_size=list(source_size), reduced_level=None if not copyable else level,
             reduced_bytes=os.path.getsize(dst),
             comment=f"Upload swisstopo {KINDS[kind]['label']} {built['num']}, reduced-resolution copy ([[{REQUEST}]])")
    problems = []
    if BAD_TITLE_CHARS.search(p["title"][5:]):
        problems.append(f"file name has a character not allowed in titles: {p['title']}")
    mb = p["reduced_bytes"] / 2 ** 20
    if not REDUCED_MB[0] <= mb <= REDUCED_MB[1]:
        problems.append(f"reduced copy size: {mb:.1f} MB, outside {REDUCED_MB[0]}-{REDUCED_MB[1]} MB")
    try:
        problems += dry_run(p["title"], p["text"], [c["name"] for c in p["categories"]])
    except Exception as e:
        problems.append(f"file check failed: dry run: {type(e).__name__}: {e}")
    p["problems"] = problems
    if problems and os.path.exists(dst):
        os.remove(dst)
    return p


def retryable(p):
    """Set aside only because a build or check failed (not uploaded, not a duplicate)."""
    probs = p.get("problems") or []
    return (not p.get("uploaded") and not p.get("already_on_commons") and bool(probs)
            and all(x.startswith(("build failed", "file check failed")) or x == DEFERRED for x in probs))


def batch_files(kind):
    return sorted(glob.glob(os.path.join(HERE, f"{kind}[0-9][0-9][0-9].json")))


def prepare(kind, out_path, count):
    os.makedirs(work_dir(kind), exist_ok=True)
    claimed, attempts = set(SAMPLES), {}
    for path in batch_files(kind):
        if os.path.abspath(path) == os.path.abspath(out_path):
            continue
        for p in json.load(open(path, encoding="utf-8")):
            if retryable(p):
                attempts[p["item_id"]] = attempts.get(p["item_id"], 0) + 1
            else:
                claimed.add(p["item_id"])
    retry = {i for i, k in attempts.items() if i not in claimed and k < MAX_ATTEMPTS}
    claimed.update(attempts)
    by_id = {it["id"]: it for it in items(kind)}
    todo = [by_id[i] for i in by_id if i in retry][:count]
    print(f"{len(todo)} earlier set-aside files retried", flush=True)
    todo += [it for i, it in by_id.items() if i not in claimed][:count - len(todo)]

    pages, done = [None] * len(todo), 0
    with ThreadPoolExecutor(KINDS[kind]["workers"]) as pool:
        futures = {pool.submit(prepare_one, kind, it): k for k, it in enumerate(todo)}
        for fut in as_completed(futures):
            p = pages[futures[fut]] = fut.result()
            done += 1
            print(f"{done}/{len(todo)} {p['title'] or p['item_id']} | duplicate: {p['already_on_commons']}"
                  f" | verified: {p['verified']} | problems: {p['problems']}", flush=True)
    tmp = out_path + ".tmp"
    json.dump(pages, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, out_path)
    print(f"prepared {len(pages)}; not ready: {[p['item_id'] for p in pages if not ready(p)]}", flush=True)


def ready(p):
    return p.get("verified") and not p.get("problems") and not p.get("already_on_commons")


def is_deferred(p):
    return any(x.startswith("deferred:") for x in p.get("problems") or [])


def data_error(p):
    """Set aside because of the record itself (date, place): a ValueError from the page builder."""
    probs = p.get("problems") or []
    return bool(probs) and all(x.startswith("build failed: ValueError") for x in probs)


# ---------------------------------------------------------------- the unattended run

LOG = ["collections.log"]   # --log: one file per job when two jobs run side by side


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M')} {msg}"
    print(line, flush=True)
    with open(os.path.join(HERE, LOG[0]), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def run_step(args, logfile):
    with open(logfile, "a", encoding="utf-8") as f:
        return subprocess.run([PY, "-u", *args], stdout=f, stderr=subprocess.STDOUT, cwd=HERE,
                              env={**os.environ, "PYTHONIOENCODING": "utf-8"}).returncode


def start_prepare(kind, name):
    if os.path.exists(os.path.join(HERE, f"{name}.json")):
        return None  # already prepared (written atomically at the end)
    f = open(os.path.join(HERE, f"{name}.log"), "a", encoding="utf-8")
    return subprocess.Popen([PY, "-u", __file__, "prepare", kind, f"{name}.json", str(KINDS[kind]["size"])],
                            stdout=f, stderr=subprocess.STDOUT, cwd=HERE,
                            env={**os.environ, "PYTHONIOENCODING": "utf-8"})


def watch(procs, logs):
    """Wait for the upload processes; stop any that writes nothing for HUNG_AFTER seconds (its progress
    file keeps what it did). Returns the exit codes (a stopped process counts as failed)."""
    while any(p.poll() is None for p in procs):
        time.sleep(30)
        for p, path in zip(procs, logs):
            if p.poll() is None and time.time() - os.path.getmtime(path) > HUNG_AFTER:
                log(f"{path}: no output for {HUNG_AFTER // 60} min; stopping that process")
                p.kill()
                p.wait()
    return [p.returncode for p in procs]


class Stop(Exception):
    pass


def run_kind(kind, max_batches, shards, interval):
    """Batches of one collection until none is left (or max_batches); returns the number of files uploaded."""
    total, n, k, prep = 0, 0, 1, None
    name_of = lambda k: f"{kind}{k:03d}"
    done = lambda k: os.path.exists(os.path.join(HERE, f"{name_of(k)}.done"))
    try:
        while n < max_batches:
            while done(k):
                k += 1
            name = name_of(k)
            path = os.path.join(HERE, f"{name}.json")
            if prep is None:
                prep = start_prepare(kind, name)
            if prep is not None:
                if prep.wait() != 0:
                    raise Stop(f"{name}: preparation failed (see {name}.log)")
            prep = None
            pages = json.load(open(path, encoding="utf-8"))
            if not pages:
                os.remove(path)
                log(f"{kind}: no files left")
                break
            dup = sum(1 for p in pages if p.get("already_on_commons"))
            data = sum(1 for p in pages if data_error(p))
            deferred = sum(1 for p in pages if is_deferred(p))
            other = sum(1 for p in pages if not ready(p) and not p.get("already_on_commons") and not data_error(p)
                        and not is_deferred(p))
            log(f"{name}: prepared {len(pages)}, ready {sum(map(ready, pages))}, duplicates {dup},"
                f" record errors {data}, deferred {deferred}, other set aside {other}")
            if not any(map(ready, pages)):     # nothing to upload (all deferred or duplicates)
                open(os.path.join(HERE, f"{name}.done"), "w").write(time.strftime("%Y-%m-%d %H:%M\n"))
                log(f"{name}: done, nothing to upload")
                n, k = n + 1, k + 1
                continue
            if other / len(pages) > MAX_SET_ASIDE:
                raise Stop(f"{name}: {other} of {len(pages)} set aside, above {MAX_SET_ASIDE:.0%}; review {name}.json")
            if data / len(pages) > MAX_DATA_ERRORS:
                raise Stop(f"{name}: {data} of {len(pages)} record errors, above {MAX_DATA_ERRORS:.0%}; review {name}.json")

            if n + 1 < max_batches:          # prepare the next batch while this one uploads
                prep = start_prepare(kind, name_of(k + 1))

            if run_step(["create_categories.py", f"{name}.json"], f"{name}.log") != 0:
                raise Stop(f"{name}: creating categories failed (see {name}.log)")
            logs = [os.path.join(HERE, f"{name}.upload-{i}.log") for i in range(shards)]
            procs = [subprocess.Popen([PY, "-u", "upload.py", f"{name}.json", "--shard", f"{i}/{shards}",
                                       "--edit-interval", str(interval), "--delete-uploaded"],
                                      stdout=open(logs[i], "a", encoding="utf-8"), stderr=subprocess.STDOUT, cwd=HERE,
                                      env={**os.environ, "PYTHONIOENCODING": "utf-8"}) for i in range(shards)]
            codes = watch(procs, logs)
            run_step(["upload.py", f"{name}.json", "--merge"], f"{name}.log")
            retries = sum(open(f, encoding="utf-8").read().count("transient server error") for f in logs)
            if any(codes):
                raise Stop(f"{name}: an upload process stopped (exit codes {codes}); see {name}.upload-*.log")

            pages = json.load(open(path, encoding="utf-8"))
            up = sum(1 for p in pages if p.get("uploaded") and p.get("sdc_done"))
            if up != sum(map(ready, pages)):
                raise Stop(f"{name}: {up} of {sum(map(ready, pages))} ready files uploaded with structured data")
            run_step(["check_thumbs.py", f"{name}.json"], f"{name}.log")
            previews = re.findall(r"^previews: (\d+)/(\d+)", open(os.path.join(HERE, f"{name}.log"), encoding="utf-8").read(), re.M)
            if not previews or previews[-1][0] != previews[-1][1]:
                raise Stop(f"{name}: previews {previews[-1] if previews else '?'} render; see 'no preview' lines in {name}.log")
            for p in pages:
                if p.get("stripped") and os.path.exists(p["stripped"]):
                    os.remove(p["stripped"])
            open(os.path.join(HERE, f"{name}.done"), "w").write(time.strftime("%Y-%m-%d %H:%M\n"))
            total += up
            n += 1
            log(f"{name}: done, {up} uploaded with structured data, {len(pages) - up} not uploaded"
                f" ({dup} duplicates), {retries} transient retries, previews {previews[-1][0]}/{previews[-1][1]}")
            k += 1
    except Stop:
        if prep is not None and prep.poll() is None:
            prep.kill()   # its downloads are .part files or complete ones that the next run reuses
        raise
    if prep is not None:
        prep.wait()
    return total


def clean_orphans(kind):
    """At the start of a run: delete files in this collection's work folder that no pending batch needs (left
    behind when a job was stopped mid-batch). Otherwise they count against DISK_LIMIT and the run waits forever."""
    needed = {os.path.abspath(p["stripped"]) for f in batch_files(kind)
              for p in json.load(open(f, encoding="utf-8"))
              if p.get("stripped") and not (p.get("uploaded") and p.get("sdc_done"))}
    folder = work_dir(kind)
    gone = 0
    for name in os.listdir(folder) if os.path.isdir(folder) else []:
        path = os.path.abspath(os.path.join(folder, name))
        if path not in needed and os.path.isfile(path):
            os.remove(path)
            gone += 1
    if gone:
        log(f"{kind}: removed {gone} leftover work files")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "prepare":
        prepare(sys.argv[2], sys.argv[3], int(sys.argv[4]))
        return
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run"])
    ap.add_argument("kinds", nargs="+", choices=list(KINDS))
    ap.add_argument("--batches", type=int, default=100000, help="at most this many batches per collection")
    ap.add_argument("--shards", type=int, default=4)
    ap.add_argument("--log", default="collections.log", help="summary log file (one per job)")
    ap.add_argument("--size", type=int, help="files per batch (default: the collection's own size)")
    ap.add_argument("--edit-interval", type=float, default=1.0)
    a = ap.parse_args()
    LOG[0] = a.log
    if a.size:
        for k in a.kinds:
            KINDS[k]["size"] = a.size
    totals = {}
    try:
        for kind in a.kinds:      # this job's own collections only: another job may be preparing other kinds
            clean_orphans(kind)
        for kind in a.kinds:
            log(f"{kind}: starting")
            totals[kind] = run_kind(kind, a.batches, a.shards, a.edit_interval)
            log(f"{kind}: finished, {totals[kind]} files uploaded in this run")
    except Stop as e:
        log(f"RESULT stopped: {e}")
        sys.exit(1)
    log("RESULT ok: " + ", ".join(f"{k} {v} files" for k, v in totals.items()))


if __name__ == "__main__":
    main()
