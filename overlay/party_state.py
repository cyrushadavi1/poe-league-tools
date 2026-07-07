"""Tracks party members' levels, presence and deaths from log events.

Pure stdlib -- no Qt imports -- so it can be unit tested headless.
Everything here is derived from Client.txt lines the game already
prints for party play (level-ups, area joins/leaves, deaths).

Config (overlay/config.json -> "party"):
  me:       your character name. With a party configured this is how
            your own level-ups are told apart from your mates'.
  members:  the 1-2 other characters' names.
  gap_warn: flag a member whose level differs from yours by >= this
            (drifting apart hurts shared XP and quest sync).
"""

GAP_WARN_DEFAULT = 3


class PartyState:
    def __init__(self, me="", members=(), gap_warn=GAP_WARN_DEFAULT):
        self.me = me
        self.my_level = 1
        self.my_deaths = 0
        self.gap_warn = gap_warn
        self.members = {
            name: {"level": None, "cls": "", "in_area": False, "deaths": 0}
            for name in members
        }

    # -- event intake -------------------------------------------------------
    def on_event(self, kind, data):
        """Feed a ClientWatcher event. Returns one of:

        ('me_level', int)   -- your level changed (update header)
        ('party', None)     -- party display should refresh
        ('death', name)     -- someone died (worth a flash); name may be me
        None                -- nothing party-relevant happened
        """
        if kind == "level":
            name, cls, level = data
            if self.is_me(name):
                self.my_level = level
                return ("me_level", level)
            if name in self.members:
                self.members[name]["level"] = level
                self.members[name]["cls"] = cls
                return ("party", None)
        elif kind == "join":
            if data in self.members:
                self.members[data]["in_area"] = True
                return ("party", None)
        elif kind == "leave":
            if data in self.members:
                self.members[data]["in_area"] = False
                return ("party", None)
        elif kind == "slain":
            if self.is_me(data):
                self.my_deaths += 1
                return ("death", data)
            if data in self.members:
                self.members[data]["deaths"] += 1
                return ("death", data)
        return None

    def is_me(self, name):
        # With no configured name (solo, old behaviour) any non-member
        # event is assumed to be about us.
        if self.me:
            return name == self.me
        return name not in self.members

    # -- display ------------------------------------------------------------
    def gap_warning(self, name):
        lvl = self.members[name]["level"]
        return lvl is not None and abs(lvl - self.my_level) >= self.gap_warn

    def status_line(self):
        """One-line party summary for the overlay; '' when no party set."""
        if not self.members:
            return ""
        bits = []
        for name, m in self.members.items():
            lvl = "?" if m["level"] is None else str(m["level"])
            here = "●" if m["in_area"] else "○"   # ● / ○
            warn = " ⚠" if self.gap_warning(name) else ""
            deaths = f" ☠{m['deaths']}" if m["deaths"] else ""
            bits.append(f"{here} {name} {lvl}{warn}{deaths}")
        return "  ".join(bits)

    def warnings(self):
        """List of active problems, e.g. ['Bob is 5 levels behind']."""
        out = []
        for name, m in self.members.items():
            if self.gap_warning(name):
                diff = m["level"] - self.my_level
                word = "ahead" if diff > 0 else "behind"
                out.append(f"{name} is {abs(diff)} levels {word}")
        return out
