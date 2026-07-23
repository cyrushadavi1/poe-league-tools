"""Pure helpers shared by the graphical and terminal setup flows.

The four reviewed party PoBs are generated into ``builds/allflame``.
This module discovers that bundle, turns a selected role into portable
config paths, and writes config atomically.  It intentionally imports no
Qt so the selection/configuration behavior stays headless-testable.
"""
from __future__ import annotations

import copy
import json
import os
import tempfile


def load_bundle(path: str) -> dict | None:
    """Return a minimally valid party bundle, otherwise ``None``."""
    try:
        with open(path, encoding="utf-8") as f:
            bundle = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(bundle, dict):
        return None
    members = bundle.get("members")
    if not isinstance(members, list) or not members:
        return None
    required = {"player", "notes", "plan"}
    if any(not isinstance(row, dict) or not required <= set(row)
           for row in members):
        return None
    return bundle


def bundle_candidates(root: str, preferred: str | None = None) -> list[str]:
    """Ordered bundle locations used by setup and the in-app picker."""
    rows = []
    if preferred:
        rows.append(os.path.abspath(preferred))
    rows.extend([
        os.path.join(root, "builds", "allflame", "party_bundle.json"),
        os.path.join(root, "builds", "party_bundle.json"),
    ])
    out = []
    for row in rows:
        normalized = os.path.normcase(os.path.abspath(row))
        if normalized not in {os.path.normcase(x) for x in out}:
            out.append(os.path.abspath(row))
    return out


def find_bundle(root: str, preferred: str | None = None):
    """Return ``(path, bundle)`` for the first valid candidate."""
    for path in bundle_candidates(root, preferred):
        bundle = load_bundle(path)
        if bundle:
            return path, bundle
    return None, None


def member_label(member: dict) -> str:
    """Friendly role/build label for the graphical selector."""
    player = str(member.get("player") or "Build")
    role = str(member.get("role") or "").strip()
    cls = str(member.get("class") or "").strip()
    asc = str(member.get("ascendancy") or "").strip()
    build = f"{cls} ({asc})" if cls and asc else cls or asc
    details = " · ".join(part for part in (role, build) if part)
    return f"{player} — {details}" if details else player


def selected_member_index(cfg: dict, bundle: dict, bundle_path: str,
                          config_path: str) -> int:
    """Best current selection from explicit metadata or resolved notes."""
    selected = cfg.get("selected_build") or {}
    selected_id = (selected.get("id") if isinstance(selected, dict)
                   else selected)
    members = bundle["members"]
    for i, member in enumerate(members):
        if selected_id and member.get("player") == selected_id:
            return i

    notes_cfg = cfg.get("build_notes")
    if notes_cfg:
        config_dir = os.path.dirname(os.path.abspath(config_path))
        current = (notes_cfg if os.path.isabs(notes_cfg)
                   else os.path.join(config_dir, notes_cfg))
        current = os.path.normcase(os.path.abspath(current))
        bundle_dir = os.path.dirname(os.path.abspath(bundle_path))
        for i, member in enumerate(members):
            candidate = os.path.normcase(os.path.abspath(
                os.path.join(bundle_dir, member["notes"])))
            if candidate == current:
                return i
    return 0


def _portable_ref(target: str, config_path: str) -> str:
    rel = os.path.relpath(os.path.abspath(target),
                          os.path.dirname(os.path.abspath(config_path)))
    return rel.replace(os.sep, "/")


def apply_profile(cfg: dict, config_path: str, bundle_path: str,
                  bundle: dict, member_index: int,
                  character_name: str = "",
                  teammates: list[str] | None = None) -> dict:
    """Return config updated for one selected build.

    ``character_name`` is deliberately separate from the role label.  The
    game log contains real character names, while the hardcoded build
    catalog contains stable role ids such as ``Carry`` and ``Aurabot``.
    """
    members = bundle["members"]
    if not 0 <= member_index < len(members):
        raise ValueError("selected build is outside the bundle")
    member = members[member_index]
    bundle_dir = os.path.dirname(os.path.abspath(bundle_path))
    notes_abs = os.path.join(bundle_dir, member["notes"])
    plan_abs = os.path.join(bundle_dir, member["plan"])
    if not os.path.exists(notes_abs):
        raise FileNotFoundError(
            f"{member['notes']} is missing next to the party bundle")
    if not os.path.exists(plan_abs):
        raise FileNotFoundError(
            f"{member['plan']} is missing next to the party bundle")

    out = copy.deepcopy(cfg)
    party = dict(out.get("party") or {})
    current_me = str(party.get("me") or "").strip()
    role_ids = {str(row.get("player") or "") for row in members}
    if current_me in role_ids:
        current_me = ""
    me = str(character_name or "").strip() or current_me
    party["me"] = me
    party["build"] = member["player"]
    if teammates is not None:
        cleaned = []
        for name in teammates:
            name = str(name or "").strip()
            if name and name != me and name not in cleaned:
                cleaned.append(name)
        party["members"] = cleaned
    else:
        party.setdefault("members", [])
    party.setdefault("gap_warn", 3)
    out["party"] = party

    out["league"] = bundle.get("league", out.get("league", "3.29"))
    out["build_notes"] = _portable_ref(notes_abs, config_path)
    out["build_plan"] = _portable_ref(plan_abs, config_path)
    out["party_bundle"] = _portable_ref(bundle_path, config_path)
    out["selected_build"] = {
        "id": member["player"],
        "role": member.get("role") or "",
        "class": member.get("class") or "",
        "ascendancy": member.get("ascendancy") or "",
        "pob": member.get("pob") or "",
    }
    return out


def write_config(cfg: dict, path: str) -> None:
    """Atomically replace config so an interrupted save cannot corrupt it."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(
        prefix=".config.", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
            f.write("\n")
        os.replace(temp_path, path)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise
