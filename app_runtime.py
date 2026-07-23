"""Runtime paths and configuration for the installed desktop app.

Packaged code and data are immutable.  Player-specific configuration,
window state, and run history live under the user's local application-data
directory so upgrades never overwrite them and the app never needs admin
rights.

This module is deliberately stdlib-only and import-safe so packaging and
path behavior can be tested on any operating system.
"""
from __future__ import annotations

import copy
import json
import os
import sys

APP_DIR_NAME = "PoE League Tools"
DATA_DIR_ENV = "POE_TOOLS_DATA_DIR"
SOURCE_ROOT = os.path.dirname(os.path.abspath(__file__))


def resource_root() -> str:
    """Directory containing immutable routes, builds, and application code."""
    return os.path.abspath(getattr(sys, "_MEIPASS", SOURCE_ROOT))


def user_data_dir(env: dict[str, str] | None = None) -> str:
    """Writable per-user application-data directory.

    ``POE_TOOLS_DATA_DIR`` is an intentional override for portable/testing
    use.  Windows installers use LOCALAPPDATA; the non-Windows fallbacks
    keep source development pleasant.
    """
    env = os.environ if env is None else env
    override = str(env.get(DATA_DIR_ENV) or "").strip()
    if override:
        return os.path.abspath(os.path.expanduser(override))

    if os.name == "nt":
        base = env.get("LOCALAPPDATA") or os.path.expanduser(
            r"~\AppData\Local")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = env.get("XDG_DATA_HOME") or os.path.expanduser(
            "~/.local/share")
    return os.path.join(os.path.abspath(base), APP_DIR_NAME)


def app_paths(root: str | None = None, data_dir: str | None = None) -> dict:
    """Canonical immutable and writable paths used by the desktop launcher."""
    root = os.path.abspath(root or resource_root())
    data_dir = os.path.abspath(data_dir or user_data_dir())
    return {
        "root": root,
        "data": data_dir,
        "config": os.path.join(data_dir, "config.json"),
        "ui_state": os.path.join(data_dir, "ui_state.json"),
        "runs": os.path.join(data_dir, "runs"),
        "logs": os.path.join(data_dir, "logs"),
        "routes": os.path.join(root, "routes"),
        "layouts": os.path.join(root, "overlay", "assets", "layouts"),
        "bundle": os.path.join(
            root, "builds", "allflame", "party_bundle.json"),
    }


def _load_existing(path: str, defaults: dict) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            value = json.load(f)
        if isinstance(value, dict):
            return value
    except (OSError, json.JSONDecodeError):
        pass
    return copy.deepcopy(defaults)


def _legacy_or_missing(value, legacy_values: set[str]) -> bool:
    if not value:
        return True
    value = str(value)
    if value.replace("\\", "/") in legacy_values:
        return True
    return os.path.isabs(value) and not os.path.exists(value)


def prepare_config(path: str, defaults: dict, paths: dict) -> dict:
    """Create/update the installed app's config and return it.

    User preferences remain intact.  Only legacy relative paths or stale
    managed asset paths are redirected to packaged resources and writable
    per-user storage.
    """
    cfg = _load_existing(path, defaults)

    if _legacy_or_missing(cfg.get("routes_dir"), {"../routes"}):
        cfg["routes_dir"] = paths["routes"]
    if _legacy_or_missing(cfg.get("runs_dir"), {"../runs", "runs"}):
        cfg["runs_dir"] = paths["runs"]

    layouts = dict(cfg.get("layouts") or {})
    if _legacy_or_missing(
            layouts.get("dir"), {"assets/layouts", "../overlay/assets/layouts"}):
        layouts["dir"] = paths["layouts"]
    layouts.setdefault("enabled", True)
    layouts.setdefault("auto_show", True)
    cfg["layouts"] = layouts

    cfg.setdefault("party", {"me": "", "members": [], "gap_warn": 3})
    cfg.setdefault("hotkeys", {})
    cfg.setdefault("narration", {
        "enabled": False, "rate": 0, "volume": 100,
        "tips": True, "layout": True,
    })

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    os.makedirs(paths["runs"], exist_ok=True)
    os.makedirs(paths["logs"], exist_ok=True)

    from tools import setup_profiles
    setup_profiles.write_config(cfg, path)
    return cfg


def needs_first_run(cfg: dict, config_path: str) -> bool:
    """Whether the graphical build/character setup should open."""
    selected = cfg.get("selected_build")
    if not isinstance(selected, dict) or not selected.get("id"):
        return True
    notes = cfg.get("build_notes")
    if not notes:
        return True
    if not os.path.isabs(notes):
        notes = os.path.join(
            os.path.dirname(os.path.abspath(config_path)), notes)
    return not os.path.isfile(notes)


def self_test(paths: dict) -> list[str]:
    """Return missing packaged assets; an empty list means the build is sane."""
    required = [
        paths["routes"],
        os.path.join(paths["routes"], "act1.json"),
        paths["bundle"],
        os.path.join(paths["root"], "data", "pob_leveling_adapters.json"),
    ]
    return [path for path in required if not os.path.exists(path)]
