"""Market LLM layer: launch watchlist, daily brief, anomaly explainer.

Addendum section 4.4 (task 26). Three subcommands, all standard tier:

    python -m market.brief watchlist [--summary ...] [--recs ...] [--out ...]
        data/3.29/summary.json (+ optional advisor recommendations markdown)
        -> LLM -> market/watchlist.json. Every entry cites a summary item id
        or carries source "assumption" (uncited ids are coerced).

    python -m market.brief daily [--db ...] [--watchlist ...] [--out ...]
        top-20 stored opportunities + 24h trendlines + watchlist hits
        -> one-page markdown brief to stdout or --out.

    python -m market.brief explain <opportunity_id> [--db ...]
        one opportunity + its snapshots -> probable-cause label
        {price_fixing|patch_demand|low_liquidity|genuine} with reasoning.
        Advisory only; never changes scanner output.

All three degrade to a clear "LLM disabled - skipped" message (exit 0)
under LLMDisabled; the scanner/store never depend on this module. Every
whisper and trade remains a human action — this module only writes text.

Stdlib only; import-safe (no side effects at import time when imported as
the ``market.brief`` package module).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

try:
    from market import prompts
except ImportError:  # run as a loose script: python market/brief.py ...
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)
    from market import prompts

from llm.client import LLM, LLMDisabled, LLMError   # noqa: E402
from market.store import DEFAULT_DB_PATH, Store     # noqa: E402

DEFAULT_SUMMARY_PATH = os.path.join(_ROOT, "data", "3.29", "summary.json")
DEFAULT_WATCHLIST_PATH = os.path.join(_HERE, "watchlist.json")

CAUSES = ("price_fixing", "patch_demand", "low_liquidity", "genuine")

# Watchlist entry format per docs/INTERFACES.md ("Watchlist"). The LLM
# returns an object wrapper (structured output wants a top-level object);
# the bare list is what gets written to market/watchlist.json.
WATCHLIST_SCHEMA = {
    "type": "object",
    "required": ["watchlist"],
    "properties": {
        "watchlist": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["item", "reason", "source", "expected_window"],
                "properties": {
                    "item": {"type": "string"},
                    "reason": {"type": "string"},
                    "source": {"type": "string"},
                    "expected_window": {"type": "string"},
                },
            },
        },
    },
}

EXPLAIN_SCHEMA = {
    "type": "object",
    "required": ["cause", "reasoning"],
    "properties": {
        "cause": {"type": "string", "enum": list(CAUSES)},
        "reasoning": {"type": "string"},
    },
}


def _default_llm_factory():
    """Build the real standard-tier client (raises LLMDisabled when off)."""
    return LLM("standard")


# ------------------------------------------------------------- data helpers

def _path_items(path) -> list[str]:
    """Item names appearing in an opportunity path like ["chaos->divine"].

    Accepts a decoded list of leg strings or a raw JSON/plain string;
    returns unique names in first-seen order.
    """
    if isinstance(path, str):
        try:
            path = json.loads(path)
        except (TypeError, ValueError):
            path = [path]
    items: list[str] = []
    for leg in path or []:
        if not isinstance(leg, str):
            continue
        for part in leg.split("->"):
            part = part.strip()
            if part and part not in items:
                items.append(part)
    return items


def _trend_summary(points) -> dict | None:
    """Compress [(ts, buy, sell), ...] into first/last/percent-change."""
    if not points:
        return None

    def pct(a, b):
        if a and b:
            return round((b - a) / a * 100.0, 2)
        return None

    (f_ts, f_buy, f_sell), (l_ts, l_buy, l_sell) = points[0], points[-1]
    return {
        "points": len(points),
        "first": {"ts": f_ts, "buy": f_buy, "sell": f_sell},
        "last": {"ts": l_ts, "buy": l_buy, "sell": l_sell},
        "buy_change_pct": pct(f_buy, l_buy),
        "sell_change_pct": pct(f_sell, l_sell),
    }


def _load_watchlist(path) -> list[dict]:
    """market/watchlist.json as a list; [] when absent/unreadable (degrade)."""
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def gather_daily_inputs(store: Store, watchlist: list[dict],
                        hours: float = 24.0, top: int = 20) -> dict:
    """Assemble the daily-brief payload: top opportunities, trendlines, hits."""
    since = (datetime.now(timezone.utc)
             - timedelta(hours=hours)).isoformat(timespec="seconds")
    opps = store.opportunities(since=since)
    opps.sort(key=lambda o: (o.get("est_profit_c") or 0.0), reverse=True)
    top_opps = opps[:top]

    items: list[str] = []
    for opp in top_opps:
        for it in _path_items(opp.get("path")):
            if it not in items:
                items.append(it)
    for entry in watchlist:
        it = entry.get("item")
        if it and it not in items:
            items.append(it)

    trendlines = {}
    for it in items:
        summary = _trend_summary(store.trendline(it, hours))
        if summary:
            trendlines[it] = summary
    hits = [e for e in watchlist if e.get("item") in trendlines]
    return {"window_hours": hours, "opportunities": top_opps,
            "trendlines_24h": trendlines, "watchlist_hits": hits}


# --------------------------------------------------------------- subcommands

def cmd_watchlist(args, llm_factory) -> int:
    """summary.json (+ optional recs md) -> LLM -> market/watchlist.json."""
    if not os.path.exists(args.summary):
        # advisor/summarize.py produces the summary; degrade when absent
        # (per INTERFACES.md there is nothing to cite without it).
        print(f"patch summary not found ({args.summary}) - skipped")
        return 0
    try:
        with open(args.summary, encoding="utf-8") as f:
            summary = json.load(f)
    except (OSError, ValueError) as exc:
        print(f"patch summary unreadable ({args.summary}: {exc}) - skipped")
        return 0
    if not isinstance(summary, dict):
        print(f"patch summary malformed ({args.summary}: not a JSON object)"
              " - skipped")
        return 0
    items = summary.get("items", [])
    known_ids = {it.get("id") for it in items if it.get("id")}

    payload = {"patch": summary.get("patch"), "summary_items": items}
    if args.recs:
        try:
            with open(args.recs, encoding="utf-8") as f:
                payload["advisor_recommendations_md"] = f.read()
        except OSError as exc:
            print(f"recommendations unreadable ({args.recs}: {exc})"
                  " - continuing without them")

    user = ("Patch-note summary (and advisor recommendations, if present) "
            "follow as JSON. Produce the launch watchlist.\n\n"
            + json.dumps(payload, indent=2))
    try:
        llm = llm_factory()
        data = llm.complete(system=prompts.WATCHLIST_PROMPT, messages=user,
                            max_tokens=2000, feature="market_watchlist",
                            json_schema=WATCHLIST_SCHEMA)
    except LLMDisabled as exc:
        print(f"LLM disabled - skipped ({exc})")
        return 0
    except LLMError as exc:
        print(f"LLM error: {exc}", file=sys.stderr)
        return 1

    # Enforce the contract in code, not just in the prompt: every entry
    # cites a real summary item id or is tagged "assumption".
    entries, coerced = [], 0
    for e in data["watchlist"]:
        entry = {"item": e["item"], "reason": e["reason"],
                 "source": e["source"],
                 "expected_window": e["expected_window"]}
        if entry["source"] != "assumption" and entry["source"] not in known_ids:
            entry["source"] = "assumption"
            coerced += 1
        entries.append(entry)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)
        f.write("\n")
    msg = f"wrote {len(entries)} watchlist entries to {args.out}"
    if coerced:
        msg += f' ({coerced} uncited source(s) coerced to "assumption")'
    print(msg)
    return 0


def cmd_daily(args, llm_factory) -> int:
    """Stored opportunities + trendlines + watchlist hits -> markdown brief."""
    if not os.path.exists(args.db):
        print(f"market database not found ({args.db}) - nothing to brief")
        return 0
    store = Store(args.db)
    try:
        watchlist = _load_watchlist(args.watchlist)
        payload = gather_daily_inputs(store, watchlist,
                                      hours=args.hours, top=args.top)
    finally:
        store.close()
    if not payload["opportunities"] and not payload["trendlines_24h"]:
        print(f"no market data in the last {args.hours:g}h - nothing to brief")
        return 0

    user = ("Market data for today's brief follows as JSON.\n\n"
            + json.dumps(payload, indent=2))
    try:
        llm = llm_factory()
        md = llm.complete(system=prompts.DAILY_BRIEF_PROMPT, messages=user,
                          max_tokens=2000, feature="market_daily_brief")
    except LLMDisabled as exc:
        print(f"LLM disabled - skipped ({exc})")
        return 0
    except LLMError as exc:
        print(f"LLM error: {exc}", file=sys.stderr)
        return 1

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(md if md.endswith("\n") else md + "\n")
        print(f"wrote daily brief to {args.out}")
    else:
        print(md)
    return 0


def _explain_payload(opp: dict, db_path, watchlist_path, hours: float) -> dict:
    """Context payload for the anomaly explainer: latest quotes and 24h
    trendlines for the path's items plus matching watchlist notes."""
    items = _path_items(opp.get("path"))
    latest, trends = [], {}
    if db_path and os.path.exists(db_path):
        store = Store(db_path)
        try:
            latest = [{k: row.get(k) for k in ("ts", "source", "league",
                                               "item", "buy", "sell",
                                               "buy_vol", "sell_vol")}
                      for row in store.latest_snapshots()   # raw omitted (bulk)
                      if row.get("item") in items]
            for it in items:
                summary = _trend_summary(store.trendline(it, hours))
                if summary:
                    trends[it] = summary
        finally:
            store.close()
    notes = [e for e in _load_watchlist(watchlist_path)
             if e.get("item") in items]
    return {"opportunity": opp, "latest_quotes": latest,
            "trendlines_24h": trends, "context_notes": notes}


