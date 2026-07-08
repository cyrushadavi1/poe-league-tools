#!/usr/bin/env python3
"""Path of Building import-code tools.

  decode : print a build summary (class, level, tree specs, skill sets)
  plan   : write a leveling plan (markdown) + overlay gem notes (json)

Usage:
  python pob.py decode <code-or-file>
  python pob.py plan   <code-or-file> [--md plan.md] [--json build_notes.json]

PoB codes are URL-safe base64 of zlib-compressed XML. Paste the code
itself (PoB → Import/Export Build → Generate), not a pobb.in URL.

Guide PoBs usually ship multiple tree Specs ("Level 30", "Final") and
multiple Skill Sets ("Act 1-2", "Endgame") — this tool turns those into
an act-by-act sheet, and `--json` output can be fed to the overlay via
`build_notes` in config.json so gem links show up on the correct act.

A PoB with NO act-tagged skill sets (a bare endgame export) falls back
to the generic per-class plan in data/leveling_defaults.json — clearly
labelled, because it knows the class, not the build.
"""
import argparse
import base64
import json
import os
import re
import zlib
import xml.etree.ElementTree as ET

GENERIC_PLANS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "leveling_defaults.json")


def read_code(arg: str) -> str:
    try:
        with open(arg, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return arg.strip()


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


def skill_sets(root):
    skills = root.find("Skills")
    if skills is None:
        return []
    sets = skills.findall("SkillSet") or [skills]
    out = []
    for i, ss in enumerate(sets, 1):
        groups = []
        for sk in ss.findall("Skill"):
            gems = [g.get("nameSpec") or g.get("skillId") or "?"
                    for g in sk.findall("Gem")
                    if g.get("enabled", "true") != "false"]
            if gems:
                groups.append({"label": sk.get("label") or sk.get("slot") or "",
                               "gems": gems})
        out.append({"title": ss.get("title") or f"Skill set {i}", "groups": groups})
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
        for g in ss["groups"]:
            label = f"  [{g['label']}]" if g["label"] else ""
            lines.append(f"- {' – '.join(g['gems'])} ({len(g['gems'])}L){label}")
        lines.append("")
        text = " | ".join(" – ".join(g["gems"]) for g in ss["groups"])
        for act in acts_in_title(ss["title"]):
            # skill sets titled "Act N"/"Act N-M" become overlay notes
            # for EVERY act in the span
            notes.append({"act": act, "text": text})

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
        new = len(cur - prev)
        est_hi = len(cur) + 1                # zero quest points banked
        est_lo = max(2, len(cur) - 22 + 1)   # all ~22 quest points banked
        delta = f"+{new} vs previous, " if prev else ""
        lines.append(f"- **{sp['title']}** — {len(cur)} points "
                     f"({delta}reach ~lvl {est_lo}–{est_hi} depending on quest "
                     f"points; open this spec in PoB for the exact pathing)")
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
    except (ValueError, zlib.error, ET.ParseError) as e:
        hint = ("that looks like a URL — paste the export CODE instead"
                if read_code(a.code).lower().startswith(("http://", "https://"))
                else "paste the code from PoB → Import/Export Build → "
                     "Generate (or a file containing it)")
        raise SystemExit(f"could not decode PoB code: {e}\n  {hint}")
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
