"""Headless tests: Client.txt discovery (common paths, Steam vdf, drives).

All OS access is injected, so a Windows disk is faked on any platform.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "overlay")]

import find_client  # noqa: E402

# ------------------------------------------------------- vdf parsing
# Real files escape backslashes; parse_library_roots must unescape.
VDF = r'''
"libraryfolders"
{
    "0"
    {
        "path"        "C:\\Program Files (x86)\\Steam"
        "label"        ""
    }
    "1"
    {
        "path"        "E:\\Games\\SteamLib"
        "contentid"    "123"
    }
}
'''
assert find_client.parse_library_roots(VDF) == [
    r"C:\Program Files (x86)\Steam", r"E:\Games\SteamLib"]
assert find_client.parse_library_roots("") == []
assert find_client.parse_library_roots(None) == []
# duplicates collapse
assert find_client.parse_library_roots(
    '"path" "D:\\\\A"\n"path" "D:\\\\A"') == [r"D:\A"]

# ------------------------------------------------- fake filesystem bits
STEAM_ROOT = r"C:\Program Files (x86)\Steam"
VDF_PATH = os.path.join(STEAM_ROOT, "steamapps", "libraryfolders.vdf")
LIB_CLIENT = os.path.join(os.path.normpath(r"E:\Games\SteamLib"),
                          find_client._STEAM_SUFFIX)


def fs(existing, texts=None):
    """(exists, read_text) closed over a fake disk."""
    texts = texts or {}
    return (lambda p: p in existing), (lambda p: texts.get(p))


no_registry = lambda: None  # noqa: E731

# 1. configured path wins when it exists
exists, read = fs({r"X:\anywhere\Client.txt"})
assert find_client.discover(r"X:\anywhere\Client.txt", exists, read,
                            no_registry, drives=[]) == \
    (r"X:\anywhere\Client.txt", "config")

# 2. configured missing -> a common path
common = find_client.COMMON_CLIENT_PATHS[1]
exists, read = fs({common})
assert find_client.discover(r"X:\gone\Client.txt", exists, read,
                            no_registry, drives=[]) == (common, "common")

# 3. nothing common -> Steam's own library list (default install dir)
exists, read = fs({VDF_PATH, LIB_CLIENT}, {VDF_PATH: VDF})
assert find_client.discover(None, exists, read, no_registry, drives=[]) == \
    (LIB_CLIENT, "steam library")

# 4. Steam found via the registry at a custom location
reg_root = r"E:\CustomSteam"
reg_vdf = os.path.join(reg_root, "steamapps", "libraryfolders.vdf")
reg_lib_client = os.path.join(os.path.normpath(r"F:\Lib"),
                              find_client._STEAM_SUFFIX)
exists, read = fs({reg_vdf, reg_lib_client},
                  {reg_vdf: '"path"  "F:\\\\Lib"'})
assert find_client.discover(None, exists, read, lambda: reg_root,
                            drives=[]) == (reg_lib_client, "steam library")

# 5. last resort: per-drive layout scan
drive_client = r"G:\Games\Path of Exile\logs\Client.txt"
exists, read = fs({drive_client})
assert find_client.discover(None, exists, read, no_registry,
                            drives=["G:\\"]) == (drive_client, "drive scan")

# 6. nothing anywhere
exists, read = fs(set())
assert find_client.discover(r"X:\gone.txt", exists, read, no_registry,
                            drives=["C:\\", "D:\\"]) == (None, "")

# 7. candidate_paths: unique, commons first, steam + drives included
exists, read = fs({VDF_PATH}, {VDF_PATH: VDF})
cands = find_client.candidate_paths(exists, read, no_registry,
                                    drives=["G:\\"])
assert cands[:len(find_client.COMMON_CLIENT_PATHS)] == \
    find_client.COMMON_CLIENT_PATHS
assert LIB_CLIENT in cands
assert drive_client in cands
assert len(cands) == len(set(cands))

# 8. vdf that lists a library also covered by the common list dedupes
dup_lib = os.path.join(os.path.normpath(r"D:\SteamLibrary"),
                       find_client._STEAM_SUFFIX)
exists, read = fs({VDF_PATH}, {VDF_PATH: '"path" "D:\\\\SteamLibrary"'})
cands = find_client.candidate_paths(exists, read, no_registry, drives=[])
# the common D:\SteamLibrary entry and the vdf-derived one differ only
# by separator on non-Windows; both spellings must not BOTH appear twice
assert len(cands) == len(set(cands))
assert dup_lib in cands or dup_lib in find_client.COMMON_CLIENT_PATHS

print("ALL TESTS PASSED")
