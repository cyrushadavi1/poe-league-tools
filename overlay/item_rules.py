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
_ADAPTER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "..", "data", "pob_leveling_adapters.json")
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
_profile_cache: dict[str, dict] | None = None


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


def load_build_profiles(path: str = _ADAPTER_PATH) -> dict[str, dict]:
    """Adapter id -> item guide, including gems from its acquisition list."""
    try:
        with open(path, encoding="utf-8") as f:
            adapters = json.load(f).get("adapters", [])
    except (OSError, json.JSONDecodeError, AttributeError):
        return {}
    out = {}
    for adapter in adapters:
        adapter_id = adapter.get("id")
        guide = adapter.get("item_guide")
        if not adapter_id or not isinstance(guide, dict):
            continue
        guide = dict(guide)
        gems = set(guide.get("wanted_gems", []))
        for row in adapter.get("gem_checklist", []):
            gems.update(str(x) for x in row.get("items", []) if x)
        guide["wanted_gems"] = sorted(gems)
        out[adapter_id] = guide
    return out


def _profiles() -> dict[str, dict]:
    global _profile_cache
    if _profile_cache is None:
        _profile_cache = load_build_profiles()
    return _profile_cache


def _profile(build) -> dict | None:
    if isinstance(build, dict):
        return build
    if isinstance(build, str):
        return _profiles().get(build)
    return None


def _in_act(row, act):
    return int(row.get("act_min", 1)) <= act <= int(row.get("act_max", 10))


def _matching_base_rule(parsed, profile, act):
    haystack = " ".join((parsed.get("name", ""), parsed.get("base", ""))).lower()
    for rule in profile.get("keep_bases", []):
        if not _in_act(rule, act):
            continue
        names = rule.get("names", [])
        classes = rule.get("classes", [])
        if (any(str(name).lower() in haystack for name in names)
                or parsed.get("item_class") in classes):
            return rule
    return None


def _matching_socket_target(parsed, profile, act):
    """Return a linked group whose colours earn the 3.29 quality bonus.

    Gems fit every socket in 3.29.  A matching red/green/blue socket is
    therefore an upgrade (+10% gem quality), not an equip requirement.
    White sockets are usable but do not stand in for a matching colour.
    """
    for target in profile.get("socket_targets", []):
        if not _in_act(target, act):
            continue
        need = target.get("colors") or {}
        for group in parsed.get("link_groups", []):
            if len(group) < int(target.get("links", 0)):
                continue
            deficit = sum(max(0, int(count) - group.count(color))
                          for color, count in need.items())
            if deficit == 0:
                return target
    return None


def _weapon_rule(profile, act):
    for rule in profile.get("weapon_rules", []):
        if _in_act(rule, act):
            return rule
    return None


def _matching_mod(parsed, needles):
    blob = "\n".join(parsed.get("mods") or []).lower()
    return next((needle for needle in needles
                 if str(needle).lower() in blob), None)


def _matching_item_mod_rule(parsed, profile, act):
    for rule in profile.get("item_mod_rules", []):
        if not _in_act(rule, act):
            continue
        classes = rule.get("classes", [])
        if classes and parsed.get("item_class") not in classes:
            continue
        if mod := _matching_mod(parsed, rule.get("mods_any", [])):
            return rule, mod
    return None, None


def _weapon_threshold(rule, act):
    rows = sorted(rule.get("thresholds", []),
                  key=lambda row: int(row.get("act", 1)))
    eligible = [row for row in rows if int(row.get("act", 1)) <= act]
    return eligible[-1] if eligible else (rows[0] if rows else None)


def _score(parsed: dict) -> int:
    """Life + total resists (+ movespeed for boots), one point each."""
    props = parsed.get("props") or {}
    res = props.get("res") or {}
    score = props.get("life", 0) + sum(res.values())
    if parsed.get("item_class") == "Boots":
        score += props.get("movespeed", 0)
    return score


