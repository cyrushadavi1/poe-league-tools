"""Headless tests: log parsing, party state, route engine, PoB, party builds."""
import json
import os
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "overlay"), os.path.join(ROOT, "buildgen")]

from client_watcher import (ClientWatcher, last_known_level,  # noqa: E402
                            parse_line)
from party_state import PartyState             # noqa: E402
from route_engine import RouteEngine           # noqa: E402
import party                                   # noqa: E402
import pob                                     # noqa: E402

# ---------------------------------------------------------- log parsing
PRE = "2026/07/24 20:11:03 1234 ac9 [INFO Client 5]"
assert parse_line(f"{PRE} : You have entered The Coast.") == \
    ("zone", "The Coast")
assert parse_line(f"{PRE} : Exile59 (Witch) is now level 7") == \
    ("level", ("Exile59", "Witch", 7))
assert parse_line(f"{PRE} : FriendChar has joined the area.") == \
    ("join", "FriendChar")
assert parse_line(f"{PRE} : FriendChar has left the area.") == \
    ("leave", "FriendChar")
assert parse_line(f"{PRE} : FriendChar has been slain.") == \
    ("slain", "FriendChar")
assert parse_line("random noise") is None

# chat must not spoof any event (chat lines have a speaker between ] and :)
assert parse_line(f"{PRE} #Troll: You have entered The Coast.") is None
assert parse_line(f"{PRE} #Troll: Exile59 (Witch) is now level 99") is None
assert parse_line(f"{PRE} @From Scam: Exile59 has been slain.") is None
assert parse_line(f"{PRE} %Mate: Bob has joined the area.") is None
# ...even when the chat payload embeds a fake '] : ' system prefix
assert parse_line(
    f"{PRE} #Troll: lol ] : You have entered Kitava's Hideout.") is None
assert parse_line(
    f"{PRE} @From Scammer: ] : Exile59 has been slain.") is None
assert parse_line(
    f"{PRE} %Mate: ] : Exile59 (Witch) is now level 4") is None

# guilded characters carry a '<TAG> ' prefix on name-bearing events
assert parse_line(f"{PRE} : <TAG> Bob has joined the area.") == ("join", "Bob")
assert parse_line(f"{PRE} : <TAG> Bob has left the area.") == ("leave", "Bob")
assert parse_line(f"{PRE} : <TAG> Bob has been slain.") == ("slain", "Bob")
assert parse_line(f"{PRE} : <Cool Guild> Bob (Witch) is now level 12") == \
    ("level", ("Bob", "Witch", 12))

# -------------------------------------------------- watcher: partial lines
watch_tmp = tempfile.mkdtemp(prefix="poe_watch_test_")
try:
    log = os.path.join(watch_tmp, "Client.txt")
    open(log, "w", encoding="utf-8").close()
    w = ClientWatcher(log)

    def _append(text):
        with open(log, "a", encoding="utf-8") as f:
            f.write(text)

    # a mid-line flush is buffered, not parsed: the event arrives complete
    # once its newline lands (mid-word AND mid-number truncations)
    _append(f"{PRE} : You have entered The Tidal Isl")
    assert w.poll() == [], "half-written line must not be consumed"
    _append("and.\n")
    assert w.poll() == [("zone", "The Tidal Island")]
    _append(f"{PRE} : Exile59 (Witch) is now level 4")
    assert w.poll() == [], "truncated level number must not parse as 4"
    _append(f"2\n{PRE} : You have entered The Coast.\n")
    assert w.poll() == [("level", ("Exile59", "Witch", 42)),
                        ("zone", "The Coast")]

    # last_known_level primes a restarted overlay from the log tail
    assert last_known_level(log, lambda n: n == "Exile59") == 42
    assert last_known_level(log, lambda n: n == "Nobody") is None
    assert last_known_level(os.path.join(watch_tmp, "gone.txt"),
                            lambda n: True) is None
finally:
    shutil.rmtree(watch_tmp)

# ---------------------------------------------------------- party state
ps = PartyState(me="MyChar", members=["FriendA", "FriendB"], gap_warn=3)
assert ps.status_line() == "○ FriendA ?  ○ FriendB ?"

assert ps.on_event("level", ("MyChar", "Witch", 10)) == ("me_level", 10)
assert ps.my_level == 10
assert ps.on_event("level", ("FriendA", "Duelist", 9)) == ("party", None)
assert ps.on_event("join", "FriendA") == ("party", None)
assert ps.on_event("join", "RandomTownGuy") is None, \
    "non-members in the area are ignored"
assert ps.on_event("level", ("FriendB", "Ranger", 6)) == ("party", None)
assert ps.gap_warning("FriendB") and not ps.gap_warning("FriendA")
assert ps.warnings() == ["FriendB is 4 levels behind"]
assert ps.on_event("slain", "FriendA") == ("death", "FriendA")
assert ps.members["FriendA"]["deaths"] == 1
assert ps.on_event("slain", "MyChar") == ("death", "MyChar")
assert ps.my_deaths == 1 and ps.is_me("MyChar")
assert ps.on_event("leave", "FriendA") == ("party", None)
assert ps.status_line() == "○ FriendA 9 ☠1  ○ FriendB 6 ⚠"

