#!/usr/bin/env python3
"""Crafting copilot CLI (addendum 5G, task 29).

Usage:
    python tools/craft_check.py item.txt --level 34 --goal "ms boots"
    Get-Clipboard | python tools/craft_check.py -          (PC, after Ctrl+C)
    pbpaste | python tools/craft_check.py -                (Mac, dev)

Reads one Ctrl+C item text from a file or stdin ('-' or no argument),
prints the deterministic crafting digest (identified mods with tiers, open
affixes, rollable pool, usable essences, bench options, applicable
recipes), then the LLM plan when available. --no-llm forces the degraded
digest-only mode; --json dumps the full result for piping.

Requires data/repoe_craft.json (run tools/refresh_repoe.py once).
Read-only everywhere: the player copies the item with the game's own
Ctrl+C; nothing here touches the game. Stdlib only, import-safe.
"""
from __future__ import annotations

import argparse
import json
import os
import sys


def main(argv=None):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for p in (root, os.path.join(root, "overlay")):
        if p not in sys.path:
            sys.path.insert(0, p)
    import itemtext
    from craft import copilot
    from craft.pool import CraftData

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("file", nargs="?", default="-",
                    help="item text file, or '-' for stdin (default)")
    ap.add_argument("--level", type=int, default=None,
                    help="character level (filters recipes)")
    ap.add_argument("--goal", default=None,
                    help="what you want out of this item")
    ap.add_argument("--budget", default=None,
                    help="rough currency budget, e.g. '20 alts, 2 essences'")
    ap.add_argument("--no-llm", action="store_true",
                    help="deterministic digest only")
    ap.add_argument("--data", default=None,
                    help="path to repoe_craft.json (default: data/)")
    ap.add_argument("--json", action="store_true", dest="as_json",
                    help="dump the full result as JSON")
    args = ap.parse_args(argv)

    if args.file == "-":
        text = sys.stdin.read()
    else:
        with open(args.file, encoding="utf-8", errors="ignore") as f:
            text = f.read()

    parsed = itemtext.parse(text)
    if parsed is None:
        print("input does not look like Ctrl+C item text", file=sys.stderr)
        return 2
    if parsed.get("rarity") in ("Gem", "Currency", "Divination Card",
                                "Quest", "Unique", "Relic"):
        print(f"{parsed['name']}: {parsed['rarity']} items aren't a "
              "crafting target", file=sys.stderr)
        return 2

    try:
        data = CraftData.load(args.data)
    except OSError as exc:
        print(f"crafting dataset missing ({exc}); "
              "run: python tools/refresh_repoe.py", file=sys.stderr)
        return 1

    llm_factory = None
    if args.no_llm:
        def llm_factory():  # forces the documented degrade path
            from llm.client import LLMDisabled
            raise LLMDisabled("--no-llm")

    ctx = {"level": args.level, "goal": args.goal, "budget": args.budget}
    result = copilot.advise(parsed, ctx, data=data, llm_factory=llm_factory)
    if args.as_json:
        out = dict(result)
        print(json.dumps(out, indent=1, ensure_ascii=False))
    else:
        print(result["text"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