def _gem_key(name):
    text = str(name or "").strip().lower()
    return text.removesuffix(" support")


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
    profile = _profile(ctx.get("build"))

    # -- always-take categories ------------------------------------------
    if rarity == "Currency" or item_class == "Stackable Currency":
        return TAKE, "Currency — always worth picking up"
    if rarity == "Divination Card" or item_class == "Divination Cards":
        return TAKE, "Divination card — always worth picking up"
    if rarity == "Gem":
        if profile:
            gem = (parsed.get("name") or parsed.get("base") or "").strip()
            wanted = {_gem_key(name)
                      for name in profile.get("wanted_gems", [])}
            if _gem_key(gem) in wanted:
                return TAKE, f"{gem} is on this build's gem checklist"
            if int(parsed.get("quality", 0) or 0) > 0:
                return CHECK, "Off-plan quality gem — keep only if valuable"
            return SKIP, "Gem is not used by this build's campaign or swap"
        return CHECK, "Skill gem — take it if the build uses it"

    if profile:
        if rarity in ("Unique", "Relic"):
            unique_name = parsed.get("name") or ""
            reason = (profile.get("keep_uniques") or {}).get(unique_name)
            if reason:
                return TAKE, reason

        mod_rule, mod = _matching_item_mod_rule(parsed, profile, act)
        if mod_rule:
            return mod_rule.get("verdict", TAKE), (
                f"{mod_rule['reason']} ({mod})")

        socket_target = _matching_socket_target(parsed, profile, act)
        if socket_target:
            return TAKE, socket_target["reason"]

        base_rule = _matching_base_rule(parsed, profile, act)
        if base_rule:
            return base_rule.get("verdict", TAKE), base_rule["reason"]

    # -- link upgrade beats everything else ------------------------------
    if links > links_best:
        if profile and any(_in_act(row, act)
                           for row in profile.get("socket_targets", [])):
            return TAKE, (f"{links}-link beats your current best "
                          f"({links_best}-link); in 3.29 every gem fits every "
                          "socket colour (matching colours only add quality)")
        return TAKE, (f"{links}-link beats your current best "
                      f"({links_best}-link) — socket setup upgrade")

    # -- movement speed is king early ------------------------------------
    movespeed = props.get("movespeed", 0)
    if item_class == "Boots" and movespeed >= 10 and act < 4:
        return TAKE, f"{movespeed}% movement speed boots before Act 4"

    if rarity in ("Unique", "Relic"):     # Relic = foil unique
        return CHECK, "Unique — worth a look, needs build judgment"

    # -- weapons: apply the matched build's class/DPS priorities ----------
    if item_class in WEAPON_CLASSES:
        if profile and (rule := _weapon_rule(profile, act)):
            wanted = set(rule.get("classes", []))
            if wanted and item_class not in wanted:
                return SKIP, (f"Wrong weapon type for this stage — wants "
                              f"{'/'.join(sorted(wanted))}")
            threshold = _weapon_threshold(rule, act)
            if threshold:
                metric = threshold.get("metric", "total")
                actual = float((parsed.get("weapon_dps") or {}).get(metric, 0))
                minimum = float(threshold.get("min", 0))
                if actual >= minimum:
                    return TAKE, (f"{actual:.0f} {metric} DPS meets the Act "
                                  f"{act} target ({minimum:.0f}+)")
                if actual > 0:
                    return SKIP, (f"{actual:.0f} {metric} DPS is below the "
                                  f"Act {act} target ({minimum:.0f}+)")
            mod = _matching_mod(parsed, rule.get("mods_any", []))
            if mod:
                return TAKE, f"Preferred {item_class}: has {mod}"
            if rarity in ("Rare", "Magic"):
                return CHECK, (f"Correct weapon type ({item_class}) — compare "
                               "damage/mods with your equipped weapon")
            return SKIP, "Correct type but no useful roll or build-specific base"
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
