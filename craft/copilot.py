"""Crafting copilot: deterministic digest + LLM plan (addendum 5G).

    from craft import copilot
    result = copilot.advise(parsed, ctx)          # parsed = itemtext.parse()

ctx keys (all optional): level (int), goal (str), budget (str),
build (dict — the party bundle's member entry or a PoB digest).

Returns {"digest", "text", "plan", "llm_note"}:
  digest    the deterministic facts dict (also the LLM's DATA payload)
  text      human-readable digest + plan, ready for terminal/overlay
  plan      validated dict per prompts.PLAN_SCHEMA, or None
  llm_note  why plan is None ("" when it isn't)

Degrade (INTERFACES.md invariant 4): LLMDisabled/LLMError/ImportError →
digest-only output, llm_note says why. Nothing here blocks the overlay
poll path — callers hand this to a worker thread. Standard tier,
feature tag "craft_copilot". Pure stdlib, no Qt, import-safe.
"""
from __future__ import annotations

import json

from craft import pool as pool_mod
from craft import recipes as recipes_mod
from craft.prompts import PLAN_SCHEMA, SYSTEM

# Ladders shown to the human and the LLM (ranked by spawn weight); the
# cut is noted in the digest so nobody mistakes it for the full pool.
POOL_LADDERS_SHOWN = 18


def build_digest(parsed, ctx, data, recipes=None, guidelines=None,
                 order=None, methods=None):
    """All deterministic facts about crafting on this item, as one dict."""
    match = data.match_item(parsed)
    ilvl = parsed.get("ilvl") or 100
    cls = match["cls"]
    digest = {
        "item": {
            "name": parsed.get("name", ""),
            "base": match["base"] or parsed.get("base", ""),
            "cls": cls,
            "rarity": parsed.get("rarity", ""),
            "ilvl": parsed.get("ilvl", 0),
            "quality": parsed.get("quality", 0),
            "sockets": parsed.get("sockets", 0),
            "links": parsed.get("links", 0),
            "corrupted": parsed.get("corrupted", False),
        },
        "mods": match["rows"],
        "open": dict(match["open"], uncertain=match["uncertain"]),
        "pool": {"prefix": [], "suffix": [], "note": ""},
        "essences": data.essences_for(cls, ilvl) if cls else [],
        "bench": {"mods": [], "actions": []},
        "recipes": recipes_mod.applicable(
            recipes if recipes is not None else recipes_mod.load(),
            cls=cls or None, level=ctx.get("level")),
        "guidelines": (guidelines if guidelines is not None
                       else recipes_mod.load_guidelines()),
        "order": order if order is not None else recipes_mod.load_order(),
        "methods": (methods if methods is not None
                    else recipes_mod.load_methods()),
        "ctx": {k: ctx[k] for k in ("level", "goal", "budget", "build")
                if ctx.get(k) is not None},
        "data_version": data.meta.get("game_version", "unknown"),
    }
    if parsed.get("corrupted"):
        digest["pool"]["note"] = "item is corrupted — it cannot be crafted on"
        return digest
    if match["base"]:
        full = data.pool(match["base"], ilvl=ilvl)
        for gen in ("prefix", "suffix"):
            ladders = full[gen]
            shown = ladders[:POOL_LADDERS_SHOWN]
            digest["pool"][gen] = [
                {"type": lad["type"],
                 "best": ({"tier": lad["best"]["tier"],
                           "text": lad["best"]["text"],
                           "ilvl": lad["best"]["ilvl"]}
                          if lad["best"] else None),
                 "tiers_total": len(lad["tiers"]),
                 "weight": lad["weight"]}
                for lad in shown]
            if len(ladders) > len(shown):
                digest["pool"]["note"] += (
                    f"{gen}: top {len(shown)} of {len(ladders)} ladders "
                    f"by spawn weight shown. ")
    else:
        digest["pool"]["note"] = (
            "base not found in the dataset — pool unavailable "
            "(essences/bench/recipes keyed on the Item Class line)")
    if cls:
        bench_mods, bench_actions = data.bench_for(cls)
        digest["bench"]["mods"] = [
            {"text": b["t"], "gen": b["gen"], "cost": b["cost"],
             "master": b["master"]} for b in bench_mods]
        digest["bench"]["actions"] = [
            {"text": b["t"], "cost": b["cost"]} for b in bench_actions]
    return digest


# ------------------------------------------------------------- rendering

def _one_line(text):
    """Hybrid mod templates embed '\\n'; render them on one row."""
    return text.replace("\n", " / ")


# What a leveling party benches most, in display order; everything else
# after, alphabetically. Presentation only — the LLM payload is uncut.
_BENCH_KEYWORDS = ("maximum Life", "Resistance", "increased Movement Speed",
                   "Damage", "maximum Mana", "Attributes")


def _bench_rank(b):
    text = b["text"]
    for i, kw in enumerate(_BENCH_KEYWORDS):
        if kw in text:
            return (i, "\n" in text, b["gen"], text)
    return (len(_BENCH_KEYWORDS), "\n" in text, b["gen"], text)


_ORIGIN_SHORT = {"implicit": "imp", "enchant": "ench", "scourge": "scrg",
                 "crucible": "cruc", "rune": "rune", "Hidden": "hid",
                 "essence": "ess", "special": "spec", "bench": "bench"}


