"""Find the game's Client.txt without making anyone edit a path.

Pure stdlib, no Qt, import-safe -- shared by overlay/main.py (launch),
tools/join_party.py (first-run wizard) and tools/preflight.py (doctor).
Read-only filesystem probing: a handful of well-known install paths,
Steam's own library list (steamapps/libraryfolders.vdf -- a config file
Steam maintains, read like any other text file), and a cheap scan of
common per-drive layouts. Nothing here touches the game.

Every OS access is injectable so tests can fake a Windows disk on any
platform (see tests/test_find_client.py).
"""
import os
import re
import string

# Probed first (in order) -- covers default standalone + default Steam.
COMMON_CLIENT_PATHS = [
    r"C:\Program Files (x86)\Grinding Gear Games\Path of Exile\logs\Client.txt",
    r"C:\Program Files (x86)\Steam\steamapps\common\Path of Exile\logs\Client.txt",
    r"C:\Program Files\Grinding Gear Games\Path of Exile\logs\Client.txt",
    r"D:\SteamLibrary\steamapps\common\Path of Exile\logs\Client.txt",
]

# Relative to a Steam library root ("D:\SteamLibrary", ...). The PoE 2
# folder is literally "Path of Exile 2", so this cannot mismatch.
_STEAM_SUFFIX = r"steamapps\common\Path of Exile\logs\Client.txt"

# Per-drive layouts people actually use for the standalone client or a
# moved Steam library ("X:\" is substituted for every existing drive).
_DRIVE_PATTERNS = [
    r"{d}SteamLibrary\steamapps\common\Path of Exile\logs\Client.txt",
    r"{d}Steam\steamapps\common\Path of Exile\logs\Client.txt",
    r"{d}Grinding Gear Games\Path of Exile\logs\Client.txt",
    r"{d}Games\Path of Exile\logs\Client.txt",
    r"{d}Path of Exile\logs\Client.txt",
]


def _registry_steam_path():
    """Steam's install dir from the registry, or None (non-Windows too)."""
    if os.name != "nt":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Valve\Steam") as key:
            path = winreg.QueryValueEx(key, "SteamPath")[0]
        return os.path.normpath(path) if path else None
    except OSError:
        return None


def _read_text(path):
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return f.read()
    except OSError:
        return None


def _windows_drives(exists=os.path.exists):
    if os.name != "nt":
        return []
    return [f"{c}:\\" for c in string.ascii_uppercase if exists(f"{c}:\\")]


def parse_library_roots(vdf_text):
    """Steam library roots out of a libraryfolders.vdf body.

    The vdf is a nested key/value format; every library (including the
    Steam dir itself in current versions) carries a `"path" "X:\\\\..."`
    line -- that regex-level read is all we need.
    """
    roots = []
    for m in re.finditer(r'"path"\s+"([^"]*)"', vdf_text or ""):
        root = m.group(1).replace("\\\\", "\\")
        if root and root not in roots:
            roots.append(root)
    return roots


def steam_client_paths(exists=os.path.exists, read_text=_read_text,
                       registry_steam_path=_registry_steam_path):
    """Candidate Client.txt paths from every configured Steam library."""
    steam_dirs = []
    reg = registry_steam_path()
    if reg:
        steam_dirs.append(reg)
    steam_dirs += [r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam"]

    out = []
    for steam in steam_dirs:
        vdf = os.path.join(steam, "steamapps", "libraryfolders.vdf")
        if not exists(vdf):
            continue
        for root in parse_library_roots(read_text(vdf)):
            p = os.path.join(os.path.normpath(root), _STEAM_SUFFIX)
            if p not in out:
                out.append(p)
    return out


def candidate_paths(exists=os.path.exists, read_text=_read_text,
                    registry_steam_path=_registry_steam_path, drives=None):
    """All probe locations, most-likely first, duplicates removed."""
    if drives is None:
        drives = _windows_drives(exists)
    cands = list(COMMON_CLIENT_PATHS)
    cands += steam_client_paths(exists, read_text, registry_steam_path)
    for d in drives:
        cands += [pat.format(d=d) for pat in _DRIVE_PATTERNS]
    seen, out = set(), []
    for p in cands:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def discover(configured=None, exists=os.path.exists, read_text=_read_text,
             registry_steam_path=_registry_steam_path, drives=None):
    """Best Client.txt -> (path, how) or (None, "") when nothing exists.

    `how` is "config" | "common" | "steam library" | "drive scan" so
    callers can tell the user where the path came from. A configured
    path that exists always wins -- auto-detection never overrides an
    explicit working choice.
    """
    if configured and exists(configured):
        return configured, "config"
    for p in COMMON_CLIENT_PATHS:
        if exists(p):
            return p, "common"
    for p in steam_client_paths(exists, read_text, registry_steam_path):
        if exists(p):
            return p, "steam library"
    for d in (drives if drives is not None else _windows_drives(exists)):
        for pat in _DRIVE_PATTERNS:
            p = pat.format(d=d)
            if exists(p):
                return p, "drive scan"
    return None, ""
