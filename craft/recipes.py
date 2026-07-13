"""Curated league-start crafting recipes (data/craft_recipes.json).

Authored data, hand-verified against poewiki (2026-07-11) — the 20% of
crafting knowledge that covers what a leveling party actually does. The
copilot cites these; the LLM never invents methods that aren't here or in
the dataset's essence/bench tables. Pure stdlib, import-safe.

Entry: {id, name, kind: currency|essence|bench|vendor, applies_to,
        level: [min, max], when, how, notes?}

applies_to values: dataset item classes ("Boots"), or the groups
"any", "gear" (equipment incl. weapons), "weapon", "flask".
"""
from __future__ import annotations

import json
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PATH = os.path.join(_ROOT, "data", "craft_recipes.json")
GUIDELINES_PATH = os.path.join(_ROOT, "data", "craft_guidelines.json")
ORDER_PATH = os.path.join(_ROOT, "data", "craft_order.json")
METHODS_PATH = os.path.join(_ROOT, "data", "craft_methods.json")

_WEAPON_CLASSES = {
    "Bow", "Claw", "Dagger", "Rune Dagger", "One Hand Axe", "One Hand Mace",
    "One Hand Sword", "Thrusting One Hand Sword", "Sceptre", "Staff",
    "Warstaff", "Two Hand Axe", "Two Hand Mace", "Two Hand Sword", "Wand",
}
_GEAR_CLASSES = _WEAPON_CLASSES | {
    "Body Armour", "Boots", "Gloves", "Helmet", "Shield",
    "Amulet", "Ring", "Belt", "Quiver",
}


def _class_in_group(cls, group):
    if group == "any":
        return True
    if group == "gear":
        return cls in _GEAR_CLASSES
    if group == "weapon":
        return cls in _WEAPON_CLASSES
    if group == "flask":
        return cls.endswith("Flask")
    return cls == group


def load(path=None):
    with open(path or DEFAULT_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_guidelines(path=None):
    """General crafting principles ([{id, text}]) fed to the LLM with
    every plan. Human-readable version: docs/CRAFTING_GUIDELINES.md."""
    with open(path or GUIDELINES_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_order(path=None):
    """Order of operations: {phases: [{n, name, text}], rules: [str]}.
    The phases are the canonical crafting sequence (base -> quality ->
    sockets -> mods -> upgrade -> bench -> corrupt); the rules are hard
    sequencing constraints the LLM's plan steps must never violate."""
    with open(path or ORDER_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_methods(path=None):
    """Method-selection matrix: {choose: [str], stages: [{range, default,
    skip}], methods: [{id, name, what, where, use_when, avoid_when}]} —
    when essence vs fossil vs beastcraft vs harvest etc. is the right
    tool, and what exists at each progression stage."""
    with open(path or METHODS_PATH, encoding="utf-8") as f:
        return json.load(f)


def applicable(recipes, cls=None, level=None):
    """Recipes matching an item class and character level. Unknown class
    (None/'') only drops class-specific entries; unknown level keeps all."""
    out = []
    for r in recipes:
        lo, hi = r.get("level", [1, 100])
        if level is not None and not lo <= level <= hi:
            continue
        targets = r.get("applies_to", ["any"])
        if cls:
            if not any(_class_in_group(cls, t) for t in targets):
                continue
        elif targets != ["any"] and "any" not in targets:
            continue
        out.append(r)
    return out
