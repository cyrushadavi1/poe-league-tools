"""Per-act split timers, XP-penalty warnings, and run persistence.

Pure stdlib, no Qt. Pure logic: the tracker takes client_watcher-shaped
events plus an injectable monotonic clock and returns display strings;
file IO is isolated in the small save/load helpers at the bottom.

Run-file format (docs/INTERFACES.md — "Run file"):

    {"league": "3.29", "character": "Name", "class": "Witch",
     "started": "2026-07-24T20:00:00", "ended": null,
     "splits": [{"act": 1, "t": 2710, "level": 12}],
     "levels": [{"level": 2, "t": 95}],
     "deaths": [{"t": 3100, "who": "Name"}]}

`t` = whole seconds since run start. This module owns writing
runs/run_<startts>.json and runs/pb.json; tools/retro.py consumes them.
"""
import json
import os
import time
from datetime import datetime, timedelta

# --------------------------------------------------------------- xp penalty
#
# The real PoE level-difference XP penalty, live-verified 2026-07-07 against
# https://www.poewiki.net/wiki/Experience ("Level difference penalty"):
#
#   SafeZone            = floor(3 + PlayerLevel / 16)
#   EffectiveDifference = max(|PlayerLevel - MonsterLevel| - SafeZone, 0)
#   XPMultiplier        = ((PlayerLevel + 5)
#                          / (PlayerLevel + 5 + EffectiveDifference**2.5)) ** 1.5
#
# For player levels >= 95 two further factors apply (both verified live):
#   * 1 / (1 + 0.1 * (PlayerLevel - 94))
#   * 1 / (the "3.1 XP Penalty" table below, introduced in patch 3.1)
# and the final multiplier is floored at 0.01 (min 1% of raw experience).
#
# Wiki worked examples (used as test anchors): a level 24 character gets
# 95% XP from level 19 monsters, 52.5% from level 17, 20% from level 15.

# Verified: the extra flat penalty for levels 95-99 ("3.1 XP Penalty").
# Level 100 gains no XP; values past 99 clamp to the level-99 entry.
_XP_PENALTY_31 = {95: 1.065, 96: 1.115, 97: 1.187, 98: 1.2825, 99: 1.4}


def xp_penalty(char_level, area_level):
    """XP multiplier in (0, 1] for a char_level character in an
    area_level zone (area monster level, routes/actN.json `arealvl`)."""
    safe_zone = 3 + char_level // 16
    eff = max(abs(char_level - area_level) - safe_zone, 0)
    mult = ((char_level + 5) / (char_level + 5 + eff ** 2.5)) ** 1.5
    if char_level >= 95:
        mult *= 1.0 / (1.0 + 0.1 * (char_level - 94))
        mult *= 1.0 / _XP_PENALTY_31[min(char_level, 99)]
    return max(mult, 0.01)


def xp_warning(char_level, area_level, threshold=0.95):
    """None, or a short overlay warning like 'XP -38%' when the
    multiplier drops below `threshold` (default 0.95)."""
    mult = xp_penalty(char_level, area_level)
    if mult >= threshold:
        return None
    return f"XP -{round((1.0 - mult) * 100)}%"


# --------------------------------------------------------------- formatting

def fmt_t(seconds):
    """Seconds -> 'M:SS', or 'H:MM:SS' past an hour (e.g. 2482 -> '41:22')."""
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"


def fmt_delta(seconds):
    """Signed delta, e.g. -130 -> '-2:10', 45 -> '+0:45'."""
    s = int(round(seconds))
    return ("-" if s < 0 else "+") + fmt_t(abs(s))


# ------------------------------------------------------------------ tracker

