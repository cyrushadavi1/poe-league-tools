"""Loads route JSON files and tracks progress through steps."""
import json
import os
import re


class RouteEngine:
    def __init__(self, routes_dir, lookahead=4):
        self.steps = []
        self.lookahead = lookahead
        self.i = 0
        # numeric sort: plain sorted() would put act10 between act1 and act2
        names = [n for n in os.listdir(routes_dir)
                 if re.fullmatch(r"act\d+\.json", n)]
        for name in sorted(names, key=lambda n: int(n[3:-5])):
            with open(os.path.join(routes_dir, name), encoding="utf-8") as f:
                act = json.load(f)
            for s in act["steps"]:
                s["act"] = act["act"]
                self.steps.append(s)
        if not self.steps:
            raise SystemExit(f"No route files (actN.json) found in {routes_dir}")

    # -- events -----------------------------------------------------------
    def on_zone(self, zone):
        """Auto-advance when an entered zone matches an *upcoming* step.

        Only scans a small window ahead, so town portals, logouts and
        re-rolled instances of the current zone don't teleport the guide.
        Bigger skips are handled with the manual next/prev hotkeys.
        """
        z = zone.strip().lower()
        cur = self.current()
        if cur and cur.get("zone", "").lower() == z:
            return False                       # new instance of same zone
        hi = min(len(self.steps), self.i + 1 + self.lookahead)
        for j in range(self.i + 1, hi):
            if self.steps[j].get("zone", "").lower() == z:
                self.i = j
                return True
        return False

    def _step_level(self, j):
        """Monster level at step j; towns carry no arealvl, so borrow
        the nearest same-act step that has one."""
        s = self.steps[j]
        if s.get("arealvl"):
            return int(s["arealvl"])
        act = s["act"]
        for k in list(range(j + 1, len(self.steps))) + \
                list(range(j - 1, -1, -1)):
            t = self.steps[k]
            if t["act"] == act and t.get("arealvl"):
                return int(t["arealvl"])
        return 1

    def fast_forward(self, zones, known_level=None):
        """One-shot mid-campaign resume: jump to where the log's zone
        history (oldest first) says the player already is.

        Two estimates:
        1. Walk the whole history through on_zone — a log that covers
           the run from the start replays it exactly like live play.
        2. The LAST history zone that names a route step — covers a
           tail that starts mid-campaign (fresh overlay install, log
           rotated, or an alt's early zones polluting the walk).
           Duplicate zone names (act 1 vs 6 towns...) are broken by
           arealvl closest to known_level, deeper step on ties.
        With a known character level the estimate whose area level
        fits it better wins; without one, a walk that moved is
        trusted over the guess.

        Startup-only by design: during play the lookahead window is
        what stops towns/portals from teleporting the guide.
        Returns the number of steps skipped.
        """
        if not zones:
            return 0
        start = self.i
        for z in zones:
            self.on_zone(z)
        walk_i, self.i = self.i, start

        cand_i = start
        for z in reversed(zones):
            zl = z.strip().lower()
            matches = [j for j in range(start, len(self.steps))
                       if self.steps[j].get("zone", "").lower() == zl]
            if matches:
                if known_level:
                    matches.sort(key=lambda j: (
                        abs(self._step_level(j) - known_level), -j))
                    cand_i = matches[0]
                else:
                    cand_i = matches[-1]
                break

        if known_level:
            self.i = min((walk_i, cand_i), key=lambda j: (
                abs(self._step_level(j) - known_level), -j))
        else:
            self.i = walk_i if walk_i > start else cand_i
        return self.i - start

    # -- manual navigation --------------------------------------------------
    def next(self):
        self.i = min(self.i + 1, len(self.steps) - 1)

    def prev(self):
        self.i = max(self.i - 1, 0)

    def current(self):
        return self.steps[self.i] if self.steps else None

    def peek(self, n=1):
        j = self.i + n
        return self.steps[j] if j < len(self.steps) else None

    def progress(self):
        """Returns (step_number_within_act, steps_in_act, act_number)."""
        act = self.steps[self.i]["act"]
        in_act = [s for s in self.steps if s["act"] == act]
        return in_act.index(self.steps[self.i]) + 1, len(in_act), act
