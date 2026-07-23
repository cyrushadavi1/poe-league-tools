# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller one-folder build for the Windows desktop application."""
import os
import glob

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

required_trees = [
    ("routes", "routes"),
    (os.path.join("builds", "allflame"), os.path.join("builds", "allflame")),
    (os.path.join("overlay", "assets"), os.path.join("overlay", "assets")),
]
missing = [src for src, _dest in required_trees
           if not os.path.exists(os.path.join(ROOT, src))]
if missing:
    raise SystemExit(
        "installer data is not prepared: " + ", ".join(missing)
        + " (generate builds and fetch layouts before PyInstaller)")

datas = [(os.path.join(ROOT, src), dest) for src, dest in required_trees]
# Only tracked deterministic JSON is application data.  Do not accidentally
# ship a developer's ignored wiki cache or raw patch-note download.
datas += [(path, "data")
          for path in glob.glob(os.path.join(ROOT, "data", "*.json"))]

a = Analysis(
    [os.path.join(ROOT, "poe_league_tools.py")],
    pathex=[
        ROOT,
        os.path.join(ROOT, "overlay"),
        os.path.join(ROOT, "buildgen"),
        os.path.join(ROOT, "market"),
        os.path.join(ROOT, "advisor"),
    ],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "main",
        "party",
        "tools.setup_gui",
        "tools.setup_profiles",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PoE League Tools",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="PoE League Tools",
)
