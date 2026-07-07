"""Headless tests for overlay/run_tracker.py: splits, XP penalty, PB files."""
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "overlay")]

from run_tracker import (RunTracker, fmt_delta, fmt_t, load_run,   # noqa: E402
                         run_total, xp_penalty, xp_warning)


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


def close(a, b, tol=1e-9):
    return abs(a - b) < tol


# ---------------------------------------------------------------- xp penalty
# Formula (live-verified against poewiki.net/wiki/Experience):
#   safe_zone = 3 + floor(L/16); eff = max(|L - area| - safe_zone, 0)
#   mult = ((L+5) / (L+5 + eff**2.5)) ** 1.5, floored at 0.01
assert xp_penalty(20, 20) == 1.0
assert xp_penalty(20, 24) == 1.0, "diff 4 == safe zone 4 -> no penalty"
assert xp_penalty(20, 16) == 1.0, "penalty is symmetric inside the safe zone"
# L20 vs 25: eff 1 -> (25/26)**1.5
assert close(xp_penalty(20, 25), 0.9428660343181925, 1e-12)
# L20 vs 30: eff 6 -> (25/(25 + 6**2.5))**1.5
assert close(xp_penalty(20, 30), 0.10381164966786455, 1e-12)
# L70 vs 60: safe zone 7, eff 3 -> (75/(75 + 3**2.5))**1.5
assert close(xp_penalty(70, 60), 0.7533253805273331, 1e-12)
# poewiki worked examples: level 24 char gets 95% / 52.5% / 20% XP
# from level 19 / 17 / 15 monsters.
assert close(xp_penalty(24, 19), 0.95, 5e-3)
assert close(xp_penalty(24, 17), 0.525, 5e-3)
assert close(xp_penalty(24, 15), 0.20, 5e-3)
# level >= 95: * 1/(1 + 0.1*(L-94)) and * 1/(3.1 penalty table)
assert close(xp_penalty(95, 95), 1 / 1.1 / 1.065, 1e-12)
assert close(xp_penalty(97, 97), 1 / 1.3 / 1.187, 1e-12)
assert xp_penalty(94, 94) == 1.0, "the 94+ rule starts at 95"
# hard floor at 1%
assert xp_penalty(10, 60) == 0.01
# in (0, 1] everywhere, monotonically non-increasing with distance
prev = 1.0
for area in range(20, 46):
    m = xp_penalty(20, area)
    assert 0 < m <= 1.0 and m <= prev
    prev = m

# ------------------------------------------------------------------ warning
assert xp_warning(20, 20) is None
assert xp_warning(20, 24) is None
assert xp_warning(24, 19) is None, "0.9504 >= 0.95 threshold -> no warning"
assert xp_warning(20, 25) == "XP -6%"
assert xp_warning(20, 30) == "XP -90%"
assert xp_warning(70, 60) == "XP -25%"
assert xp_warning(10, 60) == "XP -99%"
# also exposed on the tracker class
assert RunTracker.xp_penalty(20, 20) == 1.0
assert RunTracker.warning(20, 30) == "XP -90%"

# --------------------------------------------------------------- formatting
assert fmt_t(0) == "0:00"
assert fmt_t(95) == "1:35"
assert fmt_t(2482) == "41:22"
assert fmt_t(2710) == "45:10"
assert fmt_t(3722) == "1:02:02"
assert fmt_delta(-130) == "-2:10"
assert fmt_delta(45) == "+0:45"
assert fmt_delta(0) == "+0:00"

