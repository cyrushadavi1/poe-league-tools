"""Headless integration tests: overlay wiring helpers + market seams.

Imports overlay/main.py WITHOUT running main() — the module must stay
import-safe (no side effects, no Qt at import time). Exercises the pure
wiring helpers the Qt callbacks delegate to (dispatch_events,
evaluate_clipboard_text, tracker_status, save_run), the single-item ->
pair-row scanner glue numerically, and the shipped config defaults.
Offline, no network, no Qt, stdlib only.
"""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "overlay"), os.path.join(ROOT, "market")]

import main                                     # noqa: E402  (overlay/main.py)
import item_rules                               # noqa: E402
import itemtext                                 # noqa: E402
from party_state import PartyState              # noqa: E402
from route_engine import RouteEngine            # noqa: E402
from run_tracker import RunTracker, load_run    # noqa: E402
from scanner import pair_rows_from_currency, scan  # noqa: E402

# ------------------------------------------------ import safety: no Qt pulled
assert not any(m == "PyQt6" or m.startswith("PyQt6.") for m in sys.modules), \
    "importing overlay/main.py must not import Qt (it lives inside main())"

# the wiring helpers exist and are callable
for name in ("dispatch_events", "evaluate_clipboard_text", "tracker_status",
             "save_run", "main", "_find_client", "_resolve"):
    assert callable(getattr(main, name)), f"main.{name} missing"
assert main.MAX_CLIP_CHARS == 8192

# ------------------------------------------------ shipped config defaults
with open(os.path.join(ROOT, "overlay", "config.json"),
          encoding="utf-8") as f:
    cfg = json.load(f)
for key in ("client_txt", "poll_ms", "routes_dir", "party", "hotkeys"):
    assert key in cfg, f"existing config key {key} must survive"
assert cfg["timer"] is True
assert cfg["runs_dir"] == "../runs"
assert cfg["item_eval"] is True
assert cfg["links_best"] == 3

with open(os.path.join(ROOT, "market", "config.json"),
          encoding="utf-8") as f:
    mcfg = json.load(f)
assert mcfg["league"] == "Mirage", "rehearsal league (live-verified)"
assert "_note" in mcfg, "note to switch league at 3.29 launch"


