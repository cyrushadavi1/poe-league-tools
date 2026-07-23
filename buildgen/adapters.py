"""Build-specific campaign adapters for guide PoBs.

Guide authors do not use one common convention: some put level ranges in
tree titles, some use empty labelled Skill groups as separators, and some
export only an endgame setup.  The generic parser in pob.py handles the
first two conventions.  This module matches known party builds by their
structure and supplies reviewed campaign milestones for the gaps.

Matching intentionally does not depend on a pobb.in slug.  A downloaded
PoB remains recognizable when party.json stores only its resolved code,
and a refreshed share link still matches while its class/stage signature
is unchanged.
"""
import json
import os
import re


CATALOG = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "pob_leveling_adapters.json")


def normalize_title(title):
    """Drop PoB's display-order suffix: ``Early Maps {2}`` -> Early Maps."""
    return re.sub(r"\s*\{\d+\}\s*$", "", title or "").strip()


def load_catalog(path=CATALOG):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    rows = data.get("adapters", []) if isinstance(data, dict) else []
    return rows if isinstance(rows, list) else []


def _gems(root):
    out = set()
    skills = root.find("Skills")
    if skills is None:
        return out
    for skill in skills.iter("Skill"):
        if skill.get("enabled", "true") == "false":
            continue
        for gem in skill.findall("Gem"):
            if gem.get("enabled", "true") == "false":
                continue
            name = gem.get("nameSpec") or gem.get("skillId")
            if name:
                out.add(name)
    return out


def match_adapter(root, info, specs, sets, catalog_path=CATALOG):
    """Return the first exact structural match, or ``None``.

    Every declared matcher is required.  This conservative rule is more
    useful than a fuzzy class-only match: a wrong campaign respec is much
    worse than falling back to the clearly labelled generic guide.
    """
    set_titles = {normalize_title(row.get("title")) for row in sets}
    tree_titles = {normalize_title(row.get("title")) for row in specs}
    gems = _gems(root)
    for adapter in load_catalog(catalog_path):
        match = adapter.get("match") or {}
        if match.get("class") and match["class"] != info.get("class"):
            continue
        if (match.get("ascendancy")
                and match["ascendancy"] != info.get("ascendancy")):
            continue
        if not set(match.get("skill_sets", [])).issubset(set_titles):
            continue
        if not set(match.get("tree_specs", [])).issubset(tree_titles):
            continue
        if not set(match.get("gems", [])).issubset(gems):
            continue
        return adapter
    return None


def campaign_notes(adapter):
    """Validated overlay-note rows from a matched adapter."""
    out = []
    for row in adapter.get("milestones", []):
        try:
            act = int(row["act"])
        except (KeyError, TypeError, ValueError):
            continue
        text = str(row.get("text") or "").strip()
        if not text or not 1 <= act <= 10:
            continue
        note = {
            "act": act,
            "text": text,
            "source": f"adapter:{adapter.get('id', 'unknown')}",
        }
        try:
            note["level"] = int(row["level"])
        except (KeyError, TypeError, ValueError):
            pass
        out.append(note)
    return sorted(out, key=lambda n: (n["act"], n.get("level", -1)))