def explain_opportunity(opp: dict, db_path=DEFAULT_DB_PATH,
                        watchlist_path=DEFAULT_WATCHLIST_PATH,
                        hours: float = 24.0, llm_factory=None) -> str:
    """One opportunity dict -> probable-cause text (the console's '?').

    Advisory only; never changes scanner output. Raises LLMDisabled when
    the LLM is off (callers degrade to a 'skipped' notice) and LLMError
    on API failure.
    """
    payload = _explain_payload(opp, db_path, watchlist_path, hours)
    user = ("Explain the probable cause of this scanner opportunity. "
            "Data follows as JSON.\n\n" + json.dumps(payload, indent=2))
    llm = (llm_factory or _default_llm_factory)()
    data = llm.complete(system=prompts.ANOMALY_EXPLAINER_PROMPT,
                        messages=user, max_tokens=800,
                        feature="market_anomaly_explain",
                        json_schema=EXPLAIN_SCHEMA)
    cause = data["cause"]
    lines = []
    if cause not in CAUSES:
        lines.append(f"warning: unrecognized cause {cause!r}"
                     f" (expected one of: {', '.join(CAUSES)})")
    lines.append(f"probable cause: {cause}")
    lines.append(f"reasoning: {data['reasoning']}")
    lines.append("(advisory only - scanner output is unchanged;"
                 " every trade is a human action)")
    return "\n".join(lines)


