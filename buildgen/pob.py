#!/usr/bin/env python3
"""Path of Building import-code tools.

  decode : print a build summary (class, level, tree specs, skill sets)
  plan   : write a leveling plan (markdown) + overlay gem notes (json)

Usage:
  python pob.py decode <code, file, or link>
  python pob.py plan   <code, file, or link> [--md plan.md] [--json build_notes.json]

PoB codes are URL-safe base64 of zlib-compressed XML. Paste the code
itself (PoB → Import/Export Build → Generate) or a build link —
pobb.in, pastebin, poe.ninja, maxroll, rentry, and poedb links (even
wrapped in a YouTube redirect) are fetched automatically (sources.py).

Guide PoBs usually ship multiple tree Specs ("Level 30", "Final") and
multiple Skill Sets ("Act 1-2", "Endgame") — this tool turns those into
an act-by-act sheet, and `--json` output can be fed to the overlay via
`build_notes` in config.json so gem links show up on the correct act.

A PoB with no act-tagged skill sets may match a reviewed structural
adapter in data/pob_leveling_adapters.json.  An unknown bare endgame
export falls back to data/leveling_defaults.json — clearly labelled,
because that fallback knows the class, not the build.
"""
import argparse
import base64
import json
import os
import re
import zlib
import xml.etree.ElementTree as ET

import adapters
import sources

GENERIC_PLANS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "leveling_defaults.json")

# what decode() raises on a bad code — callers (party.py) catch these
# together with sources.SourceError to report which member's paste broke
DECODE_ERRORS = (ValueError, zlib.error, ET.ParseError)


def read_code(arg: str, fetch=None) -> str:
    """Code, path to a file holding one, or build link -> the code.

    A file may itself contain a link (friends drop whatever they have
    into a .txt); links resolve through sources.resolve, which raises
    SourceError on unknown hosts or fetch failures.
    """
    try:
        with open(arg, encoding="utf-8") as f:
            arg = f.read()
    except OSError:
        pass
    return sources.resolve(arg.strip(), fetch=fetch)


def decode(code: str) -> ET.Element:
    code = code.strip()
    pad = "=" * (-len(code) % 4)
    raw = base64.urlsafe_b64decode(code + pad)
    return ET.fromstring(zlib.decompress(raw))


def encode(root: ET.Element) -> str:
    return base64.urlsafe_b64encode(zlib.compress(ET.tostring(root), 9)).decode()


