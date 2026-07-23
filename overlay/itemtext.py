"""Parser for Path of Exile's Ctrl+C item text (clipboard) format.

Pure stdlib, no Qt, import-safe — the clipboard hook is wired by
integration elsewhere; this module only turns pasted text into a dict.

Format (live-verified against Awakened PoE Trade's parser + English
client strings, 2026-07-07):
  - sections split by lines of exactly eight dashes: "--------"
  - header block: "Item Class: X", "Rarity: Y", then 1-2 name lines
    (rare/unique: name + base; normal/magic: a single combined line,
    normal items may carry a "Superior " prefix)
  - property keys: "Sockets: ", "Item Level: ", "Stack Size: ",
    "Quality: ", "Requirements:" block with "Level: N"
  - mod lines may carry parenthetical tags: (implicit), (crafted),
    (enchant), (fractured), ...

parse(text) -> dict | None.  Returns None for non-item text; never raises
on garbage input.  parsed["mod_tags"] mirrors parsed["mods"] with each
line's parenthetical tag ("" for plain explicits).
"""
from __future__ import annotations

import re

SEPARATOR = "--------"  # exactly 8 dashes in the game's export

# "Relic" is the rarity string of foil (relic) uniques.
_RARITIES = {"Normal", "Magic", "Rare", "Unique", "Relic", "Gem", "Currency",
             "Divination Card", "Quest"}

# rarities whose post-header text is description, not mods — skip mod
# extraction entirely (verdicts never use it, and without an Item Level
# anchor every digit-bearing description line would leak into mods)
_NO_MODS_RARITIES = {"Gem", "Currency", "Divination Card", "Quest"}

# trailing tags the game appends to mod lines
_MOD_TAG_RE = re.compile(
    r"\s*\((implicit|crafted|enchant|fractured|scourge|crucible|rune"
    r"|Hidden)\)$")

# "Key: value" property lines (never mods)
_PROP_LINE_RE = re.compile(r"^[A-Za-z][A-Za-z' /()%-]*: \S")

# quality, incl. catalyst/alternate variants:
# "Quality: +20%", "Quality (Attribute Modifiers): +20% (augmented)"
_QUALITY_RE = re.compile(r"^Quality(?: \([^)]*\))?: \+(\d+)%")

# digitless lines that are still mods
_DIGITLESS_MOD_PREFIXES = (
    "Cannot ", "Culling ", "You ", "Gain ", "Grants ", "Has ", "Hits ",
    "Immune", "Immunity", "Unaffected", "Unwavering", "Instant ",
    "Corrupted Blood", "Veiled ", "Used when ", "Reused ", "Triggers ",
)

_LIFE_RE = re.compile(r"^\+(\d+) to maximum Life$")
_MS_RE = re.compile(r"^(\d+)% increased Movement Speed$")
_RES_RE = re.compile(
    r"^\+(\d+)% to (Fire|Cold|Lightning|Chaos) Resistance$")
_RES_DUAL_RE = re.compile(
    r"^\+(\d+)% to (Fire|Cold|Lightning|Chaos) and "
    r"(Fire|Cold|Lightning|Chaos) Resistances$")
_ALL_RES_RE = re.compile(r"^\+(\d+)% to all Elemental Resistances$")
_DAMAGE_RANGE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)")
_APS_RE = re.compile(r"^Attacks per Second: ([\d.]+)")


def _split_blocks(text: str) -> list[list[str]]:
    """Split item text into blocks of non-empty lines on separator lines."""
    blocks: list[list[str]] = []
    cur: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip()
        if line == SEPARATOR:
            if cur:
                blocks.append(cur)
            cur = []
        elif line:
            cur.append(line)
    if cur:
        blocks.append(cur)
    return blocks


def _kv(lines: list[str], key: str) -> str | None:
    """Value of the first 'key: value' line in lines, else None."""
    prefix = key + ": "
    for line in lines:
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return None


def _parse_sockets(value: str) -> tuple[int, int]:
    """'B-B-R G' -> (total sockets, size of largest linked group).

    Letters: R/G/B/W (white) and A (abyssal).  Abyssal sockets are counted
    like any other; the link-group nuance around them doesn't matter for
    leveling verdicts.
    """
    total = 0
    links = 0
    for group in value.split():
        socks = [s for s in group.split("-") if s]
        total += len(socks)
        links = max(links, len(socks))
    return total, links


def _socket_details(value: str) -> tuple[dict[str, int], list[str]]:
    """Return colour counts and linked groups from a Sockets property."""
    colors = {"R": 0, "G": 0, "B": 0, "W": 0, "A": 0}
    groups = []
    for raw_group in value.split():
        socks = [s for s in raw_group.split("-") if s]
        if not socks:
            continue
        groups.append("".join(socks))
        for socket in socks:
            if socket in colors:
                colors[socket] += 1
    return colors, groups


def _average_damage(value: str) -> float:
    """Sum average damage for one or more ``min-max`` ranges."""
    return sum((float(lo) + float(hi)) / 2
               for lo, hi in _DAMAGE_RANGE_RE.findall(value))


def _clean_mod(line: str) -> str:
    return _MOD_TAG_RE.sub("", line).strip()


