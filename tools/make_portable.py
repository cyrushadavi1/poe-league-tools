#!/usr/bin/env python3
"""Build the zero-install PC bundle -- runs on the Mac (or anywhere).

    .venv/bin/python tools/make_portable.py            -> dist/poe-league-tools-pc.zip

The zip is the whole toolkit plus a private `python\\` dir: python.org's
official *Windows embeddable* CPython (~11 MB) with the PyQt6 Windows
wheels pre-extracted into its site-packages. A friend unzips, runs
setup_pc.bat, and the .bat files prefer the bundled interpreter -- no
Python install, no admin rights, no PATH edits, no pip, no internet
after the unzip. (PyInstaller-style single .exe was rejected: it cannot
cross-build, and this project's build machine is a Mac -- DECISIONS.md.)

Network is used at BUILD time only (python.org + PyPI), cached under
dist/cache/ so rebuilds are offline. Wheels are fetched with
`pip download --platform win_amd64` -- downloading, never executing,
foreign-platform code. PyQt6's sip wheel is cpXY-specific, so
EMBED_VERSION's major.minor must stay in sync with --python-version
passed to pip (handled here automatically).

Bump EMBED_VERSION deliberately; it is pinned so bundles are
reproducible, not to chase every patch release.
"""
import argparse
import fnmatch
import glob
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

EMBED_VERSION = "3.13.14"          # verified live 2026-07-07
EMBED_URL = ("https://www.python.org/ftp/python/{v}/"
             "python-{v}-embed-amd64.zip")
BUNDLE_NAME = "poe-league-tools-pc"

# Shipped = the repo, minus dev/cache/output junk. builds/ and
# party.json are gitignored (personal data) but MUST ship -- the whole
# point is that the friend's PC sets itself up from the party bundle.
EXCLUDE_NAMES = {".venv", ".git", "__pycache__", ".DS_Store", "dist",
                 "runs", "fake_client.txt", "llm_usage.jsonl"}
EXCLUDE_RELPATHS = {os.path.join("market", "market.db"),
                    os.path.join("data", "wiki_cache")}
EXCLUDE_GLOBS = ["verify_act*.json", "exposure_*.md", "*.pyc"]


def read_pyqt_requirement(req_path):
    """The PyQt6 line out of requirements.txt (comments stripped), so
    the bundle always matches what setup_pc.bat would pip-install."""
    with open(req_path, encoding="utf-8") as f:
        for line in f:
            spec = line.split("#", 1)[0].strip()
            if spec.lower().startswith("pyqt6") and "pyqt6-" not in spec.lower():
                return spec
    raise SystemExit(f"no PyQt6 requirement found in {req_path}")


def pth_content(embed_version):
    """Body for python3XX._pth -- the embeddable's sys.path manifest.

    A ._pth file takes FULL control of sys.path (script-dir insertion
    included), so every package dir the repo's entry points import from
    by-same-directory must be listed explicitly. Paths are relative to
    the python/ dir; site stays disabled (nothing needs site.py).
    """
    nodot = "".join(embed_version.split(".")[:2])
    return "\n".join([
        f"python{nodot}.zip",
        ".",
        r"Lib\site-packages",
        "..",              # repo root: tools.*, llm.*, craft.*
        r"..\overlay",     # client_watcher & co (overlay/main.py)
        r"..\buildgen",    # pob (buildgen/party.py)
        r"..\market",      # store & co (market/console.py, daemon.py)
        r"..\advisor",     # summarize & co
        "#import site",
        "",
    ])


def _skip(name, rel):
    if name in EXCLUDE_NAMES or rel in EXCLUDE_RELPATHS:
        return True
    return any(fnmatch.fnmatch(name, g) for g in EXCLUDE_GLOBS)


def assemble_tree(repo_root, dest):
    """Copy the shippable subset of the repo into dest."""
    def ignore(dirpath, names):
        reldir = os.path.relpath(dirpath, repo_root)
        reldir = "" if reldir == "." else reldir
        return {n for n in names if _skip(n, os.path.join(reldir, n))}

    shutil.copytree(repo_root, dest, ignore=ignore)


def extract_wheels(wheel_paths, site_packages):
    """Unzip wheels into site-packages. A wheel is a zip laid out
    exactly as it installs (PyQt6 has no scripts/postinstall), so
    extraction IS installation for this use."""
    os.makedirs(site_packages, exist_ok=True)
    for whl in sorted(wheel_paths):
        with zipfile.ZipFile(whl) as z:
            z.extractall(site_packages)


