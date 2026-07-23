"""Headless tests: clipboard item text parser + campaign leveling rules."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "overlay")]

import itemtext                                 # noqa: E402
import item_rules                               # noqa: E402
from item_rules import evaluate, TAKE, SKIP, CHECK  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures_items")


def fixture(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


# ------------------------------------------------------------ parser: boots
boots = itemtext.parse(fixture("rare_boots.txt"))
assert boots is not None
assert boots["item_class"] == "Boots" and boots["rarity"] == "Rare"
assert boots["name"] == "Gale Trail" and boots["base"] == "Velvet Slippers"
assert boots["ilvl"] == 33 and boots["req_level"] == 22
assert boots["quality"] == 12
assert boots["sockets"] == 4 and boots["links"] == 3, "B-B-R G -> 4 sockets, 3 linked"
assert boots["socket_colors"] == {"R": 1, "G": 1, "B": 2, "W": 0, "A": 0}
assert boots["link_groups"] == ["BBR", "G"]
assert boots["mods"] == [
    "+42 to maximum Life",
    "+35% to Fire Resistance",
    "+12% to Cold Resistance",
    "20% increased Movement Speed",
    "+24 to maximum Mana",
]
assert boots["props"] == {"life": 42, "movespeed": 20,
                          "res": {"fire": 35, "cold": 12,
                                  "lightning": 0, "chaos": 0}}

# ------------------------------------------------------- parser: magic wand
wand = itemtext.parse(fixture("magic_wand.txt"))
assert wand["item_class"] == "Wands" and wand["rarity"] == "Magic"
assert wand["name"] == "Frosted Driftwood Wand of Shining"
assert wand["base"] == wand["name"], "magic names aren't split into a base"
assert wand["ilvl"] == 7 and wand["req_level"] == 5
assert wand["sockets"] == 2 and wand["links"] == 2
assert wand["weapon_dps"]["physical"] == 9.8
assert wand["mods"] == ["11% increased Spell Damage",       # (implicit) stripped
                        "Adds 1 to 3 Cold Damage",
                        "5% increased Light Radius"]
assert wand["props"]["life"] == 0 and wand["props"]["movespeed"] == 0
assert sum(wand["props"]["res"].values()) == 0

# ------------------------------------------------ parser: normal 4L, Superior
chest = itemtext.parse(fixture("normal_chest.txt"))
assert chest["rarity"] == "Normal" and chest["item_class"] == "Body Armours"
assert chest["name"] == "Superior Shabby Jerkin"
assert chest["base"] == "Shabby Jerkin", "'Superior ' prefix stripped from base"
assert chest["quality"] == 8
assert chest["sockets"] == 4 and chest["links"] == 4
assert chest["link_groups"] == ["GGGB"]
assert chest["mods"] == [] and chest["props"]["life"] == 0

# ---------------------------------------------------------- parser: unique
uniq = itemtext.parse(fixture("unique_boots.txt"))
assert uniq["rarity"] == "Unique" and uniq["name"] == "Wanderlust"
assert uniq["base"] == "Wool Shoes" and uniq["ilvl"] == 12
assert "Cannot be Frozen" in uniq["mods"], "digitless mods kept"
assert "+13 to maximum Life" in uniq["mods"]
assert not any("Wanderlust" in m for m in uniq["mods"]), "flavour text excluded"
assert uniq["props"]["life"] == 13 and uniq["props"]["movespeed"] == 20

# -------------------------------------------------------- parser: currency
cur = itemtext.parse(fixture("currency_stack.txt"))
assert cur is not None, "currency must parse without crashing"
assert cur["rarity"] == "Currency" and cur["name"] == "Chaos Orb"
assert cur["item_class"] == "Stackable Currency"
assert cur["stack_size"] == "17/20"
assert cur["mods"] == [], "description/help text is not a mod"

# ------------------------------------------------------------- parser: gem
gem = itemtext.parse(fixture("gem.txt"))
assert gem["rarity"] == "Gem" and gem["name"] == "Frostbolt"
assert gem["quality"] == 7
assert gem["req_level"] == 10, "gem 'Level: 3' must not leak into req_level"
assert gem["ilvl"] == 0
assert gem["mods"] == [], \
    "gem stat-description lines ('Deals 15 to 23 Cold Damage') are not mods"

# -------------------------------------- parser: amulet, spread + hybrid res
amu = itemtext.parse(fixture("rare_amulet.txt"))
assert amu["name"] == "Grim Charm" and amu["base"] == "Jade Amulet"
assert amu["ilvl"] == 35 and amu["req_level"] == 28
assert "+22 to Dexterity" in amu["mods"], "implicit kept, tag stripped"
assert amu["props"]["life"] == 28
assert amu["props"]["res"] == {"fire": 25, "cold": 11,
                               "lightning": 25, "chaos": 17}, \
    "all-res spreads to the 3 elements; dual-res counts both"

# --------------------------- parser: catalyst quality + '(Hidden)' mod tag
cat_amu = itemtext.parse("""Item Class: Amulets
Rarity: Rare
Grim Charm
Jade Amulet
--------
Quality (Attribute Modifiers): +20% (augmented)
--------
Item Level: 60
--------
+30 to Dexterity (implicit)
--------
+50 to maximum Life
Grants Level 20 Aspect of the Cat Skill (Hidden)
""")
assert cat_amu["quality"] == 20, "catalyst quality variants count as quality"
assert "Grants Level 20 Aspect of the Cat Skill" in cat_amu["mods"], \
    "'(Hidden)' is stripped like every other mod tag"

# ------------------- parser: digitless mods (corrupted blood, veiled, ...)
jewel = itemtext.parse("""Item Class: Jewels
Rarity: Rare
Ancient Trial
Cobalt Jewel
--------
Item Level: 75
--------
+7% to Fire Resistance
Corrupted Blood cannot be inflicted on you (implicit)
--------
Corrupted
""")
assert "Corrupted Blood cannot be inflicted on you" in jewel["mods"], \
    "corruption implicits without digits are still mods"
assert jewel["corrupted"] is True

veiled = itemtext.parse("""Item Class: Gloves
Rarity: Rare
Sorrow Grip
Silk Gloves
--------
Item Level: 70
--------
+40 to maximum Life
Veiled Suffix
""")
assert "Veiled Suffix" in veiled["mods"], "veiled markers are kept"

flask = itemtext.parse("""Item Class: Life Flasks
Rarity: Magic
Bubbling Divine Life Flask of Staunching
--------
Quality: +20%
--------
Item Level: 60
--------
Used when Charges reach full (enchant)
--------
Immunity to Bleeding and Corrupted Blood during Effect
""")
assert "Used when Charges reach full" in flask["mods"], \
    "flask trigger enchants are mods"
assert flask["quality"] == 20

# --------------------------------------- parser: relic (foil) unique rarity
relic = itemtext.parse(fixture("unique_boots.txt").replace(
    "Rarity: Unique", "Rarity: Relic"))
assert relic is not None and relic["rarity"] == "Relic"
assert relic["name"] == "Wanderlust"

# ---------------------------- parser: divination cards carry no fake mods
card = itemtext.parse("""Item Class: Divination Cards
Rarity: Divination Card
Rain of Chaos
--------
Stack Size: 3/8
--------
8x Chaos Orb
--------
Fortune favours the brave.
""")
assert card["rarity"] == "Divination Card" and card["stack_size"] == "3/8"
assert card["mods"] == [], "'8x Chaos Orb' is a reward line, not a mod"

# --------------------------------------------------------- parser: garbage
assert itemtext.parse(fixture("garbage.txt")) is None
assert itemtext.parse("") is None
assert itemtext.parse("   \n\n  ") is None
assert itemtext.parse(None) is None
assert itemtext.parse("Rarity: Rare") is None, "header without a name line"

# =================================================================== rules
CTX = {"level": 20, "act": 3, "links_best": 3, "build": None}


def synth(item_class="Rings", rarity="Rare", life=0, ms=0, res=None,
          links=0, sockets=0, name="T", mods=None, groups=None, pdps=0,
          edps=0):
    r = {"fire": 0, "cold": 0, "lightning": 0, "chaos": 0}
    r.update(res or {})
    return {"item_class": item_class, "rarity": rarity, "name": name,
            "base": "T", "ilvl": 30, "sockets": sockets, "links": links,
            "mods": mods or [], "link_groups": groups or [],
            "weapon_dps": {"physical": pdps, "elemental": edps,
                           "total": pdps + edps},
            "props": {"life": life, "movespeed": ms, "res": r}}


# every verdict is one of the enum values with a non-empty reason
for parsed in (None, synth(), synth(rarity="Currency"), synth(rarity="Gem"),
               synth(rarity="Unique"), synth(item_class="Bows"),
               synth(links=6)):
    v, reason = evaluate(parsed, CTX)
    assert v in (TAKE, SKIP, CHECK) and isinstance(reason, str) and reason

# garbage / non-item
assert evaluate(None, CTX)[0] == SKIP

# currency + div cards always TAKE
assert evaluate(synth(item_class="Stackable Currency", rarity="Currency"),
                CTX)[0] == TAKE
assert evaluate(synth(item_class="Divination Cards",
                      rarity="Divination Card"), CTX)[0] == TAKE

# gems are a build question
assert evaluate(synth(item_class="Skill Gems", rarity="Gem"), CTX)[0] == CHECK

# link upgrade beats current best (even on a plain white base)
v, reason = evaluate(synth(rarity="Normal", item_class="Body Armours",
                           links=4), CTX)
assert v == TAKE and "4-link" in reason
# ...but not when it merely ties
assert evaluate(synth(rarity="Normal", item_class="Body Armours", links=3),
                CTX)[0] == SKIP

# movement-speed boots before act 4
v, reason = evaluate(synth(item_class="Boots", ms=15), CTX)
assert v == TAKE and "15%" in reason
assert evaluate(synth(item_class="Boots", ms=15),
                {**CTX, "act": 4})[0] != TAKE, "ms rule is acts 1-3 only"
assert evaluate(synth(item_class="Boots", ms=8), CTX)[0] == SKIP, \
    "sub-10% movespeed with nothing else is trash"
# unique boots with movespeed still TAKE early (speed rule outranks unique)
assert evaluate(synth(item_class="Boots", rarity="Unique", ms=20),
                CTX)[0] == TAKE

# uniques (non-boots or past act 3) -> CHECK
assert evaluate(synth(rarity="Unique", item_class="Amulets"), CTX)[0] == CHECK
assert evaluate(synth(rarity="Unique", item_class="Boots", ms=20),
                {**CTX, "act": 5})[0] == CHECK

# weapons
assert evaluate(synth(item_class="Two Hand Axes", rarity="Rare"),
                CTX)[0] == CHECK
assert evaluate(synth(item_class="Wands", rarity="Magic"),
                {**CTX, "act": 2})[0] == CHECK
assert evaluate(synth(item_class="Wands", rarity="Magic"),
                {**CTX, "act": 5})[0] == SKIP

# life/res scoring against the act budget (act 3 slot share ~28.75)
v, reason = evaluate(synth(life=45, res={"fire": 30, "cold": 20}), CTX)
assert v == TAKE and "Act 3" in reason and "45 life" in reason
assert evaluate(synth(life=5), CTX)[0] == SKIP
assert evaluate(synth(life=25), CTX)[0] == CHECK, "middle band -> CHECK"

# act scaling: the same item is a TAKE early and a SKIP late
mid = synth(life=20, res={"lightning": 10})               # score 30
assert evaluate(mid, {**CTX, "act": 1})[0] == TAKE
assert evaluate(mid, {**CTX, "act": 4})[0] == CHECK
assert evaluate(mid, {**CTX, "act": 9})[0] == SKIP
# out-of-range acts clamp instead of crashing
assert evaluate(mid, {**CTX, "act": 0})[0] == TAKE
assert evaluate(mid, {**CTX, "act": 99})[0] == SKIP
# missing ctx keys fall back to defaults
assert evaluate(mid, {})[0] == TAKE

# injected budget table is honored (deterministic, file-independent)
tiny = {1: {"life": 8, "res_total": 8}}
assert evaluate(synth(life=3), {**CTX, "act": 1}, budget=tiny)[0] == TAKE

# budget file: 10 acts, monotonically increasing, non-numeric keys ignored
table = item_rules.load_budget()
assert sorted(table) == list(range(1, 11))
for act in range(2, 11):
    assert table[act]["life"] >= table[act - 1]["life"]
    assert table[act]["res_total"] >= table[act - 1]["res_total"]

# ------------------------------------------- fixtures through the rules
assert evaluate(boots, CTX)[0] == TAKE, "20% ms boots in act 3"
assert evaluate(boots, {**CTX, "act": 5})[0] == TAKE, \
    "still strong life/res for act 5"
assert evaluate(chest, CTX)[0] == TAKE, "4L beats a 3L best"
assert evaluate(chest, {**CTX, "links_best": 4})[0] == SKIP
assert evaluate(uniq, {**CTX, "act": 6})[0] == CHECK
assert evaluate(relic, {**CTX, "act": 6})[0] == CHECK, \
    "relic (foil) uniques get the unique CHECK instead of vanishing"
assert evaluate(cur, CTX)[0] == TAKE
assert evaluate(gem, CTX)[0] == CHECK
assert evaluate(wand, {**CTX, "act": 2})[0] == CHECK
assert evaluate(wand, {**CTX, "act": 6})[0] == SKIP
assert evaluate(amu, CTX)[0] == TAKE

# ---------------------------------------- matched-build pickup profiles
profiles = item_rules.load_build_profiles()
assert set(profiles) == {
    "allflame-carry-inquisitor", "allflame-aurabot-ascendant",
    "allflame-banner-champion", "allflame-drugger-pathfinder",
}

carry_ctx = {**CTX, "build": "allflame-carry-inquisitor"}
banner_ctx = {**CTX, "act": 3, "links_best": 4,
              "build": "allflame-banner-champion"}
drugger_ctx = {**CTX, "act": 3, "links_best": 4,
               "build": "allflame-drugger-pathfinder"}

assert evaluate(synth(rarity="Gem", name="Armageddon Brand"),
                carry_ctx)[0] == TAKE
assert evaluate(synth(rarity="Gem", name="Frostbolt"),
                carry_ctx)[0] == SKIP
assert evaluate(synth(rarity="Normal", item_class="Body Armours",
                      links=4, groups=["RRRR"]), banner_ctx)[0] == TAKE
v, reason = evaluate(synth(rarity="Normal", item_class="Body Armours",
                           links=5, groups=["GGGGG"]), banner_ctx)
assert v == TAKE and "every gem fits every socket colour" in reason
assert evaluate(synth(rarity="Normal", item_class="Body Armours",
                      links=4, groups=["RRRR"]), drugger_ctx)[0] == SKIP, \
    "off-colour tie is usable but not a pickup upgrade"
assert evaluate(synth(rarity="Normal", item_class="Body Armours",
                      links=4, groups=["RRRR"]),
                {**drugger_ctx, "links_best": 3})[0] == TAKE, \
    "off-colour link upgrades remain fully usable in 3.29"
assert evaluate(synth(rarity="Normal", item_class="Body Armours",
                      links=4, groups=["WWWW"]), banner_ctx)[0] == SKIP, \
    "white sockets work but do not earn the matching-colour quality bonus"
assert evaluate(synth(item_class="Bows", edps=200), banner_ctx)[0] == SKIP
assert evaluate(synth(item_class="Two Hand Axes", pdps=90),
                banner_ctx)[0] == TAKE
assert evaluate(synth(item_class="Two Hand Axes", pdps=40),
                banner_ctx)[0] == SKIP
assert evaluate(synth(item_class="Bows", edps=70),
                drugger_ctx)[0] == TAKE
assert evaluate(synth(item_class="Quivers",
                      mods=["Adds 3 to 40 Lightning Damage to Attacks"]),
                drugger_ctx)[0] == TAKE
assert evaluate(synth(rarity="Gem", name="Returning Projectiles Support"),
                drugger_ctx)[0] == TAKE, "' Support' suffix is normalized"
assert evaluate(synth(rarity="Normal", item_class="Sceptres",
                      name="Goat's Horn"), carry_ctx)[0] == TAKE
assert evaluate(synth(rarity="Unique", name="Ghostwrithe"),
                carry_ctx)[0] == TAKE

print("ALL TESTS PASSED")
print(f"  parsed {len(os.listdir(FIX))} fixtures; "
      f"boots verdict: {evaluate(boots, CTX)}")