def _is_mod_line(line: str) -> bool:
    if not line or line.endswith(":"):
        return False
    if line.startswith(("{", "(")):        # advanced-format info lines
        return False
    if line.startswith(('"', "“")):   # unique flavour text
        return False
    if _PROP_LINE_RE.match(line):          # "Quality: ...", "Level: ..." etc.
        return False
    if line in ("Corrupted", "Mirrored", "Split", "Unidentified",
                "Searing Exarch Item", "Eater of Worlds Item",
                "Shaper Item", "Elder Item", "Synthesised Item"):
        return False
    stripped = _clean_mod(line)
    # sentences of help/flavour text have no digits and none of the known
    # digitless mod stems ("Right click this item...", "Place into...")
    if any(ch.isdigit() for ch in stripped) or stripped.startswith("+"):
        return True
    return stripped.startswith(_DIGITLESS_MOD_PREFIXES)


def _derive_props(mods: list[str]) -> dict:
    life = 0
    movespeed = 0
    res = {"fire": 0, "cold": 0, "lightning": 0, "chaos": 0}
    for mod in mods:
        m = _LIFE_RE.match(mod)
        if m:
            life += int(m.group(1))
            continue
        m = _MS_RE.match(mod)
        if m:
            movespeed += int(m.group(1))
            continue
        m = _RES_RE.match(mod)
        if m:
            res[m.group(2).lower()] += int(m.group(1))
            continue
        m = _RES_DUAL_RE.match(mod)
        if m:
            res[m.group(2).lower()] += int(m.group(1))
            res[m.group(3).lower()] += int(m.group(1))
            continue
        m = _ALL_RES_RE.match(mod)
        if m:
            for k in ("fire", "cold", "lightning"):
                res[k] += int(m.group(1))
    return {"life": life, "movespeed": movespeed, "res": res}


def parse(text) -> dict | None:
    """Parse Ctrl+C item text into a dict, or None if it isn't an item."""
    if not isinstance(text, str) or not text.strip():
        return None
    blocks = _split_blocks(text)
    if not blocks:
        return None

    head = blocks[0]
    rarity = _kv(head, "Rarity")
    if rarity is None or rarity not in _RARITIES:
        return None
    item_class = _kv(head, "Item Class") or ""
    # VERIFY: 'Item Class:' header line exists since patch 3.14; exports
    # from older clients would lack it — we tolerate its absence.

    # name lines = everything in the header after the Rarity line
    ridx = next(i for i, ln in enumerate(head) if ln.startswith("Rarity: "))
    name_lines = head[ridx + 1:]
    if not name_lines:
        return None
    name = name_lines[0]
    base = name_lines[1] if len(name_lines) > 1 else name
    if len(name_lines) == 1 and name.startswith("Superior "):
        base = name[len("Superior "):]

    parsed = {
        "item_class": item_class,
        "rarity": rarity,
        "name": name,
        "base": base,
        "ilvl": 0,
        "req_level": 0,
        "quality": 0,
        "sockets": 0,
        "links": 0,
        "socket_colors": {"R": 0, "G": 0, "B": 0, "W": 0, "A": 0},
        "link_groups": [],
        "weapon_dps": {"physical": 0.0, "elemental": 0.0, "total": 0.0},
        "stack_size": None,
        "corrupted": False,
        "mods": [],
        # parallel to mods: the parenthetical tag of each line ("implicit",
        # "crafted", "fractured", ...; "" = plain explicit). Added for the
        # crafting copilot; consumers that only read mods are unaffected.
        "mod_tags": [],
    }

    anchor = 0  # mods live in blocks after the Item Level block
    physical_average = 0.0
    elemental_average = 0.0
    attacks_per_second = 0.0
    for i, block in enumerate(blocks[1:], start=1):
        first = block[0]
        if first == "Requirements:":
            lvl = _kv(block, "Level")
            if lvl:
                m = re.match(r"(\d+)", lvl)
                if m:
                    parsed["req_level"] = int(m.group(1))
            if not anchor:
                anchor = i
            continue
        for line in block:
            if line.startswith("Sockets: "):
                socket_text = line[len("Sockets: "):]
                total, links = _parse_sockets(socket_text)
                parsed["sockets"], parsed["links"] = total, links
                colors, groups = _socket_details(socket_text)
                parsed["socket_colors"] = colors
                parsed["link_groups"] = groups
            elif line.startswith("Physical Damage: "):
                physical_average = _average_damage(
                    line[len("Physical Damage: "):])
            elif line.startswith("Elemental Damage: "):
                elemental_average = _average_damage(
                    line[len("Elemental Damage: "):])
            elif (m := _APS_RE.match(line)):
                attacks_per_second = float(m.group(1))
            elif line.startswith("Item Level: "):
                m = re.match(r"(\d+)", line[len("Item Level: "):])
                if m:
                    parsed["ilvl"] = int(m.group(1))
                anchor = i
            elif line.startswith("Stack Size: "):
                parsed["stack_size"] = line[len("Stack Size: "):].split()[0]
            else:
                m = _QUALITY_RE.match(line)   # incl. catalyst variants
                if m:
                    parsed["quality"] = int(m.group(1))
                elif line == "Corrupted":
                    parsed["corrupted"] = True

    if rarity not in _NO_MODS_RARITIES:
        for block in blocks[anchor + 1:]:
            for line in block:
                if _is_mod_line(line):
                    tag = _MOD_TAG_RE.search(line)
                    parsed["mods"].append(_clean_mod(line))
                    parsed["mod_tags"].append(tag.group(1) if tag else "")

    parsed["props"] = _derive_props(parsed["mods"])
    if attacks_per_second:
        pdps = physical_average * attacks_per_second
        edps = elemental_average * attacks_per_second
        parsed["weapon_dps"] = {
            "physical": round(pdps, 1),
            "elemental": round(edps, 1),
            "total": round(pdps + edps, 1),
        }
    return parsed
