"""Curated high-ticket crafting guides.

The guide catalog is authored data. This module only validates, filters, and
renders it; it never invents odds, fetches prices, crafts, trades, or touches
the game client.

Usage:
    python -m craft.guides list
    python -m craft.guides show focused_plus4_amulet
    python -m craft.guides bundle --out high_end_crafting.md
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PATH = os.path.join(_ROOT, "data", "high_end_crafts.json")

REQUIRED_GUIDE_FIELDS = {
    "id", "name", "market", "risk", "capital", "roi_thesis",
    "base_searches", "requirements", "steps", "selloffs", "stop_loss",
}


def load_catalog(path: str | None = None) -> dict:
    with open(path or DEFAULT_PATH, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not isinstance(data.get("guides"), list):
        raise ValueError("high-end craft catalog must contain a guides list")
    seen = set()
    for guide in data["guides"]:
        missing = REQUIRED_GUIDE_FIELDS - set(guide)
        if missing:
            raise ValueError(f"{guide.get('id', '?')}: missing "
                             + ", ".join(sorted(missing)))
        if guide["id"] in seen:
            raise ValueError(f"duplicate guide id: {guide['id']}")
        seen.add(guide["id"])
        if not guide["steps"]:
            raise ValueError(f"{guide['id']}: no crafting steps")
        if [s.get("n") for s in guide["steps"]] != \
                list(range(1, len(guide["steps"]) + 1)):
            raise ValueError(f"{guide['id']}: steps must be numbered 1..N")
    return data


def get_guide(catalog: dict, guide_id: str) -> dict:
    for guide in catalog["guides"]:
        if guide["id"] == guide_id:
            return guide
    raise KeyError(guide_id)


def render_guide(guide: dict, rules: list[str] | None = None) -> str:
    lines = [
        f"# {guide['name']}",
        "",
        f"Market: **{guide['market']}** · risk: **{guide['risk']}**",
        "",
        f"Capital: {guide['capital']}",
        "",
        "## ROI thesis",
        "",
        guide["roi_thesis"],
        "",
        "## Requirements",
        "",
    ]
    lines.extend(f"- {row}" for row in guide["requirements"])
    lines.extend(["", "## Craft", ""])
    for step in guide["steps"]:
        lines.extend([
            f"{step['n']}. **{step['action']}**",
            f"   - Checkpoint: {step['checkpoint']}",
        ])
    lines.extend(["", "## Sellable outcomes", ""])
    lines.extend(f"- {row}" for row in guide["selloffs"])
    lines.extend([
        "",
        "## Stop-loss",
        "",
        guide["stop_loss"],
        "",
        "## Matching base searches",
        "",
    ])
    lines.extend(f"- `{search_id}`" for search_id in guide["base_searches"])
    if rules:
        lines.extend(["", "## Portfolio rules", ""])
        lines.extend(f"- {rule}" for rule in rules)
    return "\n".join(lines).rstrip() + "\n"


def render_bundle(catalog: dict) -> str:
    lines = [
        "# High-ticket crafting playbook",
        "",
        f"Curated from: {catalog.get('source', 'authored source')}.",
        "",
        "## Contents",
        "",
    ]
    for guide in catalog["guides"]:
        lines.append(f"- [{guide['name']}](#{guide['id'].replace('_', '-')})")
    lines.append("")
    for guide in catalog["guides"]:
        lines.append(f"<a id=\"{guide['id'].replace('_', '-')}\"></a>")
        lines.append(render_guide(guide, catalog.get("rules")).rstrip())
        lines.extend(["", "---", ""])
    return "\n".join(lines).rstrip("-\n ") + "\n"


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="craft.guides",
        description="Render curated high-ticket PoE crafting guides.")
    p.add_argument("--catalog", default=DEFAULT_PATH)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="list available guide ids")
    show = sub.add_parser("show", help="render one guide")
    show.add_argument("guide_id")
    bundle = sub.add_parser("bundle", help="render every guide")
    bundle.add_argument("--out", default=None)
    return p


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        catalog = load_catalog(args.catalog)
        if args.command == "list":
            for guide in catalog["guides"]:
                print(f"{guide['id']:<32} {guide['name']} "
                      f"[{guide['risk']}]")
            return 0
        if args.command == "show":
            try:
                guide = get_guide(catalog, args.guide_id)
            except KeyError:
                print(f"unknown guide: {args.guide_id}", file=sys.stderr)
                return 1
            print(render_guide(guide, catalog.get("rules")), end="")
            return 0
        output = render_bundle(catalog)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(output)
            print(f"wrote crafting guide bundle to {args.out}")
        else:
            print(output, end="")
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"craft guide error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