solo = PartyState()                    # no party config -> old behaviour
assert solo.status_line() == ""
assert solo.on_event("level", ("Whoever", "Witch", 12)) == ("me_level", 12)
assert solo.my_level == 12

# ---------------------------------------------------------- route engine
# routes/ now ships all ten acts; this section exercises engine mechanics
# against act 1 alone, so copy act1.json into a temp dir and load that.
# (tests/test_routes_all.py covers the full 10-act route set.)
route_tmp = tempfile.mkdtemp(prefix="poe_route_test_")
shutil.copy(os.path.join(ROOT, "routes", "act1.json"), route_tmp)
eng = RouteEngine(route_tmp)
shutil.rmtree(route_tmp)                # steps are loaded in __init__
assert len(eng.steps) == 17 and eng.i == 0

walk = [
    ("Lioneye's Watch", 1), ("The Coast", 2), ("The Tidal Island", 3),
    ("Lioneye's Watch", 4), ("The Coast", 5), ("The Mud Flats", 6),
    ("The Submerged Passage", 7),
    ("The Flooded Depths", 7),        # side area: ignored
    ("The Submerged Passage", 7),     # re-entering current zone: ignored
    ("The Ledge", 8), ("The Climb", 9), ("The Lower Prison", 10),
    ("The Upper Prison", 11), ("Prisoner's Gate", 12),
    ("The Ship Graveyard", 13), ("Lioneye's Watch", 14),
    ("The Ship Graveyard", 14),       # WP back mid-town-step: ignored
    ("The Cavern of Wrath", 15), ("The Cavern of Anger", 16),
]
for zone, expected in walk:
    eng.on_zone(zone)
    assert eng.i == expected, f"after '{zone}' expected {expected}, got {eng.i}"
n, total, act = eng.progress()
assert (n, total, act) == (17, 17, 1)
eng.prev()
assert eng.i == 15
eng.next()
eng.next()
assert eng.i == 16, "next() must clamp at the last step"


# ------------------------------------------- startup fast-forward
def all_routes_engine():
    return RouteEngine(os.path.join(ROOT, "routes"))


# 1. full history replays exactly like live play (overlay restart)
ff = all_routes_engine()
history = [z for z, _ in walk]
skipped = ff.fast_forward(history)
assert skipped == 16 and ff.i == 16, f"walked to {ff.i}"

# 2. cold start mid-campaign: tail names a unique deep zone
ff = all_routes_engine()
deep = [s for s in ff.steps if s["act"] == 8][3]
skipped = ff.fast_forward(["Some Hideout", deep["zone"]], 60)
assert ff.steps[ff.i] is deep or ff.steps[ff.i]["zone"] == deep["zone"], \
    f"landed on {ff.steps[ff.i]}"
assert ff.steps[ff.i]["act"] == 8 and skipped > 0

# 3. mirrored town names resolve by level (Lioneye's: act 1 vs act 6)
ff = all_routes_engine()
ff.fast_forward(["Lioneye's Watch"], 45)
assert ff.steps[ff.i]["act"] == 6, \
    f"lvl 45 + Lioneye's must mean act 6, got act {ff.steps[ff.i]['act']}"
ff2 = all_routes_engine()
ff2.fast_forward(["Lioneye's Watch"], 2)
assert ff2.steps[ff2.i]["act"] == 1

# 4. an alt's early zones must not mask the main char's real position
ff = all_routes_engine()
act9 = next(s for s in ff.steps if s["act"] == 9 and s.get("arealvl"))
ff.fast_forward(["The Coast", "The Mud Flats", act9["zone"]], 64)
assert ff.steps[ff.i]["act"] == 9, f"got act {ff.steps[ff.i]['act']}"

# 5. no zones / no matches -> stays put
ff = all_routes_engine()
assert ff.fast_forward([]) == 0 and ff.i == 0
assert ff.fast_forward(["Not A Zone", "Also Fake"]) == 0 and ff.i == 0

# 6. recent_zones reads the tail, rejects chat spoofing
zr_tmp = tempfile.mkdtemp(prefix="poe_zones_test_")
try:
    zlog = os.path.join(zr_tmp, "Client.txt")
    with open(zlog, "w", encoding="utf-8") as f:
        f.write(f"{PRE} : You have entered The Coast.\n"
                f"{PRE} #Troll: You have entered Kitava's Hideout.\n"
                f"{PRE} : You have entered The Mud Flats.\n"
                "garbage line\n")
    from client_watcher import recent_zones
    assert recent_zones(zlog) == ["The Coast", "The Mud Flats"]
    assert recent_zones(os.path.join(zr_tmp, "missing.txt")) == []
finally:
    shutil.rmtree(zr_tmp)


# ---------------------------------------------------------- PoB round-trip
def sample_pob(class_name, asc, main_gem):
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
    return root


code = pob.encode(sample_pob("Witch", "Elementalist", "Rolling Magma"))
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
leveling_text = ("Rolling Magma – Arcane Surge Support – "
                 "Added Lightning Damage Support")