def _mod_row(r):
    if not r["key"] and r["origin"] is None:
        return f"    ?         {r['line']}"
    tier = (f"T{r['tier']}/{r['tier_of']}" if r["tier"]
            else _ORIGIN_SHORT.get(r["origin"], "?"))
    gen = r["gen"][:3] if r["gen"] and r["gen"] != "?" else "?"
    flag = " (ambiguous)" if r["ambiguous"] else ""
    if r["origin"] == "fractured":
        flag += " (fractured)"
    return f"  {gen:>3} {tier:>6}  {r['line']}{flag}"


def render_digest(digest):
    """Aligned plain-text digest (CLI + overlay meta area)."""
    item = digest["item"]
    lines = [f"{item['name']} — {item['base']} ({item['rarity']}, "
             f"ilvl {item['ilvl']}, {item['cls'] or 'unknown class'})"]
    if item["corrupted"]:
        lines.append("CORRUPTED — cannot be crafted on")
    if digest["mods"]:
        lines.append("mods:")
        lines.extend(_mod_row(r) for r in digest["mods"])
    open_ = digest["open"]
    est = " (estimate)" if open_.get("uncertain") else ""
    lines.append(f"open affixes{est}: {open_['prefix']} prefix, "
                 f"{open_['suffix']} suffix")
    for gen in ("prefix", "suffix"):
        top = [lad for lad in digest["pool"][gen] if lad["best"]][:6]
        if top:
            lines.append(f"best rollable {gen}es here:")
            lines.extend(
                f"  T{lad['best']['tier']} {_one_line(lad['best']['text'])}"
                f"  [w {lad['weight']}]" for lad in top)
    if digest["pool"]["note"]:
        lines.append(f"note: {digest['pool']['note'].strip()}")
    if digest["essences"]:
        shown = digest["essences"][:10]
        more = len(digest["essences"]) - len(shown)
        lines.append("best usable essences:"
                     + (f" (+{more} more families)" if more else ""))
        lines.extend(f"  {e['name']}: {_one_line(e['text'])}"
                     for e in shown)
    if digest["bench"]["mods"]:
        bench = sorted(digest["bench"]["mods"], key=_bench_rank)
        lines.append("bench (top tiers):")
        lines.extend(f"  {b['gen'][:3]:>3}  {_one_line(b['text'])}"
                     f"  [{b['cost']}]" for b in bench[:12])
    if digest["bench"]["actions"]:
        lines.append("bench actions: " + "; ".join(
            f"{b['text']} [{b['cost']}]"
            for b in digest["bench"]["actions"][:8]))
    if digest["recipes"]:
        lines.append("applicable methods: "
                     + "; ".join(r["name"] for r in digest["recipes"]))
    phases = digest.get("order", {}).get("phases", [])
    if phases:
        lines.append("order of operations: " + " → ".join(
            p["name"].lower() for p in sorted(phases, key=lambda p: p["n"]))
            + "  (details: docs/CRAFTING_GUIDELINES.md)")
    level = digest.get("ctx", {}).get("level")
    stages = digest.get("methods", {}).get("stages", [])
    if level and stages:
        stage = max((s for s in stages if s.get("from_level", 1) <= level),
                    key=lambda s: s.get("from_level", 1), default=None)
        if stage:
            lines.append(f"stage [{stage['range']}]: {stage['default']}")
    lines.append(f"[data: RePoE {digest['data_version']}]")
    return "\n".join(lines)


def render_plan(plan):
    lines = [f"plan ({plan.get('confidence', '?')} confidence): "
             f"{plan['assessment']}"]
    for i, s in enumerate(plan["steps"], 1):
        extra = "".join(
            f" [{k}: {s[k]}]" for k in ("cost", "risk") if s.get(k))
        lines.append(f"  {i}. {s['action']} — {s['why']}{extra}")
    lines.append(f"stop when: {plan['stop_when']}")
    if plan.get("alternatives"):
        lines.append(f"alternatives: {plan['alternatives']}")
    return "\n".join(lines)


# --------------------------------------------------------------- advise

def advise(parsed, ctx=None, data=None, recipes=None, guidelines=None,
           order=None, methods=None, llm_factory=None):
    """Digest + LLM plan for one parsed item. See module docstring."""
    ctx = ctx or {}
    if data is None:
        data = pool_mod.CraftData.load()
    digest = build_digest(parsed, ctx, data, recipes=recipes,
                          guidelines=guidelines, order=order,
                          methods=methods)
    text = render_digest(digest)

    plan, note = None, ""
    try:
        # Deferred so the copilot works when the llm package/SDK is absent.
        from llm.client import LLM, LLMDisabled, LLMError
        try:
            llm = llm_factory() if llm_factory else LLM("standard")
            plan = llm.complete(
                system=SYSTEM,
                messages="DATA:\n" + json.dumps(digest, ensure_ascii=False),
                max_tokens=1000,
                feature="craft_copilot",
                json_schema=PLAN_SCHEMA,
            )
            text = text + "\n\n" + render_plan(plan)
        except (LLMDisabled, LLMError) as exc:
            note = f"LLM plan skipped: {exc}"
    except ImportError as exc:
        note = f"LLM plan skipped: {exc}"
    if note:
        text = text + "\n\n" + note
    return {"digest": digest, "text": text, "plan": plan, "llm_note": note}
