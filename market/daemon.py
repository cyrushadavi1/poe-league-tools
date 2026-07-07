"""Market snapshot polling daemon (single process).

Loops forever: every ``poll_currency_s`` seconds it fetches the poe.ninja
currency overview, every ``poll_items_s`` seconds the item overviews, and
writes the normalized rows into the snapshots table via market.store.Store.
One status line is logged per cycle. Clean shutdown on SIGINT / Ctrl-C.

    .venv/bin/python market/daemon.py            # run forever
    .venv/bin/python market/daemon.py --once     # one cycle (both feeds), exit
    .venv/bin/python market/daemon.py --db /path/market.db --config market/config.json

ToS/rate invariants: all HTTP lives in market/sources.py, whose client
sends the identifying User-Agent, honors 429/Retry-After, and enforces
the 1-request-per-2-seconds floor. This daemon only schedules calls at
the (much slower) configured poll intervals and never touches the game.

market.sources is imported lazily (inside the fetch path) so that this
module — and the store tests that exercise run_cycle() with a fake
source — import cleanly while sources.py is still being built.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time

try:
    from market.store import Store, utcnow_iso
except ImportError:  # executed as a script: sys.path[0] is market/
    from store import Store, utcnow_iso  # type: ignore

_MARKET_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG_PATH = os.path.join(_MARKET_DIR, "config.json")

# Fallbacks when config.json is missing keys (values from docs/INTERFACES.md).
CONFIG_DEFAULTS = {
    "league": "Standard",
    "poll_currency_s": 300,
    "poll_items_s": 1800,
}


def load_config(path: str | None = None) -> dict:
    """market/config.json merged over CONFIG_DEFAULTS; tolerant of absence."""
    cfg = dict(CONFIG_DEFAULTS)
    path = path or DEFAULT_CONFIG_PATH
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            cfg.update(json.load(f))
    return cfg


# --------------------------------------------------------------- source


class NinjaSource:
    """Adapter turning market.sources.NinjaClient fetches into snapshot rows.

    Uses the real market/sources.py API (aligned 2026-07-07):
    NinjaClient(league).snapshot_currency(type_) / .snapshot_items(type_)
    return normalized snapshot rows already carrying ts/source/league;
    _normalize only fills those fields when a row lacks them. The import
    stays lazy so this module (and the store tests, which drive
    run_cycle() with a fake source) never touch market.sources.
    """

    def __init__(self, league: str):
        self.league = league
        self._client = None

    def _ninja(self):
        if self._client is None:
            try:
                from market.sources import NinjaClient
            except ImportError:  # executed as a script: sys.path[0] is market/
                from sources import NinjaClient  # type: ignore
            self._client = NinjaClient(self.league)
        return self._client

    def _normalize(self, rows) -> list[dict]:
        out = []
        for row in rows:
            r = dict(row)
            r.setdefault("ts", utcnow_iso())
            r.setdefault("source", "ninja")
            r.setdefault("league", self.league)
            out.append(r)
        return out

    def currency_rows(self) -> list[dict]:
        client = self._ninja()
        rows: list[dict] = []
        for type_ in client.CURRENCY_TYPES:
            rows.extend(client.snapshot_currency(type_))
        return self._normalize(rows)

    def item_rows(self) -> list[dict]:
        client = self._ninja()
        rows: list[dict] = []
        for type_ in client.ITEM_TYPES:
            rows.extend(client.snapshot_items(type_))
        return self._normalize(rows)


# ---------------------------------------------------------------- cycle


def run_cycle(store: Store, source, *, do_currency: bool = True,
              do_items: bool = True) -> dict:
    """Run one poll cycle: fetch the due feeds, write snapshots.

    ``source`` is any object with currency_rows() / item_rows() methods
    returning snapshot row dicts (NinjaSource in production, a fake in
    tests). A failure in one feed is recorded and does not abort the
    other. Returns a status dict:
    {"ts", "currency_rows", "item_rows", "errors": [str]}.
    """
    status = {"ts": utcnow_iso(), "currency_rows": 0, "item_rows": 0,
              "errors": []}
    if do_currency:
        try:
            status["currency_rows"] = store.insert_snapshots(
                source.currency_rows())
        except Exception as exc:  # noqa: BLE001 — daemon must survive
            status["errors"].append(f"currency: {exc}")
    if do_items:
        try:
            status["item_rows"] = store.insert_snapshots(source.item_rows())
        except Exception as exc:  # noqa: BLE001
            status["errors"].append(f"items: {exc}")
    return status


def format_status(status: dict, *, do_currency: bool, do_items: bool) -> str:
    """One human-readable log line per cycle."""
    parts = [status["ts"]]
    parts.append("currency=%s" % (status["currency_rows"] if do_currency
                                  else "-"))
    parts.append("items=%s" % (status["item_rows"] if do_items else "-"))
    if status["errors"]:
        parts.append("errors=" + "; ".join(status["errors"]))
    else:
        parts.append("ok")
    return " ".join(parts)


# ----------------------------------------------------------------- main


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Market snapshot polling daemon (writes market.db).")
    ap.add_argument("--once", action="store_true",
                    help="run a single cycle (both feeds) and exit")
    ap.add_argument("--db", default=None,
                    help="SQLite path (default market/market.db)")
    ap.add_argument("--config", default=None,
                    help="config path (default market/config.json)")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    poll_currency_s = float(cfg["poll_currency_s"])
    poll_items_s = float(cfg["poll_items_s"])
    store = Store(args.db) if args.db else Store()
    source = NinjaSource(cfg["league"])

    stop = {"flag": False}

    def _sigint(_signum, _frame):
        stop["flag"] = True

    prev_handler = signal.signal(signal.SIGINT, _sigint)
    print(f"daemon: league={cfg['league']} db={store.db_path} "
          f"poll_currency_s={poll_currency_s:g} poll_items_s={poll_items_s:g}",
          flush=True)

    next_currency = 0.0   # monotonic deadlines; 0 -> due immediately
    next_items = 0.0
    exit_code = 0
    try:
        while not stop["flag"]:
            now = time.monotonic()
            do_currency = now >= next_currency
            do_items = now >= next_items
            if do_currency or do_items:
                status = run_cycle(store, source,
                                   do_currency=do_currency,
                                   do_items=do_items)
                print(format_status(status, do_currency=do_currency,
                                    do_items=do_items), flush=True)
                if do_currency:
                    next_currency = now + poll_currency_s
                if do_items:
                    next_items = now + poll_items_s
                if args.once:
                    if status["errors"]:
                        exit_code = 1
                    break
            time.sleep(min(1.0, max(0.05,
                                    min(next_currency, next_items) - now)))
    except KeyboardInterrupt:
        pass
    finally:
        signal.signal(signal.SIGINT, prev_handler)
        store.close()
        print("daemon: clean shutdown", flush=True)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
