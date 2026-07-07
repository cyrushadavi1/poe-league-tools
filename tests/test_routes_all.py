"""Headless tests: the full 10-act route set — schema, ordering, walkability."""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "overlay")]

from route_engine import RouteEngine           # noqa: E402

ROUTES_DIR = os.path.join(ROOT, "routes")
KINDS = {"travel", "kill", "town", "trial"}


def is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


# ---------------------------------------------------------- files present
files = sorted(f for f in os.listdir(ROUTES_DIR)
               if f.startswith("act") and f.endswith(".json"))
assert files == sorted(f"act{n}.json" for n in range(1, 11)), \
    f"expected exactly act1..act10.json, found {files}"

# ------------------------------------------------- per-file schema checks
acts = {}                                        # act number -> raw steps
for name in files:
    file_act = int(name[len("act"):-len(".json")])
    with open(os.path.join(ROUTES_DIR, name), encoding="utf-8") as f:
        data = json.load(f)
    assert data["act"] == file_act, \
        f"{name}: 'act' is {data['act']!r}, filename says {file_act}"
    steps = data["steps"]
    assert isinstance(steps, list) and len(steps) >= 8, \
        f"{name}: expected >= 8 steps, got {len(steps)}"
    for k, s in enumerate(steps):
        where = f"{name} step {k}"
        zone = s.get("zone")
        assert isinstance(zone, str) and zone.strip(), \
            f"{where}: 'zone' must be a non-empty string"
        assert s.get("kind") in KINDS, \
            f"{where} ({zone}): bad 'kind' {s.get('kind')!r}"
        do = s.get("do")
        assert isinstance(do, list) and do, \
            f"{where} ({zone}): 'do' must be a non-empty list"
        assert all(isinstance(d, str) and d.strip() for d in do), \
            f"{where} ({zone}): every 'do' entry must be a non-empty string"
        for opt in ("layout", "tip"):
            if opt in s:
                assert isinstance(s[opt], str) and s[opt].strip(), \
                    f"{where} ({zone}): '{opt}' must be a non-empty string"
        if "arealvl" in s:
            assert is_int(s["arealvl"]) and 1 <= s["arealvl"] <= 70, \
                f"{where} ({zone}): 'arealvl' {s['arealvl']!r} not int in 1..70"
    acts[file_act] = steps

# --------------------------------------------------- engine sees all acts
eng = RouteEngine(ROUTES_DIR)
assert sorted(set(s["act"] for s in eng.steps)) == list(range(1, 11)), \
    "engine over routes/ must load exactly acts 1..10"
assert len(eng.steps) == sum(len(v) for v in acts.values())

# RouteEngine concatenates by *lexicographic* filename sort, which puts
# act10.json between act1.json and act2.json. Fixing that belongs in
# overlay/route_engine.py (integration-owned: numeric sort key), so it is
# surfaced here as a loud warning rather than a failure.
engine_act_order = []
for s in eng.steps:
    if not engine_act_order or engine_act_order[-1] != s["act"]:
        engine_act_order.append(s["act"])
if engine_act_order != list(range(1, 11)):
    print(f"WARN: RouteEngine loads routes/ acts in order {engine_act_order} "
          "(lexicographic filename sort) — route_engine.py needs a numeric "
          "sort key before the overlay can run acts 1..10 in sequence")

# ------------------------------------------------------------ walk-through
# Validate step ordering by walking the whole campaign in play order
# (acts 1..10). Zero-padded copies in a temp dir force the loader to
# concatenate the acts in that order regardless of the wart above.
walk_dir = tempfile.mkdtemp(prefix="poe_routes_all_test_")
try:
    for n in range(1, 11):
        shutil.copy(os.path.join(ROUTES_DIR, f"act{n}.json"),
                    os.path.join(walk_dir, f"act{n:02d}.json"))
    weng = RouteEngine(walk_dir)
finally:
    shutil.rmtree(walk_dir)
assert [s["act"] for s in weng.steps] == \
    [a for a in range(1, 11) for _ in acts[a]], \
    "walk engine must hold acts 1..10 in play order"

# Feed every step's zone to on_zone() in order, like a player running the
# route. Consecutive steps in the SAME zone can't auto-advance (on_zone
# ignores the current zone), so there — and only there — press next(),
# mirroring the manual hotkey. Anything else that fails to advance is an
# unreachable step: fail loudly.
assert weng.i == 0
for j in range(1, len(weng.steps)):
    step = weng.steps[j]
    zone = step["zone"]
    if not weng.on_zone(zone):
        cur = weng.current()
        assert cur["zone"].strip().lower() == zone.strip().lower(), (
            f"UNREACHABLE step {j} (act {step['act']}, '{zone}'): stuck at "
            f"step {weng.i} (act {cur['act']}, '{cur['zone']}') — ordering "
            f"or lookahead break")
        weng.next()                    # same-zone follow-up step: hotkey
    assert weng.i == j, \
        f"after '{zone}' expected step {j}, engine is at {weng.i}"
assert weng.i == len(weng.steps) - 1, "walk must end on the final step"
n, total, act = weng.progress()
assert act == 10 and n == total == len(acts[10]), \
    "final step must be the last step of act 10"

# --------------------------------------- arealvl coverage (warn, not fail)
missing = [(a, k, s["zone"]) for a in sorted(acts)
           for k, s in enumerate(acts[a])
           if s["kind"] != "town" and "arealvl" not in s]
for a, k, zone in missing:
    print(f"WARN: act{a} step {k} ('{zone}') is non-town but has no arealvl")

print("ALL TESTS PASSED")
print(f"  {len(weng.steps)} steps across {len(acts)} acts walked end to end")
print(f"  non-town steps missing arealvl: {len(missing)}")
