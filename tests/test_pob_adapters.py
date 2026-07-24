"""Offline tests for structural PoB adapters and level-aware notes."""
import os
import sys
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "buildgen"),
                os.path.join(ROOT, "overlay")]

import adapters  # noqa: E402
from build_notes import (adapter_id, group_notes, group_passives,  # noqa: E402
                         select_note, select_passives)
import pob  # noqa: E402


def fixture_for(adapter):
    match = adapter["match"]
    root = ET.Element("PathOfBuilding")
    ET.SubElement(root, "Build", {
        "level": "90",
        "className": match["class"],
        "ascendClassName": match["ascendancy"],
    })
    skills = ET.SubElement(root, "Skills")
    for i, title in enumerate(match.get("skill_sets", []), 1):
        ss = ET.SubElement(skills, "SkillSet",
                           {"title": f"{title} {{{i}}}"})
        sk = ET.SubElement(ss, "Skill", {"enabled": "true"})
        gems = match.get("gems", []) if i == 1 else ["Portal"]
        for gem in gems:
            ET.SubElement(sk, "Gem", {"nameSpec": gem})
    tree = ET.SubElement(root, "Tree")
    for i, title in enumerate(match.get("tree_specs", [])):
        ET.SubElement(tree, "Spec", {
            "title": title,
            "nodes": ",".join(str(n) for n in range(100, 110 + i)),
        })
    return root


catalog = adapters.load_catalog()
assert len(catalog) == 4
banner_adapter = next(
    adapter for adapter in catalog
    if adapter["id"] == "allflame-banner-champion")
banner_steps = banner_adapter["gem_checklist"]
assert banner_steps[0]["items"] == ["Splitting Steel"]
assert all("Ground Slam" not in row["items"] for row in banner_steps)
assert banner_steps[1]["items"] == ["Ruthless"]
assert banner_steps[1]["when"].endswith("(Mercy Mission)")
for expected in catalog:
    root = fixture_for(expected)
    got = adapters.match_adapter(
        root, pob.build_info(root), pob.tree_specs(root), pob.skill_sets(root))
    assert got and got["id"] == expected["id"]
    md, notes = pob.make_plan(root)
    campaign = [n for n in notes if "act" in n]
    assert campaign and all(n["source"] == f"adapter:{expected['id']}"
                            for n in campaign)
    assert {n["act"] for n in campaign} == set(range(1, 11))
    assert "Build-specific campaign milestones" in md
    assert "Exact gem acquisition checklist" in md
    assert "Item pickup guide" in md
    assert "Generic" not in md
    assert adapter_id(notes) == expected["id"]

# Matching is conservative: a class/ascendancy alone must not activate one.
almost = fixture_for(catalog[0])
almost.find("Skills").remove(almost.find("Skills").findall("SkillSet")[-1])
assert adapters.match_adapter(
    almost, pob.build_info(almost), pob.tree_specs(almost),
    pob.skill_sets(almost)) is None

# Empty labelled Skill rows split one flat PoB SkillSet into level stages.
staged = ET.Element("PathOfBuilding")
ET.SubElement(staged, "Build", {"className": "Ranger", "level": "90"})
skills = ET.SubElement(staged, "Skills")
ss = ET.SubElement(skills, "SkillSet", {"title": "Leveling"})
ET.SubElement(ss, "Skill", {"label": "----Lvl 1-12----"})
sk1 = ET.SubElement(ss, "Skill", {"slot": "Body Armour"})
ET.SubElement(sk1, "Gem", {"nameSpec": "Galvanic Arrow"})
ET.SubElement(ss, "Skill", {"label": "----Lvl 12-28----"})
sk2 = ET.SubElement(ss, "Skill", {"slot": "Body Armour"})
ET.SubElement(sk2, "Gem", {"nameSpec": "Lightning Arrow"})
disabled = ET.SubElement(ss, "Skill", {"enabled": "false"})
ET.SubElement(disabled, "Gem", {"nameSpec": "Never Show Me"})
ET.SubElement(staged, "Tree")

parsed = pob.skill_sets(staged)[0]
assert [(x["level_min"], x["level_max"]) for x in parsed["stages"]] == \
    [(1, 12), (12, 28)]
assert [x["groups"][0]["gems"][0] for x in parsed["stages"]] == \
    ["Galvanic Arrow", "Lightning Arrow"]
assert "Never Show Me" not in str(parsed)
assert pob.level_range_in_title("Leveling 2 (Static Strike-Lvl 13-28)") == \
    (13, 28)
assert pob.level_range_in_title("-----Lvl 38+-----") == (38, None)

# The overlay chooses the most recent reached reminder instead of showing
# every milestone for the entire act.
rows = [
    {"act": 1, "level": 2, "text": "starter"},
    {"act": 1, "level": 4, "text": "first links"},
    {"act": 1, "level": 8, "text": "three-link"},
    {"act": 2, "text": "always"},
    {"act": "bad", "text": "ignored"},
]
grouped = group_notes(rows)
assert select_note(grouped[1], 1) == "starter"
assert select_note(grouped[1], 4) == "first links"
assert select_note(grouped[1], 99) == "three-link"
assert select_note(grouped[2], 20) == "always"
assert adapter_id(rows) is None

# A real connected Templar tree fragment produces auditable, per-level rows.
passive_root = ET.Element("PathOfBuilding")
ET.SubElement(passive_root, "Build", {
    "className": "Templar", "ascendClassName": "", "level": "3"})
tree = ET.SubElement(passive_root, "Tree")
spec = ET.SubElement(tree, "Spec", {
    "title": "Leveling 1-3",
    "treeVersion": "3_28",
    "nodes": "61525,63965,14151",
    "masteryEffects": "{63268,6216}",
})
ET.SubElement(spec, "URL").text = "https://pobb.in/test/tree"
parsed_spec = pob.tree_specs(passive_root)[0]
assert parsed_spec["mastery_effects"] == {"63268": "6216"}
assert parsed_spec["url"] == "https://pobb.in/test/tree"
passive_md, passive_notes = pob.make_plan(passive_root)
passives = group_passives(passive_notes)
assert [(row["level"], row["text"]) for row in passives] == [
    (2, "Damage and Mana"),
    (3, "Intelligence"),
]
assert "Level-by-level passive allocation" in passive_md
assert "`14151`" in passive_md
assert select_passives(passives, 2).startswith("Level: Damage and Mana")
assert select_passives(passives, 1).startswith("Next @2:")

print("ALL TESTS PASSED")
print("  four structural adapters matched with full Act 1-10 coverage")
