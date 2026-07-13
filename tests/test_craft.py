"""Headless tests for the crafting copilot (addendum 5G, task 29):
tools/refresh_repoe.py compile step, craft/pool.py (pool, matcher),
craft/recipes.py, craft/copilot.py degrade + fake-LLM paths, and the
itemtext mod_tags extension. Offline: raw RePoE inputs are the small
fixtures under tests/fixtures_craft/; no network, no Qt, no API keys.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "tools"),
                os.path.join(ROOT, "overlay")]

# Neutralise the environment BEFORE anything constructs an LLM.
for _k in ("POE_TOOLS_LLM", "ANTHROPIC_API_KEY"):
    os.environ.pop(_k, None)

import itemtext                                  # noqa: E402
import refresh_repoe                             # noqa: E402
from craft import copilot, recipes               # noqa: E402
from craft.pool import (CraftData, _norm, _values_fit,  # noqa: E402
                        canon_class)

FIX = os.path.join(ROOT, "tests", "fixtures_craft")
ITEMS = os.path.join(ROOT, "tests", "fixtures_items")


def _fixture_data():
    raw = refresh_repoe.load_raw(FIX)
    return refresh_repoe.compile_data(raw, game_version="test")


# ------------------------------------------------------------- compiler

compiled = _fixture_data()

# released dup name: highest drop_level wins; unreleased/currency dropped
assert set(compiled["bases"]) == {"Test Boots", "Test Flask"}
assert compiled["bases"]["Test Boots"]["lvl"] == 20
assert compiled["bases"]["Test Boots"]["dom"] == "item"
assert compiled["bases"]["Test Flask"]["dom"] == "flask"

# unique-generation and crafted-domain mods are not in the rollable set
assert "UniqueMod" not in compiled["mods"]
assert "BenchLife" not in compiled["mods"]
assert compiled["mods"]["EssOnlyLife"]["ess"] is True
assert compiled["mods"]["LifeT1"]["t"] == "+(50-59) to maximum Life"

# essences resolve their per-class mod text; bench costs get readable names
assert all(e["mods"]["Boots"] == "+(91-105) to maximum Life"
           for e in compiled["essences"])
bench_mod = [b for b in compiled["bench"] if b["kind"] == "mod"][0]
assert bench_mod["cost"] == "2x Chaos Orb"
assert bench_mod["gen"] == "prefix"
bench_act = [b for b in compiled["bench"] if b["kind"] == "action"][0]
assert bench_act["t"] == "link_sockets=4"

# ------------------------------------------------------- norm / values

assert _norm("+42 to maximum Life") == "+# to maximum Life"
assert _norm("+(50-59) to maximum Life") == "+# to maximum Life"
assert _values_fit("+55 to maximum Life", "+(50-59) to maximum Life")
assert not _values_fit("+42 to maximum Life", "+(50-59) to maximum Life")
assert _values_fit("Instant Recovery", "Instant Recovery")
assert canon_class("Wands") == "Wand"
assert canon_class("Boots") == "Boots"
assert canon_class("Body Armours") == "Body Armour"

# ------------------------------------------------------------------ pool

data = CraftData(compiled)

pool = data.pool("Test Boots", ilvl=15)
life = [lad for lad in pool["prefix"] if lad["type"] == "IncreasedLife"]
assert len(life) == 1
tiers = life[0]["tiers"]
assert [t["tier"] for t in tiers] == [1, 2]
assert tiers[0]["ilvl"] == 30 and not tiers[0]["reachable"]   # T1 gated
assert life[0]["best"]["text"] == "+(20-29) to maximum Life"  # T2 reachable
# flask mods never appear on an item-domain base, essence-only never rolls
assert all(lad["type"] != "FlaskFullInstantRecovery"
           for gen in pool.values() if isinstance(gen, list) for lad in gen)
flask_pool = data.pool("Test Flask", ilvl=20)
assert any(lad["type"] == "FlaskFullInstantRecovery"
           for lad in flask_pool["prefix"])
assert data.tier_of("LifeT1", {"boots", "armour", "default"}) == (1, 2)

# ----------------------------------------------------------------- match

parsed = {
    "item_class": "Boots", "rarity": "Rare", "name": "Doom Stride",
    "base": "Test Boots", "ilvl": 33, "quality": 0, "sockets": 4,
    "links": 4, "corrupted": False,
    "mods": ["+55 to maximum Life", "+31% to Fire Resistance",
             "+18 to maximum Mana", "+22 to maximum Life"],
    "mod_tags": ["", "", "implicit", "crafted"],
}
m = data.match_item(parsed)
assert m["base"] == "Test Boots" and m["cls"] == "Boots"
assert m["rows"][0]["key"] == "LifeT1" and m["rows"][0]["tier"] == 1
assert m["rows"][0]["origin"] == "roll"
assert m["rows"][1]["gen"] == "suffix"
assert m["rows"][2]["origin"] == "implicit"      # not an affix
assert m["rows"][3]["origin"] == "bench"         # crafted -> bench table
assert m["rows"][3]["gen"] == "prefix"
assert m["counts"] == {"prefix": 2, "suffix": 1, "unknown": 0}
assert m["open"] == {"prefix": 1, "suffix": 2}
assert not m["uncertain"]

# essence-only values identify as essence origin; garbage goes unknown
m2 = data.match_item(dict(parsed, mods=["+95 to maximum Life", "gibberish"],
                          mod_tags=["", ""]))
assert m2["rows"][0]["key"] == "EssOnlyLife"
assert m2["rows"][0]["origin"] == "essence"
assert m2["rows"][1]["key"] is None
assert m2["counts"]["unknown"] == 1 and m2["uncertain"]

# hybrid: two consecutive lines collapse into one mod, counted once
m3 = data.match_item(dict(
    parsed, mods=["23% increased Energy Shield",
                  "10% increased Stun and Block Recovery"],
    mod_tags=["", ""]))
assert m3["rows"][0]["key"] == m3["rows"][1]["key"] == "HybridStun"
assert m3["counts"] == {"prefix": 1, "suffix": 0, "unknown": 0}

# magic rarity caps affixes at 1/1
m4 = data.match_item(dict(parsed, rarity="Magic",
                          mods=["+55 to maximum Life"], mod_tags=[""]))
assert m4["open"] == {"prefix": 0, "suffix": 1}

# ------------------------------------------------------ essences / bench

es = data.essences_for("Boots", 30)
assert len(es) == 1 and es[0]["name"] == "Deafening Essence of Greed"
es50 = data.essences_for("Boots", 50)   # Muttering capped at ilvl 45
assert es50[0]["tier"] == 7
assert data.essences_for("Wand", 30) == []
bench_rows, bench_actions = data.bench_for("Boots")
assert bench_rows[0]["t"] == "+(15-25) to maximum Life"
assert len(bench_actions) == 1
assert data.bench_for("Wand") == ([], [])

# --------------------------------------------------------------- recipes

recs = recipes.load()
assert recs, "data/craft_recipes.json missing or empty"
boots_recs = {r["id"] for r in recipes.applicable(recs, cls="Boots",
                                                  level=12)}
assert "movespeed_boots" in boots_recs
assert "plus1_caster_weapon" not in boots_recs
assert "alt_aug_regal" not in boots_recs         # level 12 < 15
wand_recs = {r["id"] for r in recipes.applicable(recs, cls="Wand", level=8)}
assert "plus1_caster_weapon" in wand_recs
assert "movespeed_boots" not in wand_recs
flask_recs = {r["id"] for r in recipes.applicable(recs, cls="Utility Flask",
                                                  level=20)}
assert "flask_suffixes" in flask_recs
assert "magic_beats_bad_rare" not in flask_recs  # gear group excludes flasks
helm_recs = {r["id"] for r in recipes.applicable(recs, cls="Helmet",
                                                 level=20)}
assert "minion_plus1_helmet" in helm_recs
boots30 = {r["id"] for r in recipes.applicable(recs, cls="Boots", level=30)}
assert "orb_of_binding" in boots30                # gated to 25+
assert "orb_of_binding" not in boots_recs         # level 12 too early
assert "vendor_shopping" in flask_recs            # applies_to any

guidelines = recipes.load_guidelines()
assert guidelines and all(g["id"] and g["text"] for g in guidelines)
assert len({g["id"] for g in guidelines}) == len(guidelines)

order = recipes.load_order()
assert [p["n"] for p in order["phases"]] == list(range(1, 8))
assert order["phases"][0]["name"] == "Base"
assert order["phases"][-1]["name"] == "Corrupt"
assert order["rules"] and all(isinstance(r, str) for r in order["rules"])

methods = recipes.load_methods()
assert methods["choose"] and methods["methods"]
assert len({m["id"] for m in methods["methods"]}) == len(methods["methods"])
assert all(m["use_when"] and m["avoid_when"] and m["where"]
           for m in methods["methods"])
assert [s["from_level"] for s in methods["stages"]] == [1, 68, 76]

# --------------------------------------------------------------- copilot

class FakePlanLLM:
    def complete(self, system, messages, max_tokens, feature,
                 json_schema=None):
        assert feature == "craft_copilot"
        assert json_schema is not None
        assert "DATA:" in messages
        return {"assessment": "solid boots", "steps":
                [{"action": "bench a resist", "why": "open suffix",
                  "cost": "2x Chaos Orb"}],
                "stop_when": "resists capped", "confidence": "high"}


res = copilot.advise(parsed, {"level": 30}, data=data, recipes=recs,
                     llm_factory=FakePlanLLM)
assert res["plan"]["assessment"] == "solid boots"
assert "plan (high confidence)" in res["text"]
assert "bench a resist" in res["text"]
assert res["llm_note"] == ""
assert res["digest"]["open"]["prefix"] == 1
assert res["digest"]["ctx"]["level"] == 30
assert res["digest"]["guidelines"], "guidelines missing from LLM payload"
assert res["digest"]["order"]["rules"], "order rules missing from payload"
assert "order of operations: base → quality → sockets" in res["text"]
assert res["digest"]["methods"]["choose"], "methods matrix missing"
assert "stage [campaign (to ~68)]" in res["text"]   # ctx level 30

res_maps = copilot.advise(parsed, {"level": 70}, data=data, recipes=recs,
                          llm_factory=FakePlanLLM)
assert "stage [early maps (68-75)]" in res_maps["text"]

def _disabled_factory():
    from llm.client import LLMDisabled
    raise LLMDisabled("test kill switch")

res2 = copilot.advise(parsed, {}, data=data, recipes=recs,
                      llm_factory=_disabled_factory)
assert res2["plan"] is None
assert "LLM plan skipped" in res2["llm_note"]
assert "LLM plan skipped" in res2["text"]
assert "Doom Stride" in res2["text"]             # digest still rendered

# corrupted items advertise themselves as uncraftable
res3 = copilot.advise(dict(parsed, corrupted=True), {}, data=data,
                      recipes=recs, llm_factory=_disabled_factory)
assert "corrupted" in res3["digest"]["pool"]["note"]
assert res3["digest"]["pool"]["prefix"] == []

# ------------------------------------------------- itemtext mod_tags

wand = itemtext.parse(open(os.path.join(ITEMS, "magic_wand.txt"),
                           encoding="utf-8").read())
assert wand["mod_tags"] == ["implicit", "", ""]
assert len(wand["mods"]) == len(wand["mod_tags"])

# ------------------------------- integration against the real dataset

real_path = os.path.join(ROOT, "data", "repoe_craft.json")
if os.path.exists(real_path):
    real = CraftData.load(real_path)
    boots = itemtext.parse(open(os.path.join(ITEMS, "rare_boots.txt"),
                                encoding="utf-8").read())
    rm = real.match_item(boots)
    assert rm["base"] == "Velvet Slippers"
    assert rm["counts"] == {"prefix": 3, "suffix": 2, "unknown": 0}
    assert rm["open"] == {"prefix": 0, "suffix": 1}
    rp = real.pool("Velvet Slippers", ilvl=33)
    assert rp["prefix"] and rp["suffix"]
    assert any("maximum Life" in e["text"]
               for e in real.essences_for("Boots", 33))
else:
    print("NOTE: data/repoe_craft.json absent — integration slice skipped "
          "(run tools/refresh_repoe.py)")

print("ALL TESTS PASSED")
