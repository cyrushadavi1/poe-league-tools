"""Headless tests: party bundle round-trip + the first-run wizard.

The producer (buildgen/party.py write_bundle) and the consumer
(tools/join_party.py) are tested against each other: what one writes,
the other must set a friend's PC up from -- no hand-edited JSON.
"""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "overlay"),
                os.path.join(ROOT, "buildgen")]

import party                              # noqa: E402
from tools import join_party              # noqa: E402

REAL_ROUTES = os.path.join(ROOT, "routes")


def no_ask(prompt):
    raise AssertionError(f"wizard asked interactively: {prompt!r}")


def scripted(answers):
    it = iter(answers)
    return lambda prompt: next(it)


tmp = tempfile.mkdtemp(prefix="poe_join_test_")
try:
    overlay_dir = os.path.join(tmp, "overlay")
    builds_dir = os.path.join(tmp, "builds")
    os.makedirs(overlay_dir)
    os.makedirs(builds_dir)
    config = os.path.join(overlay_dir, "config.json")
    bundle_path = os.path.join(builds_dir, "party_bundle.json")

    client = os.path.join(tmp, "Client.txt")
    with open(client, "w", encoding="utf-8") as f:
        f.write("2026/07/24 20:11:03 1234 ac9 [INFO Client 5] : "
                "You have entered The Coast.\n")

    # ----------------------------------------------- bundle (producer)
    members = []
    for name, cls, asc, me in [("CyrusChar", "Duelist", "Slayer", True),
                               ("FriendA", "Ranger", "Deadeye", False),
                               ("FriendB", "Witch", "Necromancer", False)]:
        notes_path = os.path.join(builds_dir, f"{name}_notes.json")
        with open(notes_path, "w", encoding="utf-8") as f:
            json.dump([{"act": 1, "text": f"{name} gems"}], f)
        members.append({"player": name, "me": me, "class": cls,
                        "ascendancy": asc, "notes_path": notes_path,
                        "plan_path": os.path.join(builds_dir,
                                                  f"{name}_plan.md")})
    bundle = party.write_bundle(members, bundle_path, league="3.29")
    assert bundle["league"] == "3.29"
    assert [m["player"] for m in bundle["members"]] == \
        ["CyrusChar", "FriendA", "FriendB"]
    assert bundle["members"][0]["me"] is True
    assert bundle["members"][1]["notes"] == "FriendA_notes.json"  # basename
    with open(bundle_path, encoding="utf-8") as f:
        assert json.load(f) == bundle              # round-trips

    # ------------------------------------- wizard, fully non-interactive
    # A friend's PC: the copied folder carries SOMEONE ELSE'S config --
    # custom tweaks must survive, identity must be rewritten.
    with open(config, "w", encoding="utf-8") as f:
        json.dump({"client_txt": "C:\\old\\wrong\\Client.txt",
                   "opacity": 0.5, "poll_ms": 250,
                   "build_notes": "/Users/cyrus/notes.json",
                   "routes_dir": REAL_ROUTES,
                   "party": {"me": "CyrusChar",
                             "members": ["FriendA", "FriendB"],
                             "gap_warn": 5},
                   "hotkeys": {"prev": "F2", "next": "F3",
                               "toggle": "F4"}}, f)
    out = []
    code = join_party.wizard(config, bundle_path, client_arg=client,
                             who="frienda",     # case-insensitive
                             yes=True, ask=no_ask, say=out.append,
                             run_checks=False)
    assert code == 0
    cfg = json.load(open(config, encoding="utf-8"))
    assert cfg["party"]["me"] == "FriendA"
    assert cfg["party"]["members"] == ["CyrusChar", "FriendB"]
    assert cfg["party"]["gap_warn"] == 5           # tweak preserved
    assert cfg["opacity"] == 0.5                   # tweak preserved
    assert cfg["poll_ms"] == 250
    assert cfg["client_txt"] == client
    assert cfg["build_notes"] == "../builds/FriendA_notes.json"
    # the path the wizard wrote resolves exactly like overlay/main.py does
    assert os.path.exists(os.path.join(overlay_dir, cfg["build_notes"]))

    # ------------------------------------- wizard end-to-end with doctor
    out = []
    code = join_party.wizard(config, bundle_path, client_arg=client,
                             who="FriendB", yes=True, ask=no_ask,
                             say=out.append, run_checks=True)
    assert code == 0
    text = "\n".join(out)
    assert "doctor" in text and "run_overlay.bat" in text
    assert "FAIL" not in text, text                # skeleton is healthy
    cfg = json.load(open(config, encoding="utf-8"))
    assert cfg["party"]["me"] == "FriendB"
    assert cfg["party"]["members"] == ["CyrusChar", "FriendA"]

    # ------------------------------------- interactive: scripted answers
    # answer 1 -> Client.txt prompt (type the path), answer 2 -> pick #2
    out = []
    code = join_party.wizard(config, bundle_path, client_arg=None,
                             who=None, yes=False,
                             ask=scripted([client, "2"]), say=out.append,
                             run_checks=False)
    assert code == 0
    cfg = json.load(open(config, encoding="utf-8"))
    assert cfg["party"]["me"] == "FriendA"
    assert any("Who are you?" in line for line in out)

    # Enter-through: bad number re-asks, then Enter takes the default
    # (the bundle's me-flagged member, #1); client_arg means no client ask
    out = []
    code = join_party.wizard(config, bundle_path, client_arg=client,
                             who=None, yes=False,
                             ask=scripted(["9", ""]), say=out.append,
                             run_checks=False)
    assert code == 0
    cfg = json.load(open(config, encoding="utf-8"))
    assert cfg["party"]["me"] == "CyrusChar"       # the bundle's me hint
    assert any("pick 1-3" in line for line in out)

    # ------------------------------------- --who not in the bundle
    before = open(config, encoding="utf-8").read()
    out = []
    code = join_party.wizard(config, bundle_path, client_arg=client,
                             who="NoSuchChar", yes=True, ask=no_ask,
                             say=out.append, run_checks=False)
    assert code == 2
    assert open(config, encoding="utf-8").read() == before  # untouched
    assert any("not in the bundle" in line for line in out)

    # ------------------------------------- corrupt config: backed up
    with open(config, "w", encoding="utf-8") as f:
        f.write('{"party": [BROKEN')
    out = []
    code = join_party.wizard(config, bundle_path, client_arg=client,
                             who="FriendA", yes=True, ask=no_ask,
                             say=out.append, run_checks=False)
    assert code == 0
    assert os.path.exists(config + ".bak")
    assert "BROKEN" in open(config + ".bak", encoding="utf-8").read()
    cfg = json.load(open(config, encoding="utf-8"))
    assert cfg["party"]["me"] == "FriendA"
    assert cfg["hotkeys"]["next"] == "F3"          # defaults restored

    # ------------------------------------- no bundle: manual entry
    no_bundle = os.path.join(tmp, "nowhere.json")
    out = []
    code = join_party.wizard(config, no_bundle, client_arg=client,
                             who="SoloChar", others="MateA, MateB",
                             yes=True, ask=no_ask, say=out.append,
                             run_checks=False)
    assert code == 0
    cfg = json.load(open(config, encoding="utf-8"))
    assert cfg["party"]["me"] == "SoloChar"
    assert cfg["party"]["members"] == ["MateA", "MateB"]
    # no bundle -> existing build_notes deliberately left alone
    assert cfg["build_notes"] == "../builds/FriendA_notes.json"

    # no bundle + --yes + nothing given: keeps what the config had
    out = []
    code = join_party.wizard(config, no_bundle, client_arg=client,
                             yes=True, ask=no_ask, say=out.append,
                             run_checks=False)
    assert code == 0
    cfg = json.load(open(config, encoding="utf-8"))
    assert cfg["party"]["me"] == "SoloChar"
    assert cfg["party"]["members"] == ["MateA", "MateB"]

    # no bundle, interactive: typed names win over the old config
    # (client_arg set -> the two asks are name + other-members)
    out = []
    code = join_party.wizard(config, no_bundle, client_arg=client,
                             yes=False,
                             ask=scripted(["NewMe", "OnlyMate"]),
                             say=out.append, run_checks=False)
    assert code == 0
    cfg = json.load(open(config, encoding="utf-8"))
    assert cfg["party"]["me"] == "NewMe"
    assert cfg["party"]["members"] == ["OnlyMate"]
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("ALL TESTS PASSED")