class RunTracker:
    """Tracks one campaign run: per-act splits, level/death log, PB compare.

    Feed it the client_watcher events (the route engine supplies the
    current act for zone events). All timestamps come from the injected
    monotonic `clock`, so tests drive it with a fake.
    """

    def __init__(self, clock=time.monotonic, runs_dir="runs"):
        self.clock = clock
        self.runs_dir = runs_dir
        self.run = None          # the run dict, exactly the file format
        self.pb = None           # loaded pb.json dict, or None
        self.act = None          # act of the current step
        self.level = 1           # current character level
        self.zone = None
        self._t0 = 0.0

    # -- lifecycle ----------------------------------------------------------
    def start(self, character, cls, league):
        """Begin a run: wall-clock ISO 'started' + monotonic zero."""
        self._t0 = self.clock()
        self.act = None
        self.level = 1
        self.zone = None
        self.run = {
            "league": league,
            "character": character,
            "class": cls,
            "started": datetime.now().isoformat(timespec="seconds"),
            "ended": None,
            "splits": [],
            "levels": [],
            "deaths": [],
        }
        self.pb = self.load_pb()
        return self.run

    def finish(self):
        """Stamp 'ended', save the run file, update pb.json if the run
        COMPLETED (reached act 10) and is faster overall (total run
        seconds). Returns the saved run-file path.

        The completion guard matters: finish() also runs on overlay
        exit, and without it a 3-minute config-check session would
        become an unbeatable zero-split pb.json forever."""
        if not self.run:
            raise ValueError("finish() before start()")
        total = int(round(self.elapsed()))
        started = datetime.fromisoformat(self.run["started"])
        self.run["ended"] = (started + timedelta(seconds=total)) \
            .isoformat(timespec="seconds")
        path = self.save()
        if self.completed():
            pb_total = run_total(self.pb)
            if pb_total is None or total < pb_total:
                _write_json(os.path.join(self.runs_dir, "pb.json"), self.run)
                self.pb = dict(self.run)
        return path

    def completed(self):
        """True when the run reached act 10 (the final campaign act)."""
        if not self.run:
            return False
        if (self.act or 0) >= 10:
            return True
        # entering act 10 records the act-9 split, so this is equivalent
        return any(s.get("act", 0) >= 9 for s in self.run["splits"])

    # -- events (from client_watcher, act from the route engine) -------------
    def on_zone(self, zone, act):
        """Zone entered; `act` is the act of the current route step.
        Records a split for each act the run advances past (normally one)."""
        if not self.run:
            return
        self.zone = zone
        if self.act is None:
            # Re-anchor the clock to the first zone of the run: overlay
            # launch precedes the real start by arbitrary idle (login
            # queue, character creation) that must not inflate splits.
            self._t0 = self.clock()
            self.act = act
            return
        if act > self.act:
            t = int(round(self.elapsed()))
            for n in range(self.act, act):
                self.run["splits"].append(
                    {"act": n, "t": t, "level": self.level})
            self.act = act
        # act < self.act (portal back to an old town) never regresses splits

    def on_level(self, level):
        if not self.run:
            return
        self.level = level
        self.run["levels"].append(
            {"level": level, "t": int(round(self.elapsed()))})

    def on_death(self, who):
        if not self.run:
            return
        self.run["deaths"].append(
            {"t": int(round(self.elapsed())), "who": who})

    # -- xp penalty on the tracker too (same functions) ----------------------
    xp_penalty = staticmethod(xp_penalty)
    warning = staticmethod(xp_warning)

    # -- display -------------------------------------------------------------
    def elapsed(self):
        """Seconds since start() by the monotonic clock (0.0 if not started)."""
        return self.clock() - self._t0 if self.run else 0.0

    def splits_str(self):
        """All recorded splits, e.g. 'A1 45:10  A2 1:22:03'."""
        if not self.run:
            return ""
        return "  ".join(f"A{s['act']} {fmt_t(s['t'])}"
                         for s in self.run["splits"])

    def vs_pb(self, act):
        """'+/-M:SS vs PB' comparing this run's split for `act` against
        the PB's, or None if either split is missing."""
        d = self._pb_delta(act)
        return None if d is None else f"{fmt_delta(d)} vs PB"

    def status_line(self, act):
        """Overlay meta row, e.g. 'A3 41:22 (-2:10 PB)'. The PB delta is
        for the last completed act (act - 1); omitted when unavailable."""
        if not self.run:
            return ""
        line = f"A{act} {fmt_t(self.elapsed())}"
        d = self._pb_delta(act - 1)
        if d is not None:
            line += f" ({fmt_delta(d)} PB)"
        return line

    def _pb_delta(self, act):
        ours = _split_t(self.run, act)
        theirs = _split_t(self.pb, act)
        if ours is None or theirs is None:
            return None
        return ours - theirs

    # -- persistence (the only file IO, all through the helpers below) -------
    def save(self, path=None):
        """Write the run file; default runs_dir/run_<startts>.json."""
        if not self.run:
            raise ValueError("save() before start()")
        if path is None:
            stamp = self.run["started"].replace("-", "").replace(":", "")
            path = os.path.join(self.runs_dir, f"run_{stamp}.json")
        _write_json(path, self.run)
        return path

    def load_pb(self):
        """Read runs_dir/pb.json -> dict, or None if absent/unreadable."""
        return _read_json(os.path.join(self.runs_dir, "pb.json"))


# ------------------------------------------------------------ small helpers

def _split_t(run, act):
    for s in (run or {}).get("splits", []):
        if s.get("act") == act:
            return s.get("t")
    return None


def run_total(run):
    """Total seconds of a finished run dict (ended - started), else None."""
    if not run:
        return None
    try:
        started = datetime.fromisoformat(run["started"])
        ended = datetime.fromisoformat(run["ended"])
    except (KeyError, TypeError, ValueError):
        return None
    return (ended - started).total_seconds()


def load_run(path):
    """Read one run file -> dict (raises on missing/bad file)."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_json(path, obj):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1)
        f.write("\n")
