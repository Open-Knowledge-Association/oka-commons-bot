"""Run a pipeline command again after it stops, so a temporary outage does not end the upload.

The pipelines resume exactly where they stopped, so a restart costs nothing. After a stop the command is rerun
WAIT seconds later. If GIVE_UP seconds pass without a single batch finishing (no new "done," line in the log),
the supervisor gives up, so a problem that needs review does not loop forever. Every restart is logged.

Usage: python keep_running.py LOGFILE -- python -u collection_pipeline.py run technical ...
(2026-10-20/21: a connection outage between Toolforge and Commons stopped both jobs for six days.)
"""
import subprocess
import sys
import time

WAIT = 30 * 60
GIVE_UP = 24 * 3600


def batches_done(log):
    try:
        return sum(1 for line in open(log, encoding="utf-8") if "done," in line)
    except FileNotFoundError:
        return 0


def note(log, msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M')} supervisor: {msg}"
    print(line, flush=True)
    with open(log, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main():
    log, cmd = sys.argv[1], sys.argv[sys.argv.index("--") + 1:]
    last_progress, done = time.time(), batches_done(log)
    while True:
        code = subprocess.call(cmd)
        if code == 0:
            note(log, "pipeline finished")
            return
        now_done = batches_done(log)
        if now_done > done:
            last_progress, done = time.time(), now_done
        if time.time() - last_progress > GIVE_UP:
            note(log, f"no batch finished for {GIVE_UP // 3600} h; giving up (exit code {code})")
            sys.exit(code)
        note(log, f"pipeline stopped (exit code {code}); restarting in {WAIT // 60} min")
        time.sleep(WAIT)


if __name__ == "__main__":
    main()
