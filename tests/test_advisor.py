"""Headless tests: PoB item/keystone extraction, party uniques wishlist,
and the advisor suite (summarize / advise / exposure) with a mocked LLM.
Offline: no network, no Qt, no real API keys."""
import json
import os
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "buildgen")]

import party                                   # noqa: E402
import pob                                     # noqa: E402
from advisor import advise, exposure, summarize  # noqa: E402

os.environ["POE_TOOLS_LLM"] = "off"   # belt & braces: never a real client


# ------------------------------------------------------ synthetic PoB XML
def sample_pob(class_name, asc, main_gem, uniques=(), notes=""):
    """Same shape as test_core's sample, plus optional Items/Notes."""
    root = ET.Element("PathOfBuilding")
    ET.SubElement(root, "Build", {"level": "92", "className": class_name,
                                  "ascendClassName": asc})
    skills = ET.SubElement(root, "Skills")
    s1 = ET.SubElement(skills, "SkillSet", {"title": "Act 1-2 leveling"})
    sk = ET.SubElement(s1, "Skill", {"label": "Main"})
    for g in [main_gem, "Arcane Surge Support", "Added Lightning Damage Support"]:
        ET.SubElement(sk, "Gem", {"nameSpec": g})
    s2 = ET.SubElement(skills, "SkillSet", {"title": "Endgame"})
    sk2 = ET.SubElement(s2, "Skill", {"label": "6-link"})
    for g in ["Fireball", "Spell Echo Support", "Fire Penetration Support"]:
        ET.SubElement(sk2, "Gem", {"nameSpec": g})
    ET.SubElement(sk2, "Gem", {"nameSpec": "Disabled Gem", "enabled": "false"})
    tree = ET.SubElement(root, "Tree")
    ET.SubElement(tree, "Spec", {"title": "Level ~30",
                                 "nodes": ",".join(map(str, range(100, 130)))})
    ET.SubElement(tree, "Spec", {"title": "Final",
                                 "nodes": ",".join(map(str, range(100, 190)))})
    if uniques:
        items = ET.SubElement(root, "Items")
        for i, (name, base) in enumerate(uniques, 1):
            it = ET.SubElement(items, "Item", {"id": str(i)})
            it.text = (f"\nRarity: UNIQUE\n{name}\n{base}\n"
                       f"Unique ID: fixture{i}\nItem Level: 20\n"
                       "+20 to maximum Life\n")
    if notes:
        ET.SubElement(root, "Notes").text = notes
    return root


# --------------------------------------------------------- extract_items
r = ET.Element("PathOfBuilding")
items_el = ET.SubElement(r, "Items")
for i, block in enumerate([
    "Rarity: UNIQUE\nTabula Rasa\nSimple Robe\nUnique ID: aaa\n"
    "Sockets: W-W-W-W-W-W\nItem Level: 20",
    "Rarity: RARE\nWhispering Goad\nImbued Wand\nItem Level: 71\n"
    "+1 to Level of all Spell Skill Gems",
    "Rarity: MAGIC\nSeething Divine Life Flask of Staunching\nQuality: 20",
    "Rarity: NORMAL\nDriftwood Wand\nItem Level: 1",
], 1):
    it = ET.SubElement(items_el, "Item", {"id": str(i)})
    it.text = "\n" + block + "\n"
# no rarity line -> first line is the name
it = ET.SubElement(items_el, "Item", {"id": "5"})
it.text = "Kaom's Heart\nGlorious Plate"

assert pob.extract_items(r) == [
    {"name": "Tabula Rasa", "base": "Simple Robe", "rarity": "UNIQUE"},
    {"name": "Whispering Goad", "base": "Imbued Wand", "rarity": "RARE"},
    {"name": "Seething Divine Life Flask of Staunching", "base": None,
     "rarity": "MAGIC"},
    {"name": "Driftwood Wand", "base": None, "rarity": "NORMAL"},
    {"name": "Kaom's Heart", "base": "Glorious Plate", "rarity": None},
]
assert pob.extract_items(ET.Element("PathOfBuilding")) == [], \
    "no Items element -> empty list"

# ------------------------------------------------------ extract_keystones
ks_root = sample_pob("Witch", "Elementalist", "Rolling Magma",
                     uniques=[("Tabula Rasa", "Simple Robe")],
                     notes="Grab Elemental Overload early; respec out of "
                           "Eldritch Battery at maps.")
