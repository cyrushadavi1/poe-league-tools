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
