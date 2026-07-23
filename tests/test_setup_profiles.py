"""Headless tests for graphical setup's pure build-selection helpers."""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "overlay")]

from tools import setup_profiles  # noqa: E402


tmp = tempfile.mkdtemp(prefix="poe_profile_picker_test_")
try:
    overlay = os.path.join(tmp, "overlay")
    builds = os.path.join(tmp, "builds", "allflame")
    os.makedirs(overlay)
    os.makedirs(builds)
    config_path = os.path.join(overlay, "config.json")
    bundle_path = os.path.join(builds, "party_bundle.json")

    members = []
    for player, role, cls, asc in [
            ("Carry", "Spark carry", "Templar", "Inquisitor"),
            ("Aurabot", "Aura support", "Scion", "Ascendant"),
            ("Banner", "Banner support", "Duelist", "Champion"),
            ("Drugger", "Warcry/flask support", "Ranger", "Pathfinder")]:
        notes = f"{player}_notes.json"
        plan = f"{player}_plan.md"
        with open(os.path.join(builds, notes), "w", encoding="utf-8") as f:
            json.dump([{"act": 1, "text": player}], f)
        with open(os.path.join(builds, plan), "w", encoding="utf-8") as f:
            f.write(f"# {player}\n")
        members.append({
            "player": player, "role": role, "class": cls,
            "ascendancy": asc, "pob": f"https://pobb.in/{player}",
            "notes": notes, "plan": plan, "me": False,
        })
    bundle = {"league": "3.29", "members": members}
    with open(bundle_path, "w", encoding="utf-8") as f:
        json.dump(bundle, f)

    assert setup_profiles.load_bundle(bundle_path) == bundle
    found_path, found = setup_profiles.find_bundle(tmp)
    assert found_path == bundle_path and found == bundle
    labels = [setup_profiles.member_label(row) for row in members]
    assert labels[0] == "Carry — Spark carry · Templar (Inquisitor)"
    assert "Aura support" in labels[1]

    base = {
        "opacity": 0.7,
        "party": {"me": "Carry", "members": [], "gap_warn": 4},
    }
    cfg = setup_profiles.apply_profile(
        base, config_path, bundle_path, bundle, 1,
        character_name="RealAuraChar",
        teammates=["RealCarryChar", "RealCarryChar", "", "RealAuraChar"])
    assert cfg["opacity"] == 0.7
    assert cfg["party"] == {
        "me": "RealAuraChar",
        "members": ["RealCarryChar"],
        "gap_warn": 4,
        "build": "Aurabot",
    }
    assert cfg["selected_build"]["id"] == "Aurabot"
    assert cfg["selected_build"]["pob"] == "https://pobb.in/Aurabot"
    assert cfg["build_notes"] == "../builds/allflame/Aurabot_notes.json"
    assert cfg["build_plan"] == "../builds/allflame/Aurabot_plan.md"
    assert cfg["party_bundle"] == \
        "../builds/allflame/party_bundle.json"
    assert os.path.exists(os.path.join(
        overlay, cfg["build_notes"]))

    setup_profiles.write_config(cfg, config_path)
    with open(config_path, encoding="utf-8") as f:
        assert json.load(f) == cfg
    assert setup_profiles.selected_member_index(
        cfg, bundle, bundle_path, config_path) == 1

    # Explicit selected-build metadata wins, then resolved notes are the
    # fallback for configs written by the older setup wizard.
    old = dict(cfg)
    old.pop("selected_build")
    old["build_notes"] = "../builds/allflame/Drugger_notes.json"
    assert setup_profiles.selected_member_index(
        old, bundle, bundle_path, config_path) == 3

    # A role placeholder from the old hardcoded bundle is not mistaken for
    # an actual game character name.
    role_only = setup_profiles.apply_profile(
        {"party": {"me": "Banner", "members": []}},
        config_path, bundle_path, bundle, 2)
    assert role_only["party"]["me"] == ""
    assert role_only["party"]["build"] == "Banner"

    try:
        setup_profiles.apply_profile(
            {}, config_path, bundle_path, bundle, 99)
        raise AssertionError("invalid member index was accepted")
    except ValueError:
        pass
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("ALL TESTS PASSED")
print("  graphical setup selects roles independently of character names")
print("  notes, plans, bundle, and source PoB persist as portable paths")
