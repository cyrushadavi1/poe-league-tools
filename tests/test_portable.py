"""Headless tests: portable-bundle builder (tools/make_portable.py).

Offline by policy: the download/pip steps are exercised manually (and
by real builds); here we pin down the pure logic -- what ships, what
doesn't, how wheels unpack, and the ._pth that controls the embedded
interpreter's sys.path.
"""
import os
import shutil
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT]

from tools import make_portable as mp  # noqa: E402

# ------------------------------------------------------------- ._pth
pth = mp.pth_content("3.13.14")
lines = pth.splitlines()
assert lines[0] == "python313.zip", lines
assert "." in lines
assert r"Lib\site-packages" in lines
# every same-directory-import package dir must be listed: the ._pth
# takes FULL control of sys.path (no script-dir insertion)
for entry in ("..", r"..\overlay", r"..\buildgen", r"..\market",
              r"..\advisor"):
    assert entry in lines, f"missing {entry}"
assert "#import site" in lines, "site must stay disabled"
assert mp.pth_content("3.10.5").splitlines()[0] == "python310.zip"

# ------------------------------------------------- requirements pin
tmp = tempfile.mkdtemp(prefix="poe_portable_test_")
try:
    req = os.path.join(tmp, "requirements.txt")
    with open(req, "w", encoding="utf-8") as f:
        f.write("# comment\nPyQt6   # the overlay UI\n# anthropic\n")
    assert mp.read_pyqt_requirement(req) == "PyQt6"
    with open(req, "w", encoding="utf-8") as f:
        f.write("PyQt6==6.7.1\n")
    assert mp.read_pyqt_requirement(req) == "PyQt6==6.7.1"
    # the real requirements.txt must always yield one
    assert mp.read_pyqt_requirement(
        os.path.join(ROOT, "requirements.txt")).lower().startswith("pyqt6")

    # ------------------------------------------------ wheel extraction
    whl = os.path.join(tmp, "fake_pkg-1.0-py3-none-any.whl")
    with zipfile.ZipFile(whl, "w") as z:
        z.writestr("fake_pkg/__init__.py", "VERSION = '1.0'\n")
        z.writestr("fake_pkg-1.0.dist-info/METADATA", "Name: fake-pkg\n")
    sp = os.path.join(tmp, "site-packages")
    mp.extract_wheels([whl], sp)
    assert os.path.exists(os.path.join(sp, "fake_pkg", "__init__.py"))
    assert os.path.exists(os.path.join(sp, "fake_pkg-1.0.dist-info",
                                       "METADATA"))

    # ------------------------------------------------ tree assembly
    fake = os.path.join(tmp, "repo")
    for rel in ["overlay/main.py", "routes/act1.json", "tools/check.py",
                "builds/party_bundle.json", "builds/A_notes.json",
                "party.json", "data/resist_budget.json",
                "data/wiki_cache/Act_1.html", "FRIENDS.md",
                "setup_pc.bat", ".venv/bin/python", ".git/HEAD",
                "dist/old.zip", "runs/run_x.json", "llm_usage.jsonl",
                "market/market.db", "market/store.py",
                "overlay/__pycache__/main.cpython-313.pyc",
                "verify_act3.json", "exposure_Bob.md"]:
        p = os.path.join(fake, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").close()
    dest = os.path.join(tmp, "out")
    mp.assemble_tree(fake, dest)

    shipped = []
    for dirpath, _dirs, files in os.walk(dest):
        for n in files:
            shipped.append(os.path.relpath(os.path.join(dirpath, n), dest))
    shipped = set(shipped)

    # must ship: code, routes, builds (gitignored but essential), docs
    for rel in ["overlay/main.py", "routes/act1.json", "tools/check.py",
                "builds/party_bundle.json", "builds/A_notes.json",
                "party.json", "data/resist_budget.json", "FRIENDS.md",
                "setup_pc.bat", "market/store.py"]:
        assert rel.replace("/", os.sep) in shipped, f"missing {rel}"
    # must NOT ship: dev/junk/caches/outputs
    for rel in [".venv/bin/python", ".git/HEAD", "dist/old.zip",
                "runs/run_x.json", "llm_usage.jsonl", "market/market.db",
                "data/wiki_cache/Act_1.html", "verify_act3.json",
                "exposure_Bob.md",
                "overlay/__pycache__/main.cpython-313.pyc"]:
        assert rel.replace("/", os.sep) not in shipped, f"leaked {rel}"
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("ALL TESTS PASSED")
