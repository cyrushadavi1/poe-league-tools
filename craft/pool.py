"""Legal mod pools and item-mod matching over data/repoe_craft.json.

Pure stdlib, no Qt, import-safe. This module is the deterministic side of
the crafting copilot: every number shown to the player or the LLM (tiers,
ranges, level gates, weights, essence caps, bench costs) is computed here
from the compiled RePoE dataset — the LLM only ever *selects and explains*.

Dataset: produced by tools/refresh_repoe.py (format documented there).

Tier convention matches the trade site: T1 = the highest-required-level
mod of a ladder that can spawn on this base's tags. Ladders group mods by
(generation, type, stat ids); `groups` exclusivity is carried through for
the copilot but not enforced here.
"""
from __future__ import annotations

import json
import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DATA_PATH = os.path.join(_ROOT, "data", "repoe_craft.json")

# Affix caps by rarity (normal crafting; no special bases).
_CAPS = {"Rare": 3, "Magic": 1, "Normal": 0}

# Ctrl+C shows plural display class names; the dataset uses RePoE's ids.
# Identity covers Boots/Gloves (canonically plural); this map covers the
# rest we care about. Fallback: strip one trailing 's'.
_CLASS_DISPLAY = {
    "Wands": "Wand", "Helmets": "Helmet", "Body Armours": "Body Armour",
    "Shields": "Shield", "Amulets": "Amulet", "Rings": "Ring",
    "Belts": "Belt", "Quivers": "Quiver", "Daggers": "Dagger",
    "Rune Daggers": "Rune Dagger", "Claws": "Claw", "Sceptres": "Sceptre",
    "Staves": "Staff", "Warstaves": "Warstaff", "Bows": "Bow",
    "One Hand Swords": "One Hand Sword", "One Hand Axes": "One Hand Axe",
    "One Hand Maces": "One Hand Mace", "Two Hand Swords": "Two Hand Sword",
    "Two Hand Axes": "Two Hand Axe", "Two Hand Maces": "Two Hand Mace",
    "Thrusting One Hand Swords": "Thrusting One Hand Sword",
    "Life Flasks": "Life Flask", "Mana Flasks": "Mana Flask",
    "Hybrid Flasks": "Hybrid Flask", "Utility Flasks": "Utility Flask",
}

_RANGE_RE = re.compile(r"\((\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)\)")
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def canon_class(display):
    """Ctrl+C 'Item Class:' value -> dataset item class id."""
    if not display:
        return ""
    if display in _CLASS_DISPLAY:
        return _CLASS_DISPLAY[display]
    return display


def _norm(text):
    """Mod line/template -> shape key: ranges and numbers become '#'."""
    return _NUM_RE.sub("#", _RANGE_RE.sub("#", text)).strip()


def _template_bounds(template_line):
    """[(lo, hi), ...] for each numeric slot of a template line.

    '(3-9)' -> (3.0, 9.0); a fixed number like '4' -> (4.0, 4.0).
    """
    bounds = []
    pos = 0
    while True:
        rm = _RANGE_RE.search(template_line, pos)
        nm = _NUM_RE.search(template_line, pos)
        if rm and rm.start() <= (nm.start() if nm else rm.start()):
            bounds.append((float(rm.group(1)), float(rm.group(2))))
            pos = rm.end()
        elif nm:
            bounds.append((float(nm.group()), float(nm.group())))
            pos = nm.end()
        else:
            return bounds


def _values_fit(line, template_line):
    """Do the line's rolled numbers fall inside the template's bounds?"""
    bounds = _template_bounds(template_line)
    values = [float(v) for v in _NUM_RE.findall(line)]
    if len(values) != len(bounds):
        return not bounds and not values
    return all(lo <= v <= hi for v, (lo, hi) in zip(values, bounds))


