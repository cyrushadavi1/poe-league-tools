"""Generate bankroll-sized official trade queries for premium craft bases.

The authored templates use exact official stat ids, including the
``fractured.*`` group, so the target modifier itself must be fractured.
Price ceilings are the smaller of an authored hard cap and a fraction of
the user's declared bankroll.

Usage:
    python -m market.base_filters --bankroll-div 100
    python -m market.base_filters --bankroll-div 100 --out-dir searches

The generated JSON files can be reviewed on the official trade site or
passed to ``tools/snipe.py --query FILE``. This module never posts a search,
arms a live monitor, buys, whispers, crafts, or lists anything.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PATH = os.path.join(_ROOT, "market", "high_end_bases.json")

ALLOWED_STAT_PREFIXES = ("explicit.", "pseudo.", "implicit.", "fractured.",
                         "enchant.")


def load_templates(path: str | None = None) -> dict:
    with open(path or DEFAULT_PATH, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not isinstance(
            data.get("templates"), list):
        raise ValueError("base-filter catalog must contain templates")
    seen = set()
    for row in data["templates"]:
        for key in ("id", "guide_id", "label", "base_type", "ilvl_min",
                    "stats", "min_bankroll_div", "bankroll_fraction",
                    "hard_cap_div", "review"):
            if key not in row:
                raise ValueError(f"{row.get('id', '?')}: missing {key}")
        if row["id"] in seen:
            raise ValueError(f"duplicate filter id: {row['id']}")
        seen.add(row["id"])
        for stat in row["stats"]:
            if not str(stat.get("id", "")).startswith(ALLOWED_STAT_PREFIXES):
                raise ValueError(f"{row['id']}: invalid stat id "
                                 f"{stat.get('id')!r}")
    return data


def price_cap(template: dict, bankroll_div: float) -> float:
    return round(min(
        float(template["hard_cap_div"]),
        bankroll_div * float(template["bankroll_fraction"]),
    ), 2)


def build_query(template: dict, bankroll_div: float) -> dict:
    if bankroll_div <= 0:
        raise ValueError("bankroll-div must be greater than zero")
    cap = price_cap(template, bankroll_div)
    stat_filters = []
    for row in template["stats"]:
        stat = {"id": row["id"]}
        if row.get("value"):
            stat["value"] = dict(row["value"])
        stat_filters.append(stat)
    query = {
        "query": {
            "status": {"option": "online"},
            "type": template["base_type"],
            "stats": [{"type": "and", "filters": stat_filters}],
            "filters": {
                "misc_filters": {
                    "filters": {
                        "ilvl": {"min": int(template["ilvl_min"])},
                        "corrupted": {"option": "false"}
                    }
                },
                "trade_filters": {
                    "filters": {
                        "price": {"option": "divine", "max": cap}
                    }
                }
            }
        },
        "sort": {"price": "asc"}
    }
    return query


def build_bundle(bankroll_div: float, catalog: dict | None = None,
                 only: list[str] | None = None) -> dict:
    if bankroll_div <= 0:
        raise ValueError("bankroll-div must be greater than zero")
    data = catalog or load_templates()
    wanted = set(only or [])
    known = {row["id"] for row in data["templates"]}
    unknown = wanted - known
    if unknown:
        raise ValueError("unknown filters: " + ", ".join(sorted(unknown)))
    queries, skipped = [], []
    for template in data["templates"]:
        if wanted and template["id"] not in wanted:
            continue
        if bankroll_div < float(template["min_bankroll_div"]):
            skipped.append({
                "id": template["id"],
                "reason": f"needs at least "
                          f"{template['min_bankroll_div']:g} div bankroll"
            })
            continue
        queries.append({
            "id": template["id"],
            "guide_id": template["guide_id"],
            "label": template["label"],
            "price_cap_div": price_cap(template, bankroll_div),
            "review": template["review"],
            "query": build_query(template, bankroll_div),
        })
    return {
        "bankroll_div": bankroll_div,
        "queries": queries,
        "skipped": skipped,
        "source": data.get("source", ""),
        "safety": "Review every result and act manually. A price ceiling is "
                  "not a valuation or a buy recommendation."
    }


def render_summary(bundle: dict) -> str:
    lines = [
        f"# Premium base filters ({bundle['bankroll_div']:g} div bankroll)",
        "",
        "Price caps are bankroll guards, not fair-value estimates. Re-price "
        "the craft before every purchase.",
        "",
    ]
    for row in bundle["queries"]:
        lines.extend([
            f"- **{row['label']}** — cap `{row['price_cap_div']:g} div`",
            f"  - guide: `{row['guide_id']}`",
            f"  - review: {row['review']}",
        ])
    if bundle["skipped"]:
        lines.extend(["", "Skipped:"])
        lines.extend(f"- `{row['id']}` — {row['reason']}"
                     for row in bundle["skipped"])
    lines.extend(["", bundle["safety"]])
    return "\n".join(lines).rstrip() + "\n"


def write_bundle(bundle: dict, out_dir: str) -> list[str]:
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    for row in bundle["queries"]:
        safe_id = re.sub(r"[^a-z0-9_-]+", "_", row["id"].lower())
        path = os.path.join(out_dir, safe_id + ".json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(row["query"], f, indent=2)
            f.write("\n")
        paths.append(path)
    summary_path = os.path.join(out_dir, "README.md")
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(render_summary(bundle))
        if paths:
            f.write("\n## Arm the searches\n\n```text\n")
            f.write("python tools/snipe.py \\\n")
            for i, path in enumerate(paths):
                rel = os.path.relpath(path, _ROOT)
                suffix = " \\" if i < len(paths) - 1 else ""
                f.write(f"  --query {rel}{suffix}\n")
            f.write("```\n\nSet `POESESSID` first; every purchase and "
                    "message remains your manual action.\n")
    return paths


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="market.base_filters",
        description="Generate bankroll-capped trade queries for premium "
                    "craft bases.")
    p.add_argument("--bankroll-div", type=float, required=True)
    p.add_argument("--catalog", default=DEFAULT_PATH)
    p.add_argument("--only", action="append",
                   help="filter id to include (repeatable)")
    p.add_argument("--out-dir", default=None,
                   help="write one snipe-compatible JSON per search")
    p.add_argument("--json", action="store_true",
                   help="print the full bundle as JSON")
    return p


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        catalog = load_templates(args.catalog)
        bundle = build_bundle(args.bankroll_div, catalog, args.only)
        if args.out_dir:
            paths = write_bundle(bundle, args.out_dir)
            print(f"wrote {len(paths)} trade queries to {args.out_dir}")
        elif args.json:
            print(json.dumps(bundle, indent=2))
        else:
            print(render_summary(bundle), end="")
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"base filter error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
