"""Deterministic campaign-leveling verdicts for parsed clipboard items.

evaluate(parsed, ctx) -> (verdict, reason)
  verdict in {"TAKE", "SKIP", "CHECK"}, reason is one human-readable line.
  parsed: overlay.itemtext.parse() output (None tolerated -> SKIP).
  ctx: {"level": int, "act": int, "links_best": int, "build": dict|None}

Pure stdlib, no Qt, no LLM — this is the P0 rules core.  The later LLM
path (addendum 5C, P1) hangs off the CHECK verdicts; integration wires it.

Life/resist scoring uses data/resist_budget.json: per act, the rough
life + total-resist a leveling character wants from ALL gear by the end
of that act.  A single item is judged against a per-slot share of that
budget (8 slots commonly carry life/res: helmet, body, gloves, boots,
belt, amulet, 2 rings).
"""
from __future__ import annotations

import json
import os

TAKE, SKIP, CHECK = "TAKE", "SKIP", "CHECK"

_BUDGET_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "data", "resist_budget.json")
_SLOTS = 8            # gear slots that typically contribute life/res
_STRONG = 1.25        # score >= STRONG * slot share  -> TAKE
_WEAK = 0.6           # score <  WEAK   * slot share  -> SKIP (CHECK between)

WEAPON_CLASSES = frozenset({
    "Bows", "Claws", "Daggers", "Fishing Rods", "One Hand Axes",
    "One Hand Maces", "One Hand Swords", "Rune Daggers", "Sceptres",
    "Staves", "Thrusting One Hand Swords", "Two Hand Axes",
    "Two Hand Maces", "Two Hand Swords", "Wands", "Warstaves",
})

_budget_cache: dict[int, dict] | None = None


def load_budget(path: str = _BUDGET_PATH) -> dict[int, dict]:
    """Load {act: {"life": N, "res_total": M}} from resist_budget.json."""
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return {int(k): v for k, v in raw.items() if k.isdigit()}


def _get_budget() -> dict[int, dict]:
    global _budget_cache
    if _budget_cache is None:
        _budget_cache = load_budget()
    return _budget_cache


def _score(parsed: dict) -> int:
    """Life + total resists (+ movespeed for boots), one point each."""
    props = parsed.get("props") or {}
    res = props.get("res") or {}
    score = props.get("life", 0) + sum(res.values())
    if parsed.get("item_class") == "Boots":
        score += props.get("movespeed", 0)
    return score


def evaluate(parsed, ctx, budget=None):
    """Return (verdict, reason) for a parsed item during campaign leveling."""
    if not isinstance(parsed, dict):
        return SKIP, "Not recognizable item text"
    ctx = ctx or {}
    act = max(1, min(10, int(ctx.get("act", 1) or 1)))
    links_best = int(ctx.get("links_best", 0) or 0)

    item_class = parsed.get("item_class", "")
    rarity = parsed.get("rarity", "")
    props = parsed.get("props") or {}
    links = parsed.get("links", 0)

    # -- always-take categories ------------------------------------------
    if rarity == "Currency" or item_class == "Stackable Currency":
        return TAKE, "Currency — always worth picking up"
    if rarity == "Divination Card" or item_class == "Divination Cards":
        return TAKE, "Divination card — always worth picking up"
    if rarity == "Gem":
        return CHECK, "Skill gem — take it if the build uses it"

    # -- link upgrade beats everything else ------------------------------
    if links > links_best:
        return TAKE, (f"{links}-link beats your current best "
                      f"({links_best}-link) — socket setup upgrade")

    # -- movement speed is king early ------------------------------------
    movespeed = props.get("movespeed", 0)
    if item_class == "Boots" and movespeed >= 10 and act < 4:
        return TAKE, f"{movespeed}% movement speed boots before Act 4"

    if rarity in ("Unique", "Relic"):     # Relic = foil unique
        return CHECK, "Unique — worth a look, needs build judgment"

    # -- weapons: DPS is a build question --------------------------------
    if item_class in WEAPON_CLASSES:
        if rarity == "Rare":
            return CHECK, "Rare weapon — DPS judgment needs the build"
        if rarity == "Magic" and act <= 2:
            return CHECK, "Early magic weapon — might beat your current DPS"
        return SKIP, "Weapon unlikely to matter now — vendor fodder"

    if rarity == "Normal":
        return SKIP, "Plain base with no useful sockets — vendor trash"

    # -- life/res scoring vs the per-act budget --------------------------
    table = budget if budget is not None else _get_budget()
    row = table.get(act) or table[max(table)]
    slot_share = (row["life"] + row["res_total"]) / _SLOTS
    score = _score(parsed)
    life = props.get("life", 0)
    res_total = sum((props.get("res") or {}).values())
    detail = f"{life} life / +{res_total}% res"
    if score >= _STRONG * slot_share:
        return TAKE, (f"Strong for Act {act}: {detail} "
                      f"(slot target ~{slot_share:.0f})")
    if score >= _WEAK * slot_share:
        return CHECK, (f"Decent for Act {act}: {detail} — "
                       "compare with what you're wearing")
    return SKIP, (f"Weak for Act {act}: {detail} "
                  f"(slot target ~{slot_share:.0f})")
