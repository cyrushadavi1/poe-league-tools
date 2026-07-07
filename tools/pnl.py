"""PnL report: realized vs expected profit from the market trade journal.

Reads executions (journaled by market/console.py's 'j' command) joined to
the opportunities the scanner produced, and prints realized-vs-expected
profit by day and by kind, totals, and a haircut-calibration hint (the
average realized/expected ratio). This is the calibration loop for the
scanner's haircut and thresholds in market/config.json — tune on numbers,
not vibes.

Stdlib only, import-safe, offline: it only ever reads the local SQLite DB
(docs/INTERFACES.md "Market DB" schema).

Usage: python tools/pnl.py [--db market/market.db]
"""
from __future__ import annotations

import argparse
import os
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(ROOT, "market", "market.db")


def _table_columns(con, table: str) -> set[str]:
    return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}


def load_rows(db_path: str) -> list[dict]:
    """Journal rows -> plain dicts for aggregation.

    `expected`/`kind` prefer the executions snapshot columns (recorded at
    journal time — rescans overwrite the live opportunities row, so the
    live value would mis-calibrate the haircut); rows journaled before
    those columns existed fall back to a LEFT JOIN on opportunities, so
    pruned-opportunity fills still count toward realized totals.

    Only the missing-journal case degrades to []; any other database
    error (e.g. 'database is locked') propagates so it is never
    misreported as 'no executions journaled yet'.
    """
    con = sqlite3.connect(db_path)
    try:
        exec_cols = _table_columns(con, "executions")
        if not exec_cols:
            return []                       # journal table not created yet
        kind_expr, exp_expr = "e.kind", "e.expected_profit_c"
        if "kind" not in exec_cols:
            kind_expr = "NULL"
        if "expected_profit_c" not in exec_cols:
            exp_expr = "NULL"
        join = ""
        if _table_columns(con, "opportunities"):
            kind_expr = f"COALESCE({kind_expr}, o.kind)"
            exp_expr = f"COALESCE({exp_expr}, o.est_profit_c)"
            join = " LEFT JOIN opportunities o ON e.opp_id = o.id"
        cur = con.execute(
            f"SELECT e.ts, e.realized_profit_c, e.minutes, {kind_expr},"
            f" {exp_expr} FROM executions e{join}")
        return [{"ts": r[0], "realized": r[1], "minutes": r[2],
                 "kind": r[3], "expected": r[4]} for r in cur]
    finally:
        con.close()


def aggregate(rows: list[dict]) -> dict:
    """Deterministic aggregation: by day, by kind, totals, calibration.

    ratio = sum(realized) / sum(expected) over fills that still join to
    an opportunity with a non-zero est_profit_c (the "matched" fills) —
    that is the haircut-calibration signal.
    """
    def bucket() -> dict:
        return {"realized": 0.0, "expected": 0.0, "minutes": 0.0, "n": 0}

    by_day: dict[str, dict] = {}
    by_kind: dict[str, dict] = {}
    total = bucket()
    matched_realized = matched_expected = 0.0
    matched_n = 0
    for r in rows:
        day = (r.get("ts") or "")[:10] or "?"
        kind = r.get("kind") or "?"
        realized = float(r.get("realized") or 0.0)
        minutes = float(r.get("minutes") or 0.0)
        expected = r.get("expected")
        exp = float(expected) if expected is not None else 0.0
        for b in (by_day.setdefault(day, bucket()),
                  by_kind.setdefault(kind, bucket()), total):
            b["realized"] += realized
            b["expected"] += exp
            b["minutes"] += minutes
            b["n"] += 1
        if expected is not None and exp != 0.0:
            matched_realized += realized
            matched_expected += exp
            matched_n += 1
    return {
        "by_day": by_day,
        "by_kind": by_kind,
        "total": total,
        "ratio": (matched_realized / matched_expected)
                 if matched_expected else None,
        "ratio_n": matched_n,
        "realized_per_hour": (total["realized"] / (total["minutes"] / 60.0))
                             if total["minutes"] else None,
    }


def _line(label: str, b: dict) -> str:
    return (f"  {label:<12} realized {b['realized']:>9.1f}c  "
            f"expected {b['expected']:>9.1f}c  fills {b['n']:>3}  "
            f"minutes {b['minutes']:>6.1f}")


def format_report(agg: dict) -> str:
    total = agg["total"]
    if not total["n"]:
        return ("No executions journaled yet - journal fills with the "
                "console's 'j' command first.")
    lines = [f"PnL report - {total['n']} fills journaled", "", "By day:"]
    for day in sorted(agg["by_day"]):
        lines.append(_line(day, agg["by_day"][day]))
    lines += ["", "By kind:"]
    for kind in sorted(agg["by_kind"]):
        lines.append(_line(kind, agg["by_kind"][kind]))
    per_hour = agg["realized_per_hour"]
    lines += ["", _line("TOTAL", total)
              + (f"  realized/h {per_hour:.1f}c" if per_hour is not None
                 else "")]
    ratio = agg["ratio"]
    if ratio is None:
        lines.append("Calibration: no fills matched to a stored "
                     "opportunity yet - no realized/expected ratio.")
    else:
        lines.append(f"Calibration: avg realized/expected = {ratio:.2f} "
                     f"over {agg['ratio_n']} matched fills")
        if ratio < 0.9:
            lines.append("  -> realized lags the model: consider raising "
                         "the haircut / min_margin_pct in market/config.json")
        elif ratio > 1.1:
            lines.append("  -> model is conservative: consider lowering "
                         "the haircut in market/config.json")
        else:
            lines.append("  -> haircut looks well calibrated")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Realized vs expected PnL from the market trade "
                    "journal (haircut calibration loop).")
    ap.add_argument("--db", default=DEFAULT_DB, help="path to market.db")
    args = ap.parse_args(argv)
    if not os.path.exists(args.db):
        print(f"no market DB at {args.db} - run the daemon/console first")
        return 1
    print(format_report(aggregate(load_rows(args.db))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
