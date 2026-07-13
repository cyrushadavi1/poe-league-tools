#!/usr/bin/env python3
"""Downloads the Exile-UI zone-layout image pack for the overlay.

The pack is ~470 hand-traced campaign zone layouts (white = zone
boundary, green = path to the exit, purple = waypoint) maintained on
the `layouts` branch of https://github.com/Lailloken/Exile-UI (MIT).
Filenames follow '<areaID> <variant>.jpg' where areaID matches the
'Generating level N area "<areaID>"' line in Client.txt.

    python tools/fetch_layouts.py            # fetch/update the pack
    python tools/fetch_layouts.py --check    # is an update available?

Installs into overlay/assets/layouts/ (zones/*.jpg + version.json +
ATTRIBUTION.md). Run it once before league start; the overlay works
fine without the pack (the layouts panel just stays hidden).
"""
import argparse
import io
import json
import os
import shutil
import tarfile
import tempfile
import time
import urllib.error
import urllib.request

REPO = "Lailloken/Exile-UI"
BRANCH = "layouts"                      # PoE1 pack ('layouts_2' is PoE2)
TARBALL_URL = f"https://github.com/{REPO}/archive/refs/heads/{BRANCH}.tar.gz"
VERSION_URL = (f"https://raw.githubusercontent.com/{REPO}/refs/heads/"
               f"{BRANCH}/version.json")

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DEST = os.path.join(HERE, "..", "overlay", "assets", "layouts")

# INTERFACES.md invariant 3: identify ourselves, honor Retry-After,
# never faster than 1 request / 2 seconds.
USER_AGENT = "poe-league-tools/1.0 (contact: cyrus@hadavi.net)"
MIN_REQUEST_GAP_S = 2.0

ATTRIBUTION = """\
# Zone layout images

Source: https://github.com/Lailloken/Exile-UI (branch `layouts`)
License: MIT (c) Lailloken and contributors
Fetched by: tools/fetch_layouts.py

White = zone boundary, green = path toward the exit, purple square =
waypoint. Filenames are '<areaID> <variant>.jpg'; the areaID matches
the 'Generating level N area "<areaID>"' line in the game's Client.txt.
"""

_last_request = [0.0]


def _get(url, say=print):
    """GET with the project UA, Retry-After support and the 2 s floor."""
    wait = MIN_REQUEST_GAP_S - (time.monotonic() - _last_request[0])
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in (1, 2, 3):
        _last_request[0] = time.monotonic()
        try:
            return urllib.request.urlopen(req, timeout=60).read()
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 3:
                raise
            delay = max(float(e.headers.get("Retry-After") or 5),
                        MIN_REQUEST_GAP_S)
            say(f"   rate-limited, retrying in {delay:.0f}s")
            time.sleep(delay)


def safe_member(name):
    """Tarball member -> pack-relative path, or None to skip.

    Strips the 'Exile-UI-layouts/' top dir and keeps only the files the
    overlay uses. Rejects anything that could escape the destination
    (absolute paths, '..' components) -- archive contents are untrusted.
    """
    parts = name.split("/")[1:]                 # drop the top-level dir
    if not parts or any(p in ("", ".", "..") for p in parts):
        return None
    rel = "/".join(parts)
    if os.path.isabs(rel) or ":" in rel.split("/")[0]:
        return None
    if rel in ("version.json", "file-list.json"):
        return rel
    if (rel.startswith("zones/") and rel.count("/") == 1
            and rel.lower().endswith((".jpg", ".png"))):
        return rel
    return None


def local_version(dest):
    try:
        with open(os.path.join(dest, "version.json"), encoding="utf-8") as f:
            return json.load(f).get("version")
    except (OSError, ValueError):
        return None


def check(dest, say=print):
    """Compare the installed pack version against the live branch."""
    have = local_version(dest)
    try:
        live = json.loads(_get(VERSION_URL, say)).get("version")
    except (urllib.error.URLError, ValueError, OSError) as e:
        say(f"!! could not reach GitHub: {e}")
        return 2
    if have is None:
        say(f"no pack installed at {dest} -- run tools/fetch_layouts.py")
        return 1
    if live is not None and live > have:
        say(f"update available: v{have} -> v{live} "
            "(re-run tools/fetch_layouts.py)")
        return 1
    say(f"layout pack v{have} is up to date")
    return 0


def fetch(dest, say=print):
    say(f"fetching {TARBALL_URL} ...")
    data = _get(TARBALL_URL, say)
    say(f"   {len(data) / 1e6:.1f} MB downloaded, extracting ...")

    staging = tempfile.mkdtemp(prefix=".layouts-", dir=os.path.dirname(dest)
                               if os.path.isdir(os.path.dirname(dest))
                               else None)
    count = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            for member in tar:
                if not member.isfile():
                    continue
                rel = safe_member(member.name)
                if not rel:
                    continue
                out = os.path.join(staging, *rel.split("/"))
                os.makedirs(os.path.dirname(out), exist_ok=True)
                src = tar.extractfile(member)
                with open(out, "wb") as f:
                    shutil.copyfileobj(src, f)
                count += rel.startswith("zones/")
        if not count:
            raise RuntimeError("archive had no zones/*.jpg -- did the "
                               "branch layout change?")
        with open(os.path.join(staging, "ATTRIBUTION.md"), "w",
                  encoding="utf-8") as f:
            f.write(ATTRIBUTION)

        # Swap in atomically-ish: the overlay never sees a half-pack.
        if os.path.isdir(dest):
            old = dest + ".old"
            shutil.rmtree(old, ignore_errors=True)
            os.replace(dest, old)
            os.replace(staging, dest)
            shutil.rmtree(old, ignore_errors=True)
        else:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            os.replace(staging, dest)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    say(f"installed {count} layout images (pack v{local_version(dest)}) "
        f"-> {os.path.relpath(dest, os.getcwd())}")
    say("the overlay picks them up on next launch")
    return 0


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dest", default=DEFAULT_DEST,
                    help="install dir (default overlay/assets/layouts)")
    ap.add_argument("--check", action="store_true",
                    help="only report whether an update is available")
    a = ap.parse_args()
    dest = os.path.abspath(a.dest)
    raise SystemExit(check(dest) if a.check else fetch(dest))


if __name__ == "__main__":
    main()