# ------------------------------------------------------------- parsing
def _int_or(value, default):
    """int(value), or `default` for None/empty/non-numeric (hand-edited
    or corrupt-but-well-formed PoB XML must not crash the CLI)."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def build_info(root):
    b = root.find("Build")
    return {
        "class": (b.get("className") if b is not None else None) or "?",
        "ascendancy": (b.get("ascendClassName") if b is not None else None) or "",
        "level": _int_or(b.get("level"), 1) if b is not None else 1,
    }


def tree_specs(root):
    out = []
    tree = root.find("Tree")
    if tree is None:
        return out
    for spec in tree.findall("Spec"):
        nodes = [n for n in (spec.get("nodes") or "").split(",") if n]
        out.append({"title": spec.get("title") or "Default", "nodes": nodes})
    return out


LEVEL_RANGE_RE = re.compile(
    r"\b(?:levels?|leveling|lvl)\s*(\d+)\s*(?:[-–]\s*(\d+)|(\+))",
    re.I)


def level_range_in_title(title):
    """Return ``(first, last)`` from common PoB stage labels.

    The last match wins so ``Leveling 2 (Static Strike-Lvl 13-28)``
    resolves to 13-28 rather than the stage number. ``last`` is ``None``
    for open-ended labels such as ``Lvl 38+``.
    """
    matches = list(LEVEL_RANGE_RE.finditer(title or ""))
    if not matches:
        return None
    m = matches[-1]
    lo = int(m.group(1))
    hi = int(m.group(2)) if m.group(2) else None
    if hi is not None and hi < lo:
        lo, hi = hi, lo
    return lo, hi


def skill_sets(root):
    skills = root.find("Skills")
    if skills is None:
        return []
    sets = skills.findall("SkillSet") or [skills]
    out = []
    for i, ss in enumerate(sets, 1):
        groups = []
        stages = []
        current_stage = None
        for sk in ss.findall("Skill"):
            if sk.get("enabled", "true") == "false":
                continue
            gems = [g.get("nameSpec") or g.get("skillId") or "?"
                    for g in sk.findall("Gem")
                    if g.get("enabled", "true") != "false"]
            label = sk.get("label") or sk.get("slot") or ""
            level_range = level_range_in_title(label)
            if not gems and level_range:
                current_stage = {
                    "title": label.strip("- \t") or label,
                    "level_min": level_range[0],
                    "level_max": level_range[1],
                    "groups": [],
                }
                stages.append(current_stage)
                continue
            if gems:
                group = {"label": label, "gems": gems}
                groups.append(group)
                if current_stage is not None:
                    current_stage["groups"].append(group)
        out.append({"title": ss.get("title") or f"Skill set {i}",
                    "groups": groups, "stages": stages})
    return out


def extract_items(root):
    """Parse Items/Item text blocks -> [{'name', 'base', 'rarity'}].

    PoB stores each equipped/stashed item as the text of an <Item> element:
    the first line is usually 'Rarity: X' (NORMAL/MAGIC/RARE/UNIQUE/RELIC),
    then the item name, then — for RARE/UNIQUE/RELIC — the base type on the
    next line. MAGIC/NORMAL items carry the base inside the name line, so
    'base' is None for them. If there is no rarity line, the first line is
    taken as the name. Metadata lines (containing ':') never become 'base'.
    VERIFY: shape assumed from PoB's documented export format; re-check
    against a real PoB export before league start.
    """
    out = []
    items = root.find("Items")
    if items is None:
        return out
    for item in items.findall("Item"):
        # itertext(): older PoB versions nest <ModRange> children inside
        # <Item>, splitting the text — collect all fragments.
        lines = [ln.strip() for ln in "".join(item.itertext()).splitlines()]
        lines = [ln for ln in lines if ln]
        if not lines:
            continue
        rarity = None
        if lines[0].lower().startswith("rarity:"):
            rarity = lines[0].split(":", 1)[1].strip().upper() or None
            lines = lines[1:]
        if not lines:
            continue
        name = lines[0]
        base = None
        if (rarity in (None, "RARE", "UNIQUE", "RELIC")
                and len(lines) > 1 and ":" not in lines[1]):
            base = lines[1]
        out.append({"name": name, "base": base, "rarity": rarity})
    return out


# VERIFY: keystone name list authored from game knowledge (3.26-era tree),
# not live-verified — refresh from poewiki once 3.29 tree data is out.
KEYSTONES = (
    "Acrobatics", "Ancestral Bond", "Arrow Dancing", "Avatar of Fire",
    "Blood Magic", "Call to Arms", "Chaos Inoculation", "Conduit",
    "Crimson Dance", "Divine Shield", "Doomsday", "Eldritch Battery",
    "Elemental Equilibrium", "Elemental Overload", "Eternal Youth",
    "Ghost Dance", "Ghost Reaver", "Glancing Blows", "Hex Master",
    "Hollow Palm Technique", "Imbalanced Guard", "Iron Grip",
    "Iron Reflexes", "Iron Will", "Lethe Shade", "Magebane",
    "Mind Over Matter", "Minion Instability", "Pain Attunement",
    "Perfect Agony", "Point Blank", "Precise Technique",
    "Resolute Technique", "Runebinder", "Solipsism", "Supreme Ego",
    "The Agnostic", "The Impaler", "Unwavering Stance", "Vaal Pact",
    "Versatile Combatant", "Wicked Ward", "Wind Dancer", "Zealot's Oath",
)


def extract_keystones(root):
    """Best-effort keystone names -> sorted [str]. May well be empty.

    LIMITATION: keystones allocated on the tree live in Tree/Spec 'nodes'
    as numeric ids; mapping ids to names requires the full passive-tree
    data file, which this stdlib-only tool does not ship. Instead we scan
    Config <Input> attributes, the Notes block, and Items text for known
    keystone names (uniques granting keystones, guide notes, custom mods).
    An empty result does NOT mean the build takes no keystones — treat it
    as 'unknown', not 'none'. The uniques list from extract_items() is the
    reliable part.
    """
    texts = []
    notes = root.find("Notes")
    if notes is not None and notes.text:
        texts.append(notes.text)
    config = root.find("Config")
    if config is not None:
        for inp in config.findall("Input"):
            texts.extend(v for v in inp.attrib.values() if v)
    items = root.find("Items")
    if items is not None:
        for item in items.findall("Item"):
            texts.append("".join(item.itertext()))
    blob = "\n".join(texts).lower()
    return sorted(ks for ks in KEYSTONES if ks.lower() in blob)


# ---------------------------------------------------------- plan output
# 'Act 3', 'Act 3+4', 'Act 6-10' (and the short form 'A3' / 'A6-10')
ACT_RE = re.compile(r"\bact\s*(\d+)(?:\s*[-–+]\s*(\d+))?", re.I)
ACT_SHORT_RE = re.compile(r"\bA(\d+)(?:\s*[-–+]\s*A?(\d+))?\b")


def acts_in_title(title):
    """Act numbers a skill-set title covers: 'Act 3+4' -> [3, 4],
    'Act 6-10' -> [6..10], 'A3 setup' -> [3], no act -> []."""
    m = ACT_RE.search(title) or ACT_SHORT_RE.search(title)
    if not m:
        return []
    lo = int(m.group(1))
    hi = int(m.group(2)) if m.group(2) else lo
    if hi < lo:
        lo, hi = hi, lo
    return [a for a in range(lo, hi + 1) if 1 <= a <= 10]


def load_generic_plan(class_name, path=GENERIC_PLANS):
    """Generic per-class leveling notes, or [] when the class (or the
    data file) is unknown. Entries carry "source": "generic" so
    downstream tools (doctor, plan.md) can say where they came from;
    the overlay ignores extra keys."""
    try:
        with open(path, encoding="utf-8") as f:
            plans = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    acts = plans.get(class_name)
    if not isinstance(acts, dict):
        return []
    return [{"act": int(a), "text": t, "source": "generic"}
            for a, t in sorted(acts.items(), key=lambda kv: int(kv[0]))
            if str(a).isdigit() and 1 <= int(a) <= 10]


def _table_text(value):
    return str(value).replace("|", "/").replace("\n", " ")


def _render_adapter_guides(lines, adapter):
    checklist = adapter.get("gem_checklist", [])
    if checklist:
        lines += ["## Exact gem acquisition checklist", "",
                  "| Act | Level | Action | When | Exact instruction |",
                  "|---:|---:|---|---|---|"]
        for row in checklist:
            instruction = row.get("instruction") or ", ".join(
                row.get("items", []))
            lines.append(
                f"| {row.get('act', '—')} | {row.get('level', '—')} | "
                f"**{_table_text(row.get('action', 'OBTAIN'))}** | "
                f"{_table_text(row.get('when', ''))} | "
                f"{_table_text(instruction)} |")
        lines.append("")

    guide = adapter.get("item_guide") or {}
    if guide:
        summary = guide.get(
            "summary", "Build-specific campaign pickup rules.")
        lines += ["## Item pickup guide", "",
                  f"*{summary} The overlay uses this same profile when you "
                  "Ctrl+C an item.*", ""]
        for title, key in (("Always pick up", "always_pick"),
                           ("Pick up and compare", "check"),
                           ("Usually leave behind", "skip")):
            rows = guide.get(key, [])
            if not rows:
                continue
            lines.append(f"### {title}")
            lines += [f"- {text}" for text in rows]
            lines.append("")


def make_plan(root):
    info = build_info(root)
    specs = tree_specs(root)
    sets = skill_sets(root)

    lines = [f"# Leveling plan — {info['class']}"
             + (f" ({info['ascendancy']})" if info["ascendancy"] else ""),
             "", "## Gem setup by stage", ""]
    notes = []
    for ss in sets:
        lines.append(f"### {ss['title']}")
        if ss.get("stages"):
            for stage in ss["stages"]:
                if stage["level_max"] is None:
                    heading = f"Level {stage['level_min']}+"
                else:
                    heading = (f"Levels {stage['level_min']}–"
                               f"{stage['level_max']}")
                lines.append(f"#### {heading}")
                for g in stage["groups"]:
                    label = f"  [{g['label']}]" if g["label"] else ""
                    lines.append(
                        f"- {' – '.join(g['gems'])} ({len(g['gems'])}L)"
                        f"{label}")
        else:
            for g in ss["groups"]:
                label = f"  [{g['label']}]" if g["label"] else ""
                lines.append(
                    f"- {' – '.join(g['gems'])} ({len(g['gems'])}L){label}")
        lines.append("")
        text = " | ".join(" – ".join(g["gems"]) for g in ss["groups"])
        for act in acts_in_title(ss["title"]):
            # skill sets titled "Act N"/"Act N-M" become overlay notes
            # for EVERY act in the span
            notes.append({"act": act, "text": text})

    adapter = adapters.match_adapter(root, info, specs, sets)
    if adapter and not notes:
        notes = adapters.campaign_notes(adapter)
        lines += [f"## Build-specific campaign milestones — "
                  f"{adapter['label']}", "",
                  f"*Matched adapter: `{adapter['id']}`. Reminders with "
                  "levels change automatically in the overlay.*", ""]
        for warning in adapter.get("warnings", []):
            lines.append(f"- **Caution:** {warning}")
        if adapter.get("warnings"):
            lines.append("")
        _render_adapter_guides(lines, adapter)
        lines += ["## Level-aware campaign reminders", ""]
        for n in notes:
            level = f", level {n['level']}" if "level" in n else ""
            lines.append(f"- **Act {n['act']}{level}:** {n['text']}")
        if adapter.get("sources"):
            lines += ["", "Sources:"]
            lines += [f"- {url}" for url in adapter["sources"]]
        lines.append("")

    if not notes:
        notes = load_generic_plan(info["class"])
        if notes:
            lines += [f"## Generic {info['class']} leveling gems", "",
                      "*(This PoB has no act-tagged skill sets, so these "
                      "are class defaults, not build-specific. Title PoB "
                      'skill sets "Act 1 ...", "Act 3-5 ..." and re-run '
                      "for build-specific notes.)*", ""]
            lines += [f"- **Act {n['act']}**: {n['text']}" for n in notes]
            lines.append("")

    lines += ["## Passive tree checkpoints", ""]
    prev = set()
    for sp in specs:
        cur = set(sp["nodes"])
        if prev and prev.issubset(cur):
            delta = f"+{len(cur - prev)} vs previous, "
        elif prev:
            delta = "alternate/reworked tree, "
        else:
            delta = ""
        intended = level_range_in_title(sp["title"])
        if intended:
            lo, hi = intended
            level_text = (f"PoB stage levels {lo}–{hi}"
                          if hi is not None else f"PoB stage level {lo}+")
        else:
            # Cluster/mastery nodes and alternate specs make node-count
            # level estimates actively misleading.  Only print a level
            # when the guide author encoded one in the title.
            level_text = "no level range encoded in the stage title"
        lines.append(f"- **{sp['title']}** — {len(cur)} points "
                     f"({delta}{level_text}; open this spec in PoB for "
                     "the exact pathing)")
        prev = cur
    lines.append("")
    return "\n".join(lines), notes


def cmd_decode(root):
    info = build_info(root)
    asc = info["ascendancy"] or "no ascendancy"
    print(f"{info['class']} ({asc}), level {info['level']}")
    print("Tree specs:")
    for sp in tree_specs(root):
        print(f"  - {sp['title']}: {len(sp['nodes'])} nodes")
    print("Skill sets:")
    for ss in skill_sets(root):
        print(f"  - {ss['title']}: {len(ss['groups'])} socket groups")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("decode")
    d.add_argument("code")
    p = sub.add_parser("plan")
    p.add_argument("code")
    p.add_argument("--md", default="plan.md")
    p.add_argument("--json", default="build_notes.json")
    a = ap.parse_args()

    try:
        # binascii.Error (bad base64) is a ValueError subclass
        root = decode(read_code(a.code))
    except sources.SourceError as e:
        raise SystemExit(str(e))
    except DECODE_ERRORS as e:
        raise SystemExit(f"could not decode PoB code: {e}\n"
                         "  paste the code from PoB → Import/Export Build → "
                         "Generate, a build link (pobb.in etc.), or a file "
                         "containing either")
    if a.cmd == "decode":
        cmd_decode(root)
    else:
        md, notes = make_plan(root)
        with open(a.md, "w", encoding="utf-8") as f:
            f.write(md)
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(notes, f, indent=2)
        print(f"wrote {a.md} and {a.json}")


if __name__ == "__main__":
    main()
