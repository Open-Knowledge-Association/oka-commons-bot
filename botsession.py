"""Log in to Commons as OKA bot with a bot password taken from the environment.

COMMONS_BOT_USER ("OKA bot@<bot password name>") and COMMONS_BOT_PASSWORD (the
Special:BotPasswords password) come from the environment (on Windows also from the
user's registry). They are written to a temporary Pywikibot directory for the run
and deleted afterwards; they are never stored in this repository.
"""
import contextlib
import json
import os
import shutil
import sys
import tempfile

try:
    import winreg
except ImportError:  # not Windows
    winreg = None


def user_env(name):
    """Read a credential from the process environment, or on Windows from the user's registry
    (which also sees variables set after the shell started)."""
    if os.environ.get(name):
        return os.environ[name]
    if winreg is None:
        sys.exit(f"environment variable {name} is not set")
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
        return winreg.QueryValueEx(key, name)[0]


@contextlib.contextmanager
def commons_site():
    login, password = user_env("COMMONS_BOT_USER"), user_env("COMMONS_BOT_PASSWORD")
    if "@" not in login or len(password) != 32:
        sys.exit("COMMONS_BOT_USER/COMMONS_BOT_PASSWORD are not a Special:BotPasswords pair; refusing to log in.")
    user, bp_name = login.split("@", 1)
    workdir = tempfile.mkdtemp(prefix="pwb-")
    try:
        with open(os.path.join(workdir, "user-config.py"), "w", encoding="utf-8") as f:
            f.write(f"family = 'commons'\nmylang = 'commons'\nusernames['commons']['commons'] = {user!r}\n"
                    "password_file = 'user-password.py'\nput_throttle = 5\nmaxlag = 5\n"
                    "user_agent_description = 'OKA bot swisstopo uploads; operator User:7804j'\n")
        with open(os.path.join(workdir, "user-password.py"), "w", encoding="utf-8") as f:
            f.write(f"({user!r}, BotPassword({bp_name!r}, {password!r}))\n")
        os.environ["PYWIKIBOT_DIR"] = workdir
        import pywikibot

        site = pywikibot.Site("commons", "commons")
        site.login()
        if site.username() != user:
            sys.exit(f"logged in as {site.username()!r}, expected {user!r}")
        yield site
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


TRANSIENT_ERRORS = ("ShellboxError", "backend-fail")  # Commons storage/processing hiccups
TRANSIENT_RETRIES = 2


def upload_with_retry(site, title, filename, expected_sha1, **kwargs):
    """Upload (or upload a new version), retrying transient server errors after 60 s.

    Before each retry, checks whether the failed request stored the file after all.
    Returns True when Commons holds exactly expected_sha1, False on a warning/failure.
    """
    import time
    import pywikibot

    for attempt in range(1 + TRANSIENT_RETRIES):
        try:
            ok = site.upload(pywikibot.FilePage(site, title), source_filename=filename, report_success=False, **kwargs)
            break
        except pywikibot.exceptions.APIError as e:
            if not any(t in e.code for t in TRANSIENT_ERRORS) or attempt == TRANSIENT_RETRIES:
                raise
            print(f"{title}: transient server error {e.code}; retrying in 60 s", flush=True)
            time.sleep(60)
            page = pywikibot.FilePage(site, title)
            if page.exists() and page.latest_file_info.sha1 == expected_sha1:
                ok = True
                break
    return bool(ok) and pywikibot.FilePage(site, title).latest_file_info.sha1 == expected_sha1


def write_structured_data(site, title, sdc, summary):
    """Add captions and statements to a file's MediaInfo entity, skipping properties it already has.

    Returns the list of properties added.
    """
    import pywikibot

    page = pywikibot.FilePage(site, title)
    mid = f"M{page.pageid}"
    current = site.simple_request(action="wbgetentities", ids=mid).submit()["entities"][mid]
    have_props = set((current.get("statements") or {}).keys())
    have_labels = set((current.get("labels") or {}).keys())
    claims = [c for c in sdc["claims"] if c["mainsnak"]["property"] not in have_props]
    labels = {k: v for k, v in sdc["labels"].items() if k not in have_labels}
    if not claims and not labels:
        return []
    data = {"claims": claims, "labels": labels}
    site.simple_request(action="wbeditentity", id=mid, data=json.dumps(data), summary=summary, bot=1,
                        token=site.tokens["csrf"]).submit()
    after = site.simple_request(action="wbgetentities", ids=mid).submit()["entities"][mid]
    missing = {c["mainsnak"]["property"] for c in claims} - set((after.get("statements") or {}).keys())
    if missing:
        raise RuntimeError(f"{title}: structured data not saved for {sorted(missing)}")
    return sorted({c["mainsnak"]["property"] for c in claims}) + [f"caption:{k}" for k in labels]