cfg = ET.SubElement(ks_root, "Config")
ET.SubElement(cfg, "Input", {"name": "customMods",
                             "string": "Keystone: Mind Over Matter"})
mal = ET.SubElement(ks_root.find("Items"), "Item", {"id": "9"})
mal.text = "\nRarity: UNIQUE\nMalachai's Simula\nIron Mask\nBlood Magic\n"
assert pob.extract_keystones(ks_root) == [
    "Blood Magic", "Eldritch Battery", "Elemental Overload",
    "Mind Over Matter"]
assert pob.extract_keystones(sample_pob("Witch", "", "Fireball")) == [], \
    "tree-only keystones are unresolvable offline -> empty is legal"

# -------------------- PoB round-trip (mirrors test_core's PoB section so
# -------------------- regressions from the extension surface here)
code = pob.encode(sample_pob("Witch", "Elementalist", "Rolling Magma",
                             uniques=[("Tabula Rasa", "Simple Robe"),
                                      ("Goldrim", "Leather Cap")]))
r2 = pob.decode(code)
info = pob.build_info(r2)
assert info == {"class": "Witch", "ascendancy": "Elementalist", "level": 92}
specs = pob.tree_specs(r2)
assert [len(s["nodes"]) for s in specs] == [30, 90]
sets = pob.skill_sets(r2)
assert sets[1]["groups"][0]["gems"] == ["Fireball", "Spell Echo Support",
                                        "Fire Penetration Support"], \
    "disabled gems must be excluded"
md, notes = pob.make_plan(r2)
assert "Rolling Magma" in md and "+60 vs previous" in md
_lvl_text = ("Rolling Magma – Arcane Surge Support – "
             "Added Lightning Damage Support")
campaign_notes = [row for row in notes if "act" in row]
assert campaign_notes == [{"act": 1, "text": _lvl_text},
                          {"act": 2, "text": _lvl_text}], \
    "'Act 1-2' title covers both acts"
assert pob.extract_items(r2)[0] == {"name": "Tabula Rasa",
                                    "base": "Simple Robe",
                                    "rarity": "UNIQUE"}, \
    "Items must survive the encode/decode round-trip"