class CraftData:
    """Loaded compiled dataset with the indexes the copilot needs."""

    def __init__(self, blob):
        self.meta = blob.get("meta", {})
        self.bases = blob.get("bases", {})
        self.mods = blob.get("mods", {})
        self.essences = blob.get("essences", [])
        self.bench = blob.get("bench", [])
        # normalized template line -> [(mod key, line index, line count)]
        self._line_index = {}
        for key, m in self.mods.items():
            lines = m["t"].split("\n")
            for i, ln in enumerate(lines):
                self._line_index.setdefault(_norm(ln), []).append(
                    (key, i, len(lines)))
        # longest-first so 'Vaal Regalia' wins over any shorter substring
        self._base_names = sorted(self.bases, key=len, reverse=True)

    @classmethod
    def load(cls, path=None):
        with open(path or DEFAULT_DATA_PATH, encoding="utf-8") as f:
            return cls(json.load(f))

    # ------------------------------------------------------------- lookups

    def find_base(self, text):
        """Longest dataset base name contained in *text* (a Ctrl+C name or
        base line — magic items embed the base inside affix names)."""
        if not text:
            return None
        for name in self._base_names:
            if name and name in text:
                return name
        return None

    def _weight(self, m, tags):
        """Spawn weight under first-match-wins tag resolution (the game's
        semantics: the first spawn_weights entry whose tag the item has
        decides, even if that weight is 0)."""
        for tag, w in m["sw"]:
            if tag in tags:
                return w
        return 0

    # ---------------------------------------------------------------- pool

    def pool(self, base_name, ilvl=100):
        """Rollable mod ladders for a base, prefix/suffix, ranked by total
        reachable spawn weight. Essence-only mods are excluded (they don't
        roll); zero-weight mods are excluded (can't roll here)."""
        base = self.bases[base_name]
        tags = set(base["tags"])
        dom = base.get("dom", "item")
        groups = {}
        for key, m in self.mods.items():
            if m["ess"] or m["dom"] != dom:
                continue
            w = self._weight(m, tags)
            if w <= 0:
                continue
            gk = (m["gen"], m["type"], tuple(s[0] for s in m["stats"]))
            groups.setdefault(gk, []).append((key, m, w))
        out = {"prefix": [], "suffix": []}
        for (gen, mtype, _), rows in groups.items():
            rows.sort(key=lambda r: (-r[1]["lvl"], r[0]))
            tiers = [{"tier": i + 1, "key": k, "ilvl": m["lvl"], "text": m["t"],
                      "weight": w, "reachable": m["lvl"] <= ilvl}
                     for i, (k, m, w) in enumerate(rows)]
            reachable = [t for t in tiers if t["reachable"]]
            out[gen].append({
                "type": mtype,
                "tiers": tiers,
                "best": reachable[0] if reachable else None,
                "weight": sum(t["weight"] for t in reachable),
            })
        for gen in out:
            out[gen].sort(key=lambda lad: -lad["weight"])
        return out

    def tier_of(self, key, tags, dom="item"):
        """(tier, ladder length) of a mod among what can roll on these
        tags in this domain, or (None, n) when the mod itself can't roll
        here (essence / league content)."""
        m = self.mods[key]
        gk = (m["gen"], m["type"], tuple(s[0] for s in m["stats"]))
        ladder = [(k2, m2) for k2, m2 in self.mods.items()
                  if (m2["gen"], m2["type"],
                      tuple(s[0] for s in m2["stats"])) == gk
                  and not m2["ess"] and m2["dom"] == dom
                  and self._weight(m2, tags) > 0]
        ladder.sort(key=lambda r: (-r[1]["lvl"], r[0]))
        for i, (k2, _) in enumerate(ladder):
            if k2 == key:
                return i + 1, len(ladder)
        return None, len(ladder)

    # --------------------------------------------------------------- match

    def _match_crafted(self, line, cls):
        """Bench row whose (first) template line fits a '(crafted)' mod
        line, or None. Crafted mods live in domain 'crafted', outside the
        rollable dataset — the bench table identifies them instead."""
        want = _norm(line)
        for b in self.bench:
            if b["kind"] != "mod" or (cls and cls not in b["classes"]):
                continue
            for tline in b["t"].split("\n"):
                if _norm(tline) == want and _values_fit(line, tline):
                    return b
        return None

    def match_item(self, parsed):
        """Identify a parsed item's (itemtext.parse) mod lines.

        Returns {base, cls, rows, counts, open, uncertain}:
          rows      one per mod line: {line, key, name, gen, tier,
                    tier_of, origin, ambiguous}; key None = unmatched
          counts    distinct matched affix mods per generation + unknown
                    lines (implicits/enchants are neither)
          open      open prefix/suffix slots for the item's rarity
          uncertain True when any affix line was unmatched or ambiguous —
                    treat the counts as an estimate, not a fact

        Uses parsed['mod_tags'] when present (itemtext ≥ this build):
        implicit/enchant/scourge/crucible/rune lines don't occupy affix
        slots; '(crafted)' lines are identified via the bench table and
        do; fractured mods count and are flagged by origin.
        """
        base_name = (self.find_base(parsed.get("base") or "")
                     or self.find_base(parsed.get("name") or ""))
        binfo = self.bases.get(base_name)
        tags = set(binfo["tags"]) if binfo else None
        dom = binfo.get("dom", "item") if binfo else "item"
        cls = binfo["cls"] if binfo else canon_class(
            parsed.get("item_class", ""))

        lines = list(parsed.get("mods", []))
        n = len(lines)
        line_tags = list(parsed.get("mod_tags") or [""] * n)
        line_tags += [""] * (n - len(line_tags))
        non_affix = {"implicit", "enchant", "scourge", "crucible", "rune",
                     "Hidden"}
        cands = []
        for i, ln in enumerate(lines):
            row = []
            if line_tags[i] not in non_affix and line_tags[i] != "crafted":
                for key, li, nl in self._line_index.get(_norm(ln), []):
                    m = self.mods[key]
                    if not _values_fit(ln, m["t"].split("\n")[li]):
                        continue
                    spawnable = (tags is not None and m["dom"] == dom
                                 and self._weight(m, tags) > 0)
                    row.append({"key": key, "li": li, "nl": nl, "m": m,
                                "spawnable": spawnable})
            cands.append(row)

        assigned = [None] * n
        # hybrid pass: consecutive lines covered by one multi-line mod
        for i in range(n):
            if assigned[i] is not None:
                continue
            for c in cands[i]:
                if c["nl"] < 2 or c["li"] != 0 or i + c["nl"] > n:
                    continue
                if all(assigned[i + j] is None
                       and any(c2["key"] == c["key"] and c2["li"] == j
                               for c2 in cands[i + j])
                       for j in range(c["nl"])):
                    for j in range(c["nl"]):
                        assigned[i + j] = c["key"]
                    break
        # singles pass: prefer rollable > essence > league-content mods
        def _rank(c):
            m = c["m"]
            klass = 0 if c["spawnable"] else (1 if m["ess"] else 2)
            w = self._weight(m, tags) if tags else 0
            return (klass, -w, c["key"])
        for i in range(n):
            if assigned[i] is None:
                singles = sorted((c for c in cands[i] if c["nl"] == 1),
                                 key=_rank)
                if singles:
                    assigned[i] = singles[0]["key"]

        rows = []
        uncertain = False
        for i, ln in enumerate(lines):
            tag = line_tags[i]
            if tag in non_affix:
                rows.append({"line": ln, "key": None, "name": "",
                             "gen": tag, "tier": None, "tier_of": 0,
                             "origin": tag, "ambiguous": False})
                continue
            if tag == "crafted":
                b = self._match_crafted(ln, cls)
                rows.append({"line": ln, "key": None, "name": "bench craft",
                             "gen": b["gen"] if b else "?", "tier": None,
                             "tier_of": 0, "origin": "bench",
                             "ambiguous": b is None})
                uncertain = uncertain or b is None
                continue
            key = assigned[i]
            if key is None:
                rows.append({"line": ln, "key": None, "name": "", "gen": "?",
                             "tier": None, "tier_of": 0, "origin": None,
                             "ambiguous": False})
                uncertain = True
                continue
            m = self.mods[key]
            others = {c["key"] for c in cands[i] if c["nl"] == 1} - {key}
            ambiguous = any(self.mods[k]["gen"] != m["gen"] for k in others)
            uncertain = uncertain or ambiguous
            if tags:
                tier, ladder_n = self.tier_of(key, tags, dom)
            else:
                tier, ladder_n = None, 0
            origin = ("essence" if m["ess"]
                      else "fractured" if tag == "fractured"
                      else "roll" if tags and m["dom"] == dom
                      and self._weight(m, tags) > 0
                      else "special")
            rows.append({"line": ln, "key": key, "name": m["n"],
                         "gen": m["gen"], "tier": tier, "tier_of": ladder_n,
                         "origin": origin, "ambiguous": ambiguous})

        def _count(gen):
            keys = {r["key"] for r in rows if r["key"] and r["gen"] == gen}
            benched = sum(1 for r in rows
                          if r["origin"] == "bench" and r["gen"] == gen)
            return len(keys) + benched
        counts = {"prefix": _count("prefix"), "suffix": _count("suffix"),
                  "unknown": sum(1 for r in rows if r["key"] is None
                                 and r["origin"] in (None, "bench")
                                 and r["gen"] == "?")}
        cap = _CAPS.get(parsed.get("rarity", ""), 0)
        open_slots = {"prefix": max(0, cap - counts["prefix"]),
                      "suffix": max(0, cap - counts["suffix"])}
        return {"base": base_name, "cls": cls, "rows": rows,
                "counts": counts, "open": open_slots, "uncertain": uncertain}

    # ---------------------------------------------------- essences / bench

    def essences_for(self, cls, ilvl):
        """Best usable essence per family for this class at this item
        level, sorted by family name. max_ilvl is the game's restriction:
        low-tier essences can't be applied to items above it."""
        best = {}
        for e in self.essences:
            text = e["mods"].get(cls)
            if not text:
                continue
            if e["max_ilvl"] is not None and ilvl > e["max_ilvl"]:
                continue
            family = e["name"].rsplit(" of ", 1)[-1]
            cur = best.get(family)
            if cur is None or e["tier"] > cur["tier"]:
                best[family] = {"name": e["name"], "tier": e["tier"],
                                "text": text}
        return [best[f] for f in sorted(best)]

    def bench_for(self, cls):
        """Bench craft rows for a class: highest tier per craft text shape
        for kind 'mod', plus all utility actions (sockets/links/colors)."""
        best = {}
        actions = []
        for b in self.bench:
            if cls not in b["classes"]:
                continue
            if b["kind"] != "mod":
                actions.append(b)
                continue
            shape = (b["gen"], _norm(b["t"]))
            cur = best.get(shape)
            if cur is None or b["tier"] > cur["tier"]:
                best[shape] = b
        rows = sorted(best.values(), key=lambda b: (b["gen"], b["t"]))
        return rows, actions