assert notes == [{"act": 1, "text": leveling_text},
                 {"act": 2, "text": leveling_text}], \
    "'Act 1-2' skill set emits a note for EVERY act in the span"


# ------------------------------------ generic fallback for bare PoBs
def bare_pob(class_name):
    """A realistic guide-site export: one 'Endgame' set, no act tags."""
    root = ET.Element("PathOfBuilding")
    ET.SubElement(root, "Build", {"level": "92", "className": class_name,
                                  "ascendClassName": ""})
    skills = ET.SubElement(root, "Skills")
    ss = ET.SubElement(skills, "SkillSet", {"title": "Endgame"})
    sk = ET.SubElement(ss, "Skill", {"label": ""})
    ET.SubElement(sk, "Gem", {"nameSpec": "Fireball"})
    ET.SubElement(root, "Tree")
    return root


md_g, notes_g = pob.make_plan(bare_pob("Witch"))
assert [n["act"] for n in notes_g] == list(range(1, 11)), \
    "bare PoB must fall back to the generic class plan, acts 1-10"
assert all(n.get("source") == "generic" for n in notes_g), \
    "generic notes must be marked so doctor/plan.md can say so"
assert "Generic Witch leveling gems" in md_g and "class defaults" in md_g
assert "Rolling Magma" in notes_g[0]["text"]

assert all("source" not in n for n in notes), \
    "act-tagged PoBs must never get generic notes mixed in"

md_u, notes_u = pob.make_plan(bare_pob("NotAClass"))
assert notes_u == [] and "Generic" not in md_u, \
    "unknown class: no notes, no crash"

# the data file: every class, every act, card-sized lines
with open(pob.GENERIC_PLANS, encoding="utf-8") as f:
    _plans = json.load(f)
_classes = {"Marauder", "Duelist", "Ranger", "Shadow", "Witch",
            "Templar", "Scion"}
assert _classes <= set(_plans), f"missing: {_classes - set(_plans)}"
for _cls in _classes:
    assert set(_plans[_cls]) == {str(a) for a in range(1, 11)}, \
        f"{_cls}: acts must be exactly 1-10"
    for _a, _text in _plans[_cls].items():
        assert 0 < len(_text) <= 110, \
            f"{_cls} act {_a}: line too long for the overlay card"

# ranged / short act titles resolve to full spans
assert pob.acts_in_title("Act 6-10 gems") == [6, 7, 8, 9, 10]
assert pob.acts_in_title("Act 3+4") == [3, 4]
assert pob.acts_in_title("A3 setup") == [3]
assert pob.acts_in_title("Endgame") == []

# malformed level attributes degrade to 1 instead of crashing
bad_build = ET.Element("PathOfBuilding")
ET.SubElement(bad_build, "Build", {"level": "", "className": "Witch"})
assert pob.build_info(bad_build)["level"] == 1
assert pob.build_info(pob.decode(pob.encode(bad_build)))["level"] == 1

# garbage input exits with a hint, not a traceback; an unrecognized URL
# exits naming the supported link sites (no network touched — recognized
# hosts like pobb.in are fetched, which tests/test_pob_sources.py covers
# with an injected fetch)
_old_argv = sys.argv
for _argv, _want in [
        (["pob.py", "decode", "not!a!code"], "could not decode PoB code"),
        (["pob.py", "decode", "https://example.com/abc123XYZ"], "pobb.in")]:
    sys.argv = _argv
    try:
        pob.main()
        raise AssertionError("bad input must exit with a hint")
    except SystemExit as e:
        assert _want in str(e), f"{_argv[-1]}: hint missing from {e}"
    finally:
        sys.argv = _old_argv

# ---------------------------------------------------------- party builds
tmp = tempfile.mkdtemp(prefix="poe_party_test_")
try:
    manifest = {"members": [
        {"player": "CyrusChar", "me": True,
         "pob": pob.encode(sample_pob("Witch", "Elementalist", "Rolling Magma"))},
        {"player": "FriendChar",
         "pob": pob.encode(sample_pob("Duelist", "Champion", "Cleave"))},
    ]}
    out_dir = os.path.join(tmp, "builds")
    os.makedirs(out_dir)
    members = [party.build_member(m, out_dir) for m in manifest["members"]]

    assert [m["class"] for m in members] == ["Witch", "Duelist"]
    assert members[0]["me"] and not members[1]["me"]
    for m in members:
        assert os.path.exists(m["plan_path"])
        with open(m["notes_path"], encoding="utf-8") as f:
            assert json.load(f)[0]["act"] == 1

    summ = party.summary_md(members)
    assert "CyrusChar ★" in summ, "the 'me' member is starred"
    assert "Rolling Magma" in summ and "Cleave" in summ
    assert "| 1 |" in summ, "per-act gem table present"
finally:
    shutil.rmtree(tmp)

print("ALL TESTS PASSED")
print(f"  sample PoB code round-tripped ({len(code)} chars)")
print(f"  party status line: {ps.status_line()}")