def download(url, dest, say=print):
    if os.path.exists(dest):
        say(f"   cached: {os.path.basename(dest)}")
        return dest
    say(f"   fetching {url}")
    req = urllib.request.Request(
        url, headers={"User-Agent":
                      "poe-league-tools/1.0 (contact: cyrus@hadavi.net)"})
    tmp = dest + ".part"
    with urllib.request.urlopen(req) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f)
    os.replace(tmp, dest)
    return dest


def pip_download_wheels(requirement, pyver, cache_dir, say=print):
    """Windows wheels for `requirement` into cache_dir (skips ones
    already there); returns their paths."""
    major_minor = ".".join(pyver.split(".")[:2])
    say(f"   pip download {requirement} (win_amd64, py{major_minor})")
    subprocess.run(
        [sys.executable, "-m", "pip", "download", requirement,
         "--platform", "win_amd64", "--python-version", major_minor,
         "--only-binary=:all:", "--dest", cache_dir, "-q"],
        check=True)
    return glob.glob(os.path.join(cache_dir, "*.whl"))


def zip_tree(src_dir, zip_path, say=print):
    """dist/<name>.zip with everything under a single top-level folder
    (friends double-click 'Extract All'; a tarbomb would be rude)."""
    top = os.path.basename(src_dir)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=6) as z:
        for dirpath, _dirs, files in os.walk(src_dir):
            for name in files:
                full = os.path.join(dirpath, name)
                z.write(full, os.path.join(
                    top, os.path.relpath(full, src_dir)))
    say(f"   {zip_path}  "
        f"({os.path.getsize(zip_path) / 1024 / 1024:.0f} MB)")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--python-version", default=EMBED_VERSION,
                    help=f"embeddable CPython version (default "
                         f"{EMBED_VERSION})")
    ap.add_argument("--out", default=os.path.join(ROOT, "dist"))
    ap.add_argument("--no-zip", action="store_true",
                    help="stop after assembling the folder (faster "
                         "while iterating)")
    a = ap.parse_args(argv)

    pyver = a.python_version
    cache = os.path.join(a.out, "cache")
    bundle = os.path.join(a.out, BUNDLE_NAME)
    os.makedirs(cache, exist_ok=True)

    print(f"== portable PC bundle (python {pyver} embeddable) ==")

    print("1. downloads (cached in dist/cache)")
    embed_zip = download(EMBED_URL.format(v=pyver),
                         os.path.join(cache,
                                      f"python-{pyver}-embed-amd64.zip"))
    requirement = read_pyqt_requirement(
        os.path.join(ROOT, "requirements.txt"))
    wheels = pip_download_wheels(requirement, pyver, cache)
    for w in sorted(wheels):
        print(f"   wheel: {os.path.basename(w)}")

    print("2. assembling the folder")
    if os.path.exists(bundle):
        shutil.rmtree(bundle)
    assemble_tree(ROOT, bundle)

    pydir = os.path.join(bundle, "python")
    with zipfile.ZipFile(embed_zip) as z:
        z.extractall(pydir)
    extract_wheels(wheels, os.path.join(pydir, "Lib", "site-packages"))

    # rewrite the ._pth (found by glob -- its name tracks the version)
    pths = glob.glob(os.path.join(pydir, "*._pth"))
    if len(pths) != 1:
        raise SystemExit(f"expected exactly one ._pth in {pydir}, "
                         f"found {pths}")
    with open(pths[0], "w", encoding="utf-8", newline="\r\n") as f:
        f.write(pth_content(pyver))

    # build stamp, for "which bundle is this?" questions in the chat
    with open(os.path.join(bundle, "PORTABLE.json"), "w",
              encoding="utf-8") as f:
        json.dump({"python": pyver,
                   "wheels": sorted(os.path.basename(w) for w in wheels)},
                  f, indent=2)

    if a.no_zip:
        print(f"done (unzipped): {bundle}")
        return 0
    print("3. zipping")
    zip_tree(bundle, os.path.join(a.out, f"{BUNDLE_NAME}.zip"))
    print("\nShip the zip. Friend: unzip anywhere, run setup_pc.bat --"
          "\nno Python install needed. Rebuild after route/build/code "
          "changes\n(rebuilds are offline once dist/cache is warm).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
