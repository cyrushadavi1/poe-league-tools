"""LLM spend report: per-feature and total token counts + estimated $.

Reads llm_usage.jsonl (written by llm/client.py, one JSON line per API call:
{"ts", "feature", "tier", "model", "in_tokens", "out_tokens"}) and prices
from llm/config.json ("prices_per_mtok": model -> [input_$, output_$] per
million tokens).

Usage:
    .venv/bin/python tools/llm_report.py [path/to/llm_usage.jsonl]

Stdlib only, offline, import-safe (no side effects at import time).
Handles a missing or empty usage file gracefully.
"""
from __future__ import annotations

import argparse
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_USAGE_PATH = os.path.join(ROOT, "llm_usage.jsonl")
DEFAULT_CONFIG_PATH = os.path.join(ROOT, "llm", "config.json")


def load_prices(config_path=DEFAULT_CONFIG_PATH):
    """Return {model: [input_$_per_mtok, output_$_per_mtok]} ({} on failure)."""
    try:
        with open(config_path, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return {}
    prices = cfg.get("prices_per_mtok", {})
    return prices if isinstance(prices, dict) else {}


def aggregate(usage_path, prices):
    """Aggregate a usage jsonl file.

    Returns (per_feature, totals):
      per_feature: {feature: {"calls", "in_tokens", "out_tokens", "cost",
                              "unknown_models": set()}}
      totals:      {"calls", "in_tokens", "out_tokens", "cost", "skipped"}
    A missing or empty file yields ({}, zeroed totals). Malformed lines are
    skipped and counted in totals["skipped"].
    """
    per = {}
    totals = {"calls": 0, "in_tokens": 0, "out_tokens": 0,
              "cost": 0.0, "skipped": 0}
    try:
        with open(usage_path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return per, totals

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            feature = rec["feature"]
            model = rec.get("model", "?")
            in_tok = int(rec.get("in_tokens", 0))
            out_tok = int(rec.get("out_tokens", 0))
        except (ValueError, TypeError, KeyError):
            totals["skipped"] += 1
            continue

        row = per.setdefault(feature, {
            "calls": 0, "in_tokens": 0, "out_tokens": 0,
            "cost": 0.0, "unknown_models": set(),
        })
        row["calls"] += 1
        row["in_tokens"] += in_tok
        row["out_tokens"] += out_tok
        price = prices.get(model)
        if price and len(price) == 2:
            cost = in_tok / 1e6 * price[0] + out_tok / 1e6 * price[1]
            row["cost"] += cost
            totals["cost"] += cost
        else:
            row["unknown_models"].add(model)
        totals["calls"] += 1
        totals["in_tokens"] += in_tok
        totals["out_tokens"] += out_tok
    return per, totals


def format_report(per, totals):
    """Render the aggregate as a plain-text table (returns a str)."""
    header = (f"{'feature':<20}{'calls':>7}{'in_tok':>12}"
              f"{'out_tok':>12}{'est $':>10}")
    out = [header, "-" * len(header)]
    for feature in sorted(per):
        row = per[feature]
        out.append(f"{feature:<20}{row['calls']:>7}{row['in_tokens']:>12}"
                   f"{row['out_tokens']:>12}{row['cost']:>10.4f}")
        if row["unknown_models"]:
            unpriced = ", ".join(sorted(row["unknown_models"]))
            out.append(f"{'':<20}(no price for: {unpriced} — "
                       f"cost above excludes those calls)")
    out.append("-" * len(header))
    out.append(f"{'TOTAL':<20}{totals['calls']:>7}{totals['in_tokens']:>12}"
               f"{totals['out_tokens']:>12}{totals['cost']:>10.4f}")
    if totals["skipped"]:
        out.append(f"({totals['skipped']} malformed line(s) skipped)")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="LLM spend report: per-feature and total token counts "
                    "plus estimated cost from llm_usage.jsonl.")
    ap.add_argument("usage_path", nargs="?", default=DEFAULT_USAGE_PATH,
                    help="usage jsonl written by llm/client.py "
                         "(default: llm_usage.jsonl at the repo root)")
    args = ap.parse_args(argv)
    usage_path = args.usage_path
    per, totals = aggregate(usage_path, load_prices())
    if not per:
        print(f"No LLM usage recorded ({usage_path} missing or empty).")
        return 0
    print(f"LLM usage report — {usage_path}")
    print(format_report(per, totals))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
