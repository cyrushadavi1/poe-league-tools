"""Shared parsers for the trade-site rate-limit headers.

Extracted from tools/tradeq.py (same semantics, live-structured from the
official site's headers; VERIFY against live headers during the Mirage
rehearsal). tradeq keeps its own private copies — this module exists so
market/livesearch.py doesn't import a CLI script for two pure functions.

Stdlib only, no side effects.
"""
from __future__ import annotations

DEFAULT_BACKOFF_S = 2.0


def retry_after_seconds(value, default: float = DEFAULT_BACKOFF_S) -> float:
    """Retry-After header -> seconds (numeric or HTTP-date), uncapped."""
    if value is None or str(value).strip() == "":
        return default
    try:
        return max(float(value), 0.0)
    except (TypeError, ValueError):
        pass
    try:
        from email.utils import parsedate_to_datetime
        from datetime import datetime, timezone
        dt = parsedate_to_datetime(str(value))
        return max((dt - datetime.now(timezone.utc)).total_seconds(), 0.0)
    except (TypeError, ValueError):
        return default


def bucket_deadline(headers, now: float) -> float:
    """Earliest next-allowed time from X-Rate-Limit-* headers.

    Rules look like "8:10:60" (max hits : window s : penalty s) and state
    like "5:10:0" (hits in window : window s : active penalty s), comma-
    separated per bucket. An active penalty, or a bucket at/over its
    limit, exhausts the budget: back off for the penalty/window length.
    """
    deadline = now
    if headers is None:
        return deadline
    rules = [s.strip() for s in
             (headers.get("X-Rate-Limit-Rules") or "Ip").split(",")
             if s.strip()]
    for name in rules:
        rule = headers.get(f"X-Rate-Limit-{name}")
        state = headers.get(f"X-Rate-Limit-{name}-State")
        if not rule or not state:
            continue
        for r, s in zip(rule.split(","), state.split(",")):
            try:
                max_hits, window, _penalty = (int(x) for x in r.split(":"))
                hits, _, active_penalty = (int(x) for x in s.split(":"))
            except ValueError:
                continue
            if active_penalty > 0:
                deadline = max(deadline, now + active_penalty)
            elif hits >= max_hits:
                deadline = max(deadline, now + window)
    return deadline