# ------------------------------------------------ event dispatch (full run)
class FakeClock:
    def __init__(self, t=100.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


tmp = tempfile.mkdtemp(prefix="poe_integration_test_")
try:
    engine = RouteEngine(os.path.join(ROOT, "routes"))
    party = PartyState(me="Hero", members=["Mate"], gap_warn=3)
    clock = FakeClock(100.0)
    tracker = RunTracker(clock=clock, runs_dir=os.path.join(tmp, "runs"))
    tracker.start("Hero", "Witch", "itest")

    act1_zones = [s["zone"] for s in engine.steps if s["act"] == 1]

    # one poll batch, the way main()'s tick feeds it
    ops = main.dispatch_events([
        ("zone", act1_zones[1]),              # advances the route
        ("level", ("Hero", "Witch", 2)),      # my level-up
        ("join", "Mate"),
        ("level", ("Mate", "Ranger", 2)),
        ("slain", "Mate"),                    # mate death: flash only
        ("slain", "Hero"),                    # my death: flash + tracker
    ], engine, party, tracker)

    assert ops[0] == ("refresh",), "zone advance re-renders the card"
    assert ops[1] == ("level", 2) and ops[2] == ("refresh",), \
        "my level updates the header, then refreshes"
    assert ops[3][0] == "party" and "Mate" in ops[3][1]
    assert ops[4][0] == "party"               # join
    assert ops[5][0] == "party"               # mate level
    assert ops[6] == ("flash", "☠ Mate died")
    assert ops[7][0] == "party" and "☠1" in ops[7][1]
    assert ops[8] == ("flash", "☠ YOU died"), "my own death says YOU"
    assert ops[9][0] == "party"
    assert len(ops) == 10

    # tracker fed exactly like the tick does
    assert tracker.level == 2
    assert tracker.run["levels"] == [{"level": 2, "t": 0}]
    assert tracker.run["deaths"] == [{"t": 0, "who": "Hero"}], \
        "only MY deaths are tracked (party deaths are display-only)"

    # a later batch walks the rest of act 1 and crosses into act 2
    clock.advance(300)
    zone_events = [("zone", z) for z in act1_zones[2:]]
    zone_events.append(("zone", "The Southern Forest"))     # act 2 step 1
    ops = main.dispatch_events(zone_events, engine, party, tracker)
    assert ops == [("refresh",)] * len(zone_events), \
        "each consecutive route zone advances exactly one step"
    assert engine.progress()[2] == 2, "engine crossed into act 2"
    assert tracker.run["splits"] == [{"act": 1, "t": 300, "level": 2}], \
        "crossing the act boundary records the act-1 split"

    # events that change nothing produce no ops
    assert main.dispatch_events([("zone", "Nowhere Special"),
                                 ("join", "RandomTownGuy")],
                                engine, party, tracker) == []
    # tracker=None (timer disabled) dispatches identically, just untracked
    ops_untracked = main.dispatch_events(
        [("level", ("Hero", "Witch", 3))], engine, party, None)
    assert ops_untracked[0] == ("level", 3)
    assert tracker.run["levels"][-1]["level"] == 2, "tracker untouched"

    # -------------------------------------------- tracker status + XP warn
    clock.advance(22)                          # elapsed 5:22
    status = main.tracker_status(tracker, engine.current(), party.my_level)
    assert status.startswith("A2 5:22"), status
    assert "⚠ XP -" in status, \
        "level 3 in an arealvl-13 zone must warn about the XP penalty"
    # matched level -> no warning bit
    assert "⚠" not in main.tracker_status(tracker,
                                          {"act": 2, "arealvl": 13}, 13)
    # no tracker (timer off): XP warning still computes, no timer bit
    solo = main.tracker_status(None, {"act": 1, "arealvl": 5}, 30)
    assert solo.startswith("⚠ XP -")
    # towns without arealvl never warn
    assert main.tracker_status(None, {"act": 1}, 90) == ""

    # -------------------------------------------- save on exit (guarded IO)
    assert main.save_run(None) is None
    path = main.save_run(tracker)
    assert path and os.path.exists(path)
    saved = load_run(path)
    assert saved["ended"] and saved["splits"] == tracker.run["splits"]
    assert not os.path.exists(os.path.join(tmp, "runs", "pb.json")), \
        "an incomplete (act 2) session must not claim pb.json on exit"

    # a run that reached act 10 does claim the PB on exit
    done = RunTracker(clock=clock, runs_dir=os.path.join(tmp, "runs"))
    done.start("Hero", "Witch", "itest")
    done.on_zone("Lioneye's Watch", 1)
    clock.advance(100)
    done.on_zone("Oriath Docks", 10)
    assert main.save_run(done)
    assert os.path.exists(os.path.join(tmp, "runs", "pb.json"))

    # tracker IO failure never raises out of save_run
    blocked = os.path.join(tmp, "blocked")
    open(blocked, "w", encoding="utf-8").close()      # file where a dir goes
    bad = RunTracker(clock=clock, runs_dir=blocked)
    bad.start("Hero", "Witch", "itest")
    assert main.save_run(bad) is None, "IO error is swallowed, not raised"
    never_started = RunTracker(clock=clock, runs_dir=tmp)
    assert main.save_run(never_started) is None

    # -------------------------------------------- clipboard evaluator glue
    with open(os.path.join(ROOT, "tests", "fixtures_items",
                           "rare_boots.txt"), encoding="utf-8") as f:
        boots_text = f.read()
    parsed = itemtext.parse(boots_text)
    want = item_rules.evaluate(parsed, {"level": 22, "act": 3,
                                        "links_best": 3, "build": None})
    got = main.evaluate_clipboard_text(boots_text, 22, 3, links_best=3)
    assert got == (want[0], "Gale Trail", want[1]), \
        "clipboard glue must agree with item_rules.evaluate"
    assert got[0] in ("TAKE", "SKIP", "CHECK")

    assert main.evaluate_clipboard_text("", 10, 1) is None
    assert main.evaluate_clipboard_text("just some chat text", 10, 1) is None
    assert main.evaluate_clipboard_text("x" * 9000, 10, 1) is None, \
        "oversized clipboard payloads are ignored"
    with open(os.path.join(ROOT, "tests", "fixtures_items", "garbage.txt"),
              encoding="utf-8") as f:
        assert main.evaluate_clipboard_text(f.read(), 10, 1) is None
finally:
    shutil.rmtree(tmp)

# ------------------------------------------------ pair-row glue, numerically
# The Divine example straight from the market/sources.py docstring:
# buy 500 (chaos to buy 1) / sell 422 (chaos received selling 1).
divine = {"ts": "2026-07-07T12:00:00", "source": "poe.ninja",
          "league": "Mirage", "item": "Divine Orb",
          "buy": 500.0, "sell": 422.0, "buy_vol": 900.0, "sell_vol": 850.0,
          "raw": None}
pairs = {p["item"]: p for p in pair_rows_from_currency([divine])}
assert set(pairs) == {"chaos->Divine Orb", "Divine Orb->chaos"}
assert abs(pairs["chaos->Divine Orb"]["sell"] - 0.002) < 1e-15, \
    "chaos->divine rate = 1/buy = 1/500"
assert pairs["chaos->Divine Orb"]["sell_vol"] == 900.0 * 500.0, \
    "listing counts normalized to chaos depth (900 x 500c)"
assert pairs["Divine Orb->chaos"]["sell"] == 422.0
assert pairs["Divine Orb->chaos"]["sell_vol"] == 850.0 * 422.0

# implied round trip: 1 chaos -> 0.002 divine -> 0.844 chaos, then two 4%
# haircuts => 0.7778 chaos. Loses money -> correctly NOT an opportunity.
P = {"haircut": 0.04, "min_margin_pct": 5.0, "min_vol": 20.0,
     "bankroll_c": 2000.0}
assert scan([divine], P) == []
# ...while a genuinely mispriced quote IS found through the same glue
crossed = dict(divine, item="Mirror Shard", buy=100.0, sell=130.0,
               buy_vol=50.0, sell_vol=45.0)
found = scan([divine, crossed], P)
assert len(found) == 1 and found[0]["kind"] == "cycle"
assert abs(found[0]["margin_pct"]
           - ((130.0 / 100.0) * 0.96 ** 2 - 1) * 100) <= 0.1

print("ALL TESTS PASSED")
print(f"  dispatch walked act 1 -> act 2 in {len(act1_zones)} zones")
print(f"  clipboard verdict for the boots fixture: {got[0]} — {got[2]}")
