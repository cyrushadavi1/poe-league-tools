#!/usr/bin/env python3
"""Build the compact passive-tree catalog used by the leveling planner.

Path of Building ships the authoritative tree graph as Lua.  The desktop
tool does not need its sprites or most calculation metadata, so this script
extracts only node names, connections, node kinds, and mastery choices.

Usage:
    python tools/update_passive_tree.py [--version 3_29]

The generated JSON is deterministic and is committed so installed/offline
copies can produce passive instructions without Path of Building installed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import urllib.request


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_VERSION = "3_29"
RAW_URL = (
    "https://raw.githubusercontent.com/PathOfBuildingCommunity/"
    "PathOfBuilding/dev/src/TreeData/{version}/tree.lua"
)

_NODE_START = re.compile(r"^        \[(\d+)\]= \{$", re.M)
_LUA_STRING = re.compile(r'"((?:\\.|[^"\\])*)"')


def _strings(text: str) -> list[str]:
    return [
        bytes(value, "utf-8").decode("unicode_escape")
        for value in _LUA_STRING.findall(text)
    ]


def _scalar(block: str, key: str) -> str | None:
    match = re.search(
        rf'\["{re.escape(key)}"\]= ("(?:\\.|[^"\\])*"|-?\d+|true|false)',
        block,
    )
    if not match:
        return None
    value = match.group(1)
    if value.startswith('"'):
        return _strings(value)[0]
    return value


def _table(block: str, key: str) -> str:
    match = re.search(rf'\["{re.escape(key)}"\]= \{{', block)
    if not match:
        return ""
    opening = match.end() - 1
    depth = 0
    quoted = False
    escaped = False
    for index in range(opening, len(block)):
        char = block[index]
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return block[opening + 1:index]
    return ""


def _masteries(block: str) -> dict[str, str]:
    section = _table(block, "masteryEffects")
    if not section:
        return {}
    starts = list(re.finditer(r'\["effect"\]= (\d+),', section))
    out = {}
    for index, match in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(section)
        choice = section[match.end():end]
        stats = _strings(_table(choice, "stats"))
        if stats:
            out[match.group(1)] = "; ".join(stats)
    return out


def parse_tree_lua(text: str, version: str) -> dict:
    starts = list(_NODE_START.finditer(text))
    nodes = {}
    for index, match in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(text)
        block = text[match.start():end]
        node_id = match.group(1)
        # Numeric tables elsewhere in tree.lua share this indentation.
        if _scalar(block, "skill") != node_id:
            continue
        name = _scalar(block, "name") or f"Passive {node_id}"
        neighbours = set(_strings(_table(block, "out")))
        neighbours.update(_strings(_table(block, "in")))
        row = {
            "name": name,
            "connections": sorted(
                (value for value in neighbours if value.isdigit()),
                key=int,
            ),
        }
        flags = []
        for key, short in (
            ("isNotable", "notable"),
            ("isMastery", "mastery"),
            ("isJewelSocket", "jewel"),
            ("isAscendancyStart", "ascendancy_start"),
        ):
            if _scalar(block, key) == "true":
                flags.append(short)
        if flags:
            row["flags"] = flags
        class_start = _scalar(block, "classStartIndex")
        if class_start is not None:
            row["class_start"] = int(class_start)
        ascendancy = _scalar(block, "ascendancyName")
        if ascendancy:
            row["ascendancy"] = ascendancy
        grants = _scalar(block, "grantedPassivePoints")
        if grants is not None:
            row["grants"] = int(grants)
        masteries = _masteries(block)
        if masteries:
            row["masteries"] = masteries
        nodes[node_id] = row
    return {
        "version": version,
        "source": RAW_URL.format(version=version),
        "nodes": dict(sorted(nodes.items(), key=lambda item: int(item[0]))),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=DEFAULT_VERSION)
    parser.add_argument("--input", help="existing tree.lua instead of downloading")
    parser.add_argument("--out")
    args = parser.parse_args(argv)

    if args.input:
        with open(args.input, encoding="utf-8") as handle:
            text = handle.read()
    else:
        url = RAW_URL.format(version=args.version)
        with urllib.request.urlopen(url, timeout=60) as response:
            text = response.read().decode("utf-8")

    catalog = parse_tree_lua(text, args.version)
    out = args.out or os.path.join(
        ROOT, "data", f"passive_tree_{args.version}.json")
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(catalog, handle, ensure_ascii=False, separators=(",", ":"))
        handle.write("\n")
    print(f"wrote {out}: {len(catalog['nodes'])} nodes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