def cmd_explain(args, llm_factory) -> int:
    """One opportunity + its snapshots -> probable-cause label. Advisory."""
    if not os.path.exists(args.db):
        print(f"market database not found ({args.db})", file=sys.stderr)
        return 1
    store = Store(args.db)
    try:
        opp = next((o for o in store.opportunities()
                    if o.get("id") == args.opportunity_id), None)
    finally:
        store.close()
    if opp is None:
        print(f"opportunity {args.opportunity_id!r} not found in"
              f" {args.db}", file=sys.stderr)
        return 1
    try:
        text = explain_opportunity(opp, db_path=args.db,
                                   watchlist_path=args.watchlist,
                                   hours=args.hours, llm_factory=llm_factory)
    except LLMDisabled as exc:
        print(f"LLM disabled - skipped ({exc})")
        return 0
    except LLMError as exc:
        print(f"LLM error: {exc}", file=sys.stderr)
        return 1
    print(f"opportunity {opp['id']}  kind={opp.get('kind')}"
          f"  margin={opp.get('margin_pct')}%"
          f"  confidence={opp.get('confidence')}")
    print(text)
    return 0


# ----------------------------------------------------------------------- CLI

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="market.brief",
        description="Market LLM layer: watchlist, daily brief, anomaly"
                    " explainer (advisory only; degrades when LLM is off).")
    sub = p.add_subparsers(dest="command", required=True)

    w = sub.add_parser("watchlist",
                       help="patch summary (+ advisor recs) -> watchlist.json")
    w.add_argument("--summary", default=DEFAULT_SUMMARY_PATH,
                   help="patch-note summary JSON (advisor output)")
    w.add_argument("--recs", default=None,
                   help="optional advisor recommendations markdown file")
    w.add_argument("--out", default=DEFAULT_WATCHLIST_PATH,
                   help="watchlist JSON output path")
    w.set_defaults(func=cmd_watchlist)

    d = sub.add_parser("daily",
                       help="top opportunities + 24h trends -> markdown brief")
    d.add_argument("--db", default=DEFAULT_DB_PATH, help="market SQLite DB")
    d.add_argument("--watchlist", default=DEFAULT_WATCHLIST_PATH,
                   help="watchlist JSON (for hit detection)")
    d.add_argument("--hours", type=float, default=24.0,
                   help="trendline window in hours")
    d.add_argument("--top", type=int, default=20,
                   help="number of top opportunities to brief")
    d.add_argument("--out", default=None,
                   help="write markdown here instead of stdout")
    d.set_defaults(func=cmd_daily)

    e = sub.add_parser("explain",
                       help="label one opportunity's probable cause (advisory)")
    e.add_argument("opportunity_id", help="id from the opportunities table")
    e.add_argument("--db", default=DEFAULT_DB_PATH, help="market SQLite DB")
    e.add_argument("--watchlist", default=DEFAULT_WATCHLIST_PATH,
                   help="watchlist JSON (context notes)")
    e.add_argument("--hours", type=float, default=24.0,
                   help="snapshot window in hours")
    e.set_defaults(func=cmd_explain)
    return p


def main(argv=None, llm_factory=None) -> int:
    args = _build_parser().parse_args(argv)
    return args.func(args, llm_factory or _default_llm_factory)


if __name__ == "__main__":
    sys.exit(main())