# ------------------------------------------------- splits with a fake clock
tmp = tempfile.mkdtemp(prefix="poe_tracker_test_")
try:
    runs_dir = os.path.join(tmp, "runs")
    clk = FakeClock()
    tr = RunTracker(clock=clk, runs_dir=runs_dir)

    # events before start() are ignored, displays are empty
    tr.on_zone("The Coast", 1)
    tr.on_level(3)
    tr.on_death("Nobody")
    assert tr.run is None and tr.elapsed() == 0.0
    assert tr.status_line(1) == "" and tr.splits_str() == ""

    tr.start("Exile59", "Witch", "3.29")
    assert tr.run["started"] and tr.run["ended"] is None
    tr.on_zone("Lioneye's Watch", 1)

    clk.advance(95)
    tr.on_level(2)
    assert tr.run["levels"] == [{"level": 2, "t": 95}]

    clk.advance(405)                       # t=500
    tr.on_zone("The Coast", 1)             # same act: no split
    assert tr.run["splits"] == []

    clk.advance(2200)                      # t=2700
    tr.on_level(12)
    clk.advance(10)                        # t=2710
    tr.on_zone("The Southern Forest", 2)   # act 1 -> 2
    assert tr.run["splits"] == [{"act": 1, "t": 2710, "level": 12}]
    assert tr.act == 2
    assert tr.splits_str() == "A1 45:10"

    clk.advance(390)                       # t=3100
    tr.on_death("Exile59")
    assert tr.run["deaths"] == [{"t": 3100, "who": "Exile59"}]

    tr.on_zone("Lioneye's Watch", 1)       # portal back: no regress
    assert tr.act == 2 and len(tr.run["splits"]) == 1
    tr.on_zone("The Riverways", 2)         # same act again: no split
    assert len(tr.run["splits"]) == 1

    assert close(tr.elapsed(), 3100.0)
    assert tr.status_line(2) == "A2 51:40", "no PB -> no delta"
    assert tr.vs_pb(1) is None and tr.vs_pb(2) is None

    # act jump 2 -> 4 records a split for every act crossed (2 and 3)
    clk.advance(3500)                      # t=6600
    tr.on_level(33)
    tr.on_zone("The Aqueduct", 4)
    assert tr.run["splits"][1:] == [{"act": 2, "t": 6600, "level": 33},
                                    {"act": 3, "t": 6600, "level": 33}]

    # ------------------------------------------------ save/load round-trip
    path = tr.save()
    assert os.path.dirname(path) == runs_dir
    assert os.path.basename(path).startswith("run_")
    on_disk = load_run(path)
    assert on_disk == tr.run
    assert list(on_disk.keys()) == ["league", "character", "class",
                                    "started", "ended", "splits",
                                    "levels", "deaths"], \
        "run-file keys must match docs/INTERFACES.md exactly"
    assert on_disk["league"] == "3.29" and on_disk["class"] == "Witch"
    assert on_disk["character"] == "Exile59" and on_disk["ended"] is None
    assert on_disk["splits"][0] == {"act": 1, "t": 2710, "level": 12}
    assert on_disk["levels"][0] == {"level": 2, "t": 95}
    assert on_disk["deaths"][0] == {"t": 3100, "who": "Exile59"}

    # ---------------------- finish(): first COMPLETED (act 10) run is PB
    clk.advance(11000)                     # t=17600
    tr.on_zone("Oriath Docks", 10)         # reach the final act
    assert tr.completed()
    clk.advance(400)                       # elapsed = 18000
    tr.finish()
    assert tr.run["ended"] is not None
    assert run_total(tr.run) == 18000
    started = datetime.fromisoformat(tr.run["started"])
    ended = datetime.fromisoformat(tr.run["ended"])
    assert (ended - started).total_seconds() == 18000
    with open(os.path.join(runs_dir, "pb.json"), encoding="utf-8") as f:
        assert json.load(f) == tr.run, "no previous PB -> this run is PB"

    # --------------------------------------------------- PB comparison run
    clk2 = FakeClock()
    tr2 = RunTracker(clock=clk2, runs_dir=runs_dir)
    tr2.start("Exile60", "Witch", "3.29")
    assert tr2.pb is not None and tr2.pb["character"] == "Exile59"
    tr2.on_zone("Lioneye's Watch", 1)
    clk2.advance(2500)
    tr2.on_level(12)
    clk2.advance(80)                       # t=2580 (PB act-1 split was 2710)
    tr2.on_zone("The Southern Forest", 2)
    assert tr2.vs_pb(1) == "-2:10 vs PB"   # 2580 - 2710 = -130
    assert tr2.vs_pb(2) is None, "act 2 not completed yet"
    clk2.advance(120)                      # elapsed 2700 = 45:00, in act 2
    assert tr2.status_line(2) == "A2 45:00 (-2:10 PB)"
    assert tr2.status_line(1) == "A1 45:00", \
        "act 1 has no previous act to compare"

    # faster overall AND completed -> pb.json replaced
    clk2.advance(14000 - 2700)             # t=14000
    tr2.on_zone("Oriath Docks", 10)        # completes the run
    clk2.advance(1000)                     # elapsed = 15000 < 18000
    tr2.finish()
    with open(os.path.join(runs_dir, "pb.json"), encoding="utf-8") as f:
        assert json.load(f)["character"] == "Exile60"

    # ------------------------------------------- slower run keeps the PB
    clk3 = FakeClock()
    tr3 = RunTracker(clock=clk3, runs_dir=runs_dir)
    tr3.start("SlowChar", "Marauder", "3.29")
    tr3.on_zone("Lioneye's Watch", 1)
    clk3.advance(2900)
    tr3.on_zone("The Southern Forest", 2)  # split slower than PB's 2580
    assert tr3.vs_pb(1) == "+5:20 vs PB"   # 2900 - 2580 = +320
    tr3.on_zone("Oriath Docks", 10)        # completed, but slower
    clk3.advance(17100)                    # elapsed = 20000 > 15000
    tr3.finish()
    with open(os.path.join(runs_dir, "pb.json"), encoding="utf-8") as f:
        assert json.load(f)["character"] == "Exile60", \
            "slower run must not overwrite pb.json"

    # ---------------------- aborted sessions never claim the PB (fast quit)
    clk4 = FakeClock()
    tr4 = RunTracker(clock=clk4, runs_dir=runs_dir)
    tr4.start("ConfigChecker", "Witch", "3.29")
    clk4.advance(180)                      # 3-minute look at the overlay
    assert not tr4.completed()
    tr4.finish()                           # run file saved, PB untouched
    with open(os.path.join(runs_dir, "pb.json"), encoding="utf-8") as f:
        assert json.load(f)["character"] == "Exile60", \
            "a short aborted session must never become the permanent PB"

    # -------------- pre-game idle: the clock re-anchors on the first zone
    clk5 = FakeClock()
    tr5 = RunTracker(clock=clk5, runs_dir=os.path.join(tmp, "runs5"))
    tr5.start("QueueWaiter", "Witch", "3.29")
    clk5.advance(1500)                     # 25 min login queue / char select
    tr5.on_zone("Lioneye's Watch", 1)      # the run really starts here
    clk5.advance(600)
    tr5.on_zone("The Southern Forest", 2)
    assert tr5.run["splits"] == [{"act": 1, "t": 600, "level": 1}], \
        "queue idle before the first zone must not inflate the A1 split"

    # run files are written under runs_dir, named by start timestamp
    # (runs started within the same wall-clock second share a name, so
    # this fast test may produce just one file)
    run_files = [n for n in os.listdir(runs_dir) if n.startswith("run_")]
    assert len(run_files) >= 1
    assert all(n.endswith(".json") for n in run_files)

    # missing/corrupt pb.json degrades to None (no crash)
    empty_dir = os.path.join(tmp, "empty")
    t_empty = RunTracker(clock=FakeClock(), runs_dir=empty_dir)
    t_empty.start("X", "Scion", "3.29")
    assert t_empty.pb is None and t_empty.vs_pb(1) is None
    os.makedirs(empty_dir, exist_ok=True)
    with open(os.path.join(empty_dir, "pb.json"), "w") as f:
        f.write("{not json")
    assert t_empty.load_pb() is None
finally:
    shutil.rmtree(tmp)

print("ALL TESTS PASSED")
print(f"  xp_penalty(20, 30) = {xp_penalty(20, 30):.4f} -> "
      f"{xp_warning(20, 30)}")