# ------------------------------------------------- party uniques wishlist
tmp = tempfile.mkdtemp(prefix="poe_advisor_test_")
try:
    out_dir = os.path.join(tmp, "builds")
    os.makedirs(out_dir)
    manifest = {"members": [
        {"player": "CyrusChar", "me": True,
         "pob": pob.encode(sample_pob(
             "Witch", "Elementalist", "Rolling Magma",
             uniques=[("Tabula Rasa", "Simple Robe"),
                      ("Goldrim", "Leather Cap")]))},
        {"player": "FriendChar",
         "pob": pob.encode(sample_pob("Duelist", "Champion", "Cleave"))},
    ]}
    members = [party.build_member(m, out_dir) for m in manifest["members"]]
    assert [m["class"] for m in members] == ["Witch", "Duelist"]
    assert [u["name"] for u in members[0]["uniques"]] == ["Tabula Rasa",
                                                          "Goldrim"]
    assert members[1]["uniques"] == []

    summ = party.summary_md(members)
    assert "CyrusChar ★" in summ, "existing behavior: 'me' member starred"
    assert "| 1 |" in summ, "existing behavior: per-act gem table present"
    assert "## Uniques wishlist" in summ
    assert "Tabula Rasa (Simple Robe)" in summ and "Goldrim (Leather Cap)" in summ
    assert "- **FriendChar**: —" in summ, "members without uniques show a dash"

    # no uniques anywhere -> section absent (old output preserved)
    plain = [party.build_member(
        {"player": "Solo", "pob": pob.encode(
            sample_pob("Ranger", "Deadeye", "Rain of Arrows"))}, out_dir)]
    assert "Uniques wishlist" not in party.summary_md(plain)

    # ------------------------------------------------------- fake LLM rig
    class FakeLLM:
        def __init__(self, result, fail=False):
            self.result, self.fail, self.calls = result, fail, []

        def complete(self, system, messages, max_tokens, feature,
                     json_schema=None):
            self.calls.append({"system": system, "messages": messages,
                               "max_tokens": max_tokens, "feature": feature,
                               "json_schema": json_schema})
            if self.fail:
                raise RuntimeError("should have degraded before calling")
            return self.result

    # --------------------------------------------------------- summarize
    fake_summary = {"patch": "3.29", "items": [
        {"id": "skill-fireball", "kind": "skill",
         "change": "Fireball deals 20% less damage.", "direction": "nerf",
         "quote": "Fireball now deals 20% less damage", "source": "Skills"},
        {"id": "unique-tabula-rasa", "kind": "unique",
         "change": "Tabula Rasa drop rate increased.", "direction": "buff",
         "quote": "Tabula Rasa is now more common", "source": "Items"},
    ]}
    fake = FakeLLM(dict(fake_summary))
    data = summarize.summarize("Fireball now deals 20% less damage",
                               patch="3.29", llm=fake)
    assert data == fake_summary
    call = fake.calls[0]
    assert call["feature"] == "advisor_summarize"
    assert call["json_schema"] is summarize.SUMMARY_SCHEMA
    assert "Fireball now deals 20% less damage" in call["messages"][0]["content"]
    assert "verbatim" in call["system"], "prompt bakes in evidence quoting"

    # patch backfilled when the model omits it
    noPatch = summarize.summarize("x", patch="3.30",
                                  llm=FakeLLM({"patch": "", "items": []}))
    assert noPatch["patch"] == "3.30"

    # CLI writes the file; LLM factory must be asked for the deep tier
    notes_path = os.path.join(tmp, "notes.txt")
    with open(notes_path, "w", encoding="utf-8") as f:
        f.write("Fireball now deals 20% less damage")
    spath = os.path.join(tmp, "summary.json")
    tiers = []
    old_llm = summarize.LLM
    summarize.LLM = lambda tier: (tiers.append(tier), fake)[1]
    try:
        summarize.main([notes_path, "--out", spath])
    finally:
        summarize.LLM = old_llm
    assert tiers == ["deep"]
    with open(spath, encoding="utf-8") as f:
        assert json.load(f)["items"][0]["id"] == "skill-fireball"

    # degrade: LLMDisabled -> SystemExit with a clear message, no file
    def _disabled(tier):
        raise summarize.LLMDisabled("kill switch")
    out2 = os.path.join(tmp, "nope.json")
    old_llm = summarize.LLM
    summarize.LLM = _disabled
    try:
        summarize.main([notes_path, "--out", out2])
        raise AssertionError("expected SystemExit")
    except SystemExit as e:
        assert "LLM unavailable" in str(e)
    finally:
        summarize.LLM = old_llm
    assert not os.path.exists(out2)

    # ------------------------------------------------------------ advise
    code2 = pob.encode(sample_pob("Duelist", "Champion", "Cleave"))
    verdict_md = ("Witch is weakened [skill-fireball]. Farm Tabula early "
                  "[unique-tabula-rasa].\n"
                  "1. Play a minion build (assumption)")
    fake_adv = FakeLLM(verdict_md)
    tiers = []
    old_llm = advise.LLM
    advise.LLM = lambda tier: (tiers.append(tier), fake_adv)[1]
    adv_out = os.path.join(tmp, "advice.md")
    try:
        advise.main(["--summary", spath, "--pob", code, "--pob", code2,
                     "--out", adv_out])
    finally:
        advise.LLM = old_llm
    assert tiers == ["deep"]
    call = fake_adv.calls[0]
    assert call["feature"] == "advisor_advise" and call["json_schema"] is None
    assert "skill-fireball" in call["messages"][0]["content"]
    assert "Cleave" in call["messages"][0]["content"]
    with open(adv_out, encoding="utf-8") as f:
        adv = f.read()
    assert "Witch" in adv and "Duelist" in adv, "deterministic digests present"
    assert "Tabula Rasa (Simple Robe)" in adv
    assert "[skill-fireball]" in adv, "LLM verdict included"

    # degrade: LLM off -> deterministic digests only, no crash
    def _adv_disabled(tier):
        raise advise.LLMDisabled("no key")
    old_llm = advise.LLM
    advise.LLM = _adv_disabled
    deg_out = os.path.join(tmp, "advice_deg.md")
    try:
        advise.main(["--summary", spath, "--pob", code, "--out", deg_out])
    finally:
        advise.LLM = old_llm
    with open(deg_out, encoding="utf-8") as f:
        deg = f.read()
    assert "LLM unavailable" in deg and "Rolling Magma" in deg
    assert "[skill-fireball]" not in deg

    # degrade: summary file missing -> note, LLM never constructed
    old_llm = advise.LLM
    advise.LLM = FakeLLM(None, fail=True)   # calling it would raise TypeError
    deg2_out = os.path.join(tmp, "advice_deg2.md")
    try:
        advise.main(["--summary", os.path.join(tmp, "missing.json"),
                     "--pob", code, "--out", deg2_out])
    finally:
        advise.LLM = old_llm
    with open(deg2_out, encoding="utf-8") as f:
        assert "no patch summary" in f.read()

    # ---------------------------------------------------------- exposure
    exp_root = pob.decode(code)   # Witch w/ Tabula Rasa + Goldrim
    comps = exposure.collect_components(exp_root)
    assert ("gem", "Rolling Magma") in comps
    assert ("gem", "Fireball") in comps
    assert ("unique", "Tabula Rasa") in comps
    assert "Disabled Gem" not in [n for _, n in comps]

    fake_exp = FakeLLM({"rows": [
        {"component": "Fireball", "change": "Fireball deals 20% less damage.",
         "direction": "nerf", "source": "skill-fireball",
         "quote": "Fireball now deals 20% less damage"},
        {"component": "Tabula Rasa", "change": "Drop rate increased.",
         "direction": "buff", "source": "unique-tabula-rasa",
         "quote": "Tabula Rasa is now more common"},
    ], "verdict": "Moderate exposure: main 6-link nerfed."})
    with open(spath, encoding="utf-8") as f:
        summary = json.load(f)
    md = exposure.report(exp_root, summary, llm=fake_exp)
    call = fake_exp.calls[0]
    assert call["feature"] == "advisor_exposure"
    assert call["json_schema"] is exposure.EXPOSURE_SCHEMA
    assert "- Rolling Magma (gem)" in call["messages"][0]["content"]
    assert "| Fireball (gem) | Fireball deals 20% less damage. | nerf | " \
           "skill-fireball | Fireball now deals 20% less damage |" in md
    assert "| Tabula Rasa (unique) | Drop rate increased. | buff |" in md
    # components the LLM didn't cover -> tagged assumption
    assert "| Rolling Magma (gem) | no patch data | unknown | assumption |" in md
    assert "Moderate exposure: main 6-link nerfed." in md

    # CLI end-to-end with a patched factory (standard tier)
    tiers = []
    fake_exp2 = FakeLLM({"rows": [], "verdict": "All quiet."})
    old_llm = exposure.LLM
    exposure.LLM = lambda tier: (tiers.append(tier), fake_exp2)[1]
    exp_out = os.path.join(tmp, "exposure.md")
    try:
        exposure.main([code, "--summary", spath, "--out", exp_out])
    finally:
        exposure.LLM = old_llm
    assert tiers == ["standard"]
    with open(exp_out, encoding="utf-8") as f:
        assert "All quiet." in f.read()

    # degrade: no summary -> every component 'no patch data', no LLM call
    md = exposure.report(exp_root, None, llm=FakeLLM(None, fail=True))
    assert md.count("no patch data") == len(comps)
    assert "no verdict — no patch summary available" in md
    assert "assumption" not in md, "degraded rows are plain 'no patch data'"

    # degrade: summary present but LLM disabled
    def _exp_disabled(tier):
        raise exposure.LLMDisabled("off")
    old_llm = exposure.LLM
    exposure.LLM = _exp_disabled
    try:
        md = exposure.report(exp_root, summary)
    finally:
        exposure.LLM = old_llm
    assert md.count("no patch data") == len(comps)
    assert "LLM unavailable" in md

    assert exposure.default_out_name({"class": "Witch"}) == "exposure_witch.md"
finally:
    shutil.rmtree(tmp)

print("ALL TESTS PASSED")
print(f"  extract_items on {len(pob.extract_items(r))} synthetic blocks")
print(f"  keystones best-effort: {pob.extract_keystones(ks_root)}")
