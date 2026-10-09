"""Run many swisstopo batches unattended, preparing the next batch while the current one uploads.

For each batch NNN (default 1000 images):
  prepare (8 images in parallel) -> upload with N parallel processes (each at most one edit per
  --edit-interval seconds) -> merge progress -> check every preview renders -> link stereo partners ->
  refresh uploads.csv -> delete the local files of uploaded images.
Stops for review on: a failed step, any upload process stopping, any preview that does not render,
or more than MAX_SET_ASIDE of a batch set aside. Progress is kept, so rerunning resumes.

Usage: python pipeline.py --start 3 --batches 10 [--size 1000] [--shards 4] [--edit-interval 1]
Log: pipeline.log (one line per step); details in batchNNN.log. Last line starts with RESULT.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

MAX_SET_ASIDE = 0.05
MAX_DATA_ERRORS = 0.20  # heading mismatches (errors in swisstopo's records)
PY = sys.executable


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M')} {msg}"
    print(line, flush=True)
    with open("pipeline.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def run(args, logfile):
    with open(logfile, "a", encoding="utf-8") as f:
        return subprocess.run([PY, "-u", *args], stdout=f, stderr=subprocess.STDOUT,
                              env={**os.environ, "PYTHONIOENCODING": "utf-8"}).returncode


def start_prepare(name, size):
    if os.path.exists(f"{name}.json"):
        return None  # already prepared (the file is written atomically at the end)
    f = open(f"{name}.log", "a", encoding="utf-8")
    return subprocess.Popen([PY, "-u", "prepare_batch.py", f"{name}.json", str(size)], stdout=f,
                            stderr=subprocess.STDOUT, env={**os.environ, "PYTHONIOENCODING": "utf-8"})


def stop(msg):
    log(f"RESULT stopped: {msg}")
    sys.exit(1)


HUNG_AFTER = 20 * 60  # seconds without any log output before an upload process counts as hung


def watch(procs, logs):
    """Wait for the upload processes; stop any that writes nothing for HUNG_AFTER seconds.

    A stopped process loses nothing: its progress file lets the next run continue where it was.
    Returns the exit codes (a stopped process counts as failed).
    """
    while any(p.poll() is None for p in procs):
        time.sleep(30)
        for p, path in zip(procs, logs):
            if p.poll() is None and time.time() - os.path.getmtime(path) > HUNG_AFTER:
                log(f"{path}: no output for {HUNG_AFTER // 60} min; stopping that process")
                p.kill()
                p.wait()
    return [p.returncode for p in procs]


def keep_awake():
    """Ask Windows not to sleep while this process runs (released automatically when it exits)."""
    if sys.platform == "win32":
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)


def main():
    keep_awake()
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, required=True)
    ap.add_argument("--batches", type=int, required=True)
    ap.add_argument("--size", type=int, default=1000)
    ap.add_argument("--shards", type=int, default=4)
    ap.add_argument("--edit-interval", type=float, default=1.0)
    a = ap.parse_args()
    names = [f"batch{k:03d}" for k in range(a.start, a.start + a.batches)]
    total_up = 0

    prep = start_prepare(names[0], a.size)
    for idx, name in enumerate(names):
        if prep is not None:
            log(f"{name}: waiting for preparation")
            if prep.wait() != 0:
                stop(f"{name}: preparation failed (see {name}.log)")
        pages = json.load(open(f"{name}.json", encoding="utf-8"))
        held = [p["num"] for p in pages if p.get("already_on_commons") or not p.get("verified") or p.get("problems")]
        # data errors in single records (heading vs photographed area) are set aside safely and counted apart;
        # the 5% limit is for failures that point at a problem in the bot itself
        data = [p["num"] for p in pages if p.get("problems") and all(x.startswith("heading ") for x in p["problems"])]
        other = len(held) - len(data)
        log(f"{name}: prepared {len(pages)}, set aside {len(held)} ({len(data)} heading mismatches)")
        if len(pages) and other / len(pages) > MAX_SET_ASIDE:
            stop(f"{name}: {other} of {len(pages)} set aside, above {MAX_SET_ASIDE:.0%}; review {name}.json")
        if len(pages) and len(data) / len(pages) > MAX_DATA_ERRORS:
            stop(f"{name}: {len(data)} of {len(pages)} heading mismatches, above {MAX_DATA_ERRORS:.0%}; review {name}.json")

        # prepare the next batch while this one uploads
        prep = start_prepare(names[idx + 1], a.size) if idx + 1 < len(names) else None

        if run(["create_categories.py", f"{name}.json"], f"{name}.log") != 0:
            stop(f"{name}: creating categories failed (see {name}.log)")
        log(f"{name}: uploading with {a.shards} processes")
        procs = [subprocess.Popen([PY, "-u", "upload.py", f"{name}.json", "--shard", f"{i}/{a.shards}",
                                   "--edit-interval", str(a.edit_interval)],
                                  stdout=open(f"{name}.upload-{i}.log", "a", encoding="utf-8"),
                                  stderr=subprocess.STDOUT, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
                 for i in range(a.shards)]
        codes = watch(procs, [f"{name}.upload-{i}.log" for i in range(a.shards)])
        run(["upload.py", f"{name}.json", "--merge"], f"{name}.log")
        retries = sum(open(f"{name}.upload-{i}.log", encoding="utf-8").read().count("transient server error")
                      for i in range(a.shards))
        if any(codes):
            if prep:
                prep.wait()
            stop(f"{name}: an upload process stopped (exit codes {codes}); see {name}.upload-*.log")

        pages = json.load(open(f"{name}.json", encoding="utf-8"))
        up = sum(1 for p in pages if p.get("uploaded") and p.get("sdc_done"))
        total_up += up

        run(["check_thumbs.py", f"{name}.json"], f"{name}.log")
        previews = re.findall(r"^previews: (\d+)/(\d+)", open(f"{name}.log", encoding="utf-8").read(), re.M)
        if not previews or previews[-1][0] != previews[-1][1]:
            if prep:
                prep.wait()
            stop(f"{name}: previews {previews[-1] if previews else '?'} render; see 'no preview' lines in {name}.log")

        if run(["link_partners.py"], f"{name}.log") != 0:
            stop(f"{name}: partner linking failed")
        if run(["make_manifest.py", "uploads.csv"], f"{name}.log") != 0:
            stop(f"{name}: list update failed")
        for p in pages:
            if p.get("uploaded") and p.get("sdc_done") and p.get("stripped") and os.path.exists(p["stripped"]):
                os.remove(p["stripped"])
        log(f"{name}: done, {up} uploaded with structured data, {len(held)} set aside, {retries} transient retries,"
            f" previews {previews[-1][0]}/{previews[-1][1]}")

    log(f"RESULT ok: {len(names)} batches, {total_up} files uploaded")


if __name__ == "__main__":
    main()
