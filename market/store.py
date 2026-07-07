"""SQLite storage for market snapshots, opportunities, and executions.

Schema is the exact contract from docs/INTERFACES.md ("Market DB").
Pure stdlib, import-safe (no side effects at import time). The database
file (default market/market.db) is gitignored via market/.gitignore.

Usage:
    from market.store import Store
    store = Store()                    # default market/market.db
    store.insert_snapshots(rows)       # INSERT OR REPLACE
    latest = store.latest_snapshots("ninja")
    store.close()
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

_MARKET_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB_PATH = os.path.join(_MARKET_DIR, "market.db")

# Exact schema from docs/INTERFACES.md — do not diverge.
_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS snapshots(ts TEXT, source TEXT, league TEXT, item TEXT,
  buy REAL, sell REAL, buy_vol REAL, sell_vol REAL, raw TEXT,
  PRIMARY KEY(ts, source, item))""",
    """CREATE TABLE IF NOT EXISTS opportunities(id TEXT PRIMARY KEY, ts TEXT, kind TEXT, path TEXT,
  margin_pct REAL, est_profit_c REAL, liq_score REAL, confidence TEXT, flags TEXT)""",
    # expected_profit_c/kind snapshot the opportunity as seen at journal
    # time (opportunity ids are stable per path and rescans overwrite
    # est_profit_c, so PnL calibration must not read the live row).
    """CREATE TABLE IF NOT EXISTS executions(id TEXT PRIMARY KEY, opp_id TEXT, ts TEXT,
  legs TEXT, realized_profit_c REAL, minutes REAL, notes TEXT,
  expected_profit_c REAL, kind TEXT)""",
    # Helper indexes (additive; not part of the contract tables themselves).
    "CREATE INDEX IF NOT EXISTS idx_snapshots_item_ts ON snapshots(item, ts)",
    "CREATE INDEX IF NOT EXISTS idx_snapshots_src_item_ts ON snapshots(source, item, ts)",
]

# Additive migrations for databases created before a column existed
# (sqlite has no ADD COLUMN IF NOT EXISTS; duplicates are ignored).
_MIGRATIONS = (
    "ALTER TABLE executions ADD COLUMN expected_profit_c REAL",
    "ALTER TABLE executions ADD COLUMN kind TEXT",
)

_SNAPSHOT_COLS = ("ts", "source", "league", "item",
                  "buy", "sell", "buy_vol", "sell_vol", "raw")
_OPP_COLS = ("id", "ts", "kind", "path", "margin_pct", "est_profit_c",
             "liq_score", "confidence", "flags")


def utcnow_iso() -> str:
    """Current UTC time as an ISO-8601 string (sorts lexicographically)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _as_text(value):
    """JSON-encode lists/dicts for TEXT columns; pass strings/None through."""
    if value is None or isinstance(value, str):
        return value
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


class Store:
    """Wrapper around the market SQLite database.

    Opens (creating parent directories if needed) and migrates the schema
    on construction. Safe to re-open on an existing database: all schema
    statements are idempotent (CREATE ... IF NOT EXISTS).
    """

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        parent = os.path.dirname(os.path.abspath(db_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.migrate()

    # ------------------------------------------------------------ lifecycle
    def migrate(self) -> None:
        """Create tables/indexes if missing. Idempotent."""
        cur = self.conn.cursor()
        for stmt in _SCHEMA:
            cur.execute(stmt)
        for stmt in _MIGRATIONS:
            try:
                cur.execute(stmt)
            except sqlite3.OperationalError:
                pass                          # column already exists
        self.conn.commit()

    init = migrate  # alias: Store.init() == Store.migrate()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------ snapshots
    def insert_snapshots(self, rows) -> int:
        """INSERT OR REPLACE snapshot rows; returns the number written.

        Each row is a dict with the snapshot columns; missing ``ts``
        defaults to now (UTC), other missing columns default to None.
        ``raw`` may be a dict/list (stored as JSON text).
        """
        prepared = []
        for row in rows:
            r = dict(row)
            r.setdefault("ts", utcnow_iso())
            r["raw"] = _as_text(r.get("raw"))
            prepared.append(tuple(r.get(c) for c in _SNAPSHOT_COLS))
        if not prepared:
            return 0
        self.conn.executemany(
            "INSERT OR REPLACE INTO snapshots({}) VALUES ({})".format(
                ",".join(_SNAPSHOT_COLS), ",".join("?" * len(_SNAPSHOT_COLS))),
            prepared)
        self.conn.commit()
        return len(prepared)

    def latest_snapshots(self, source: str | None = None) -> list[dict]:
        """Most recent snapshot row per (source, item), as a list of dicts."""
        sql = ("SELECT s.* FROM snapshots s JOIN ("
               "SELECT source, item, MAX(ts) AS mts FROM snapshots"
               "{where} GROUP BY source, item) m "
               "ON s.source = m.source AND s.item = m.item AND s.ts = m.mts")
        args: tuple = ()
        if source is not None:
            sql = sql.format(where=" WHERE source = ?")
            args = (source,)
        else:
            sql = sql.format(where="")
        return [dict(r) for r in self.conn.execute(sql, args)]

    def trendline(self, item: str, hours: float,
                  source: str | None = None) -> list[tuple]:
        """[(ts, buy, sell)] for *item* over the last *hours*, oldest first."""
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(hours=hours)).isoformat(timespec="seconds")
        sql = ("SELECT ts, buy, sell FROM snapshots"
               " WHERE item = ? AND ts >= ?")
        args: list = [item, cutoff]
        if source is not None:
            sql += " AND source = ?"
            args.append(source)
        sql += " ORDER BY ts ASC"
        return [(r["ts"], r["buy"], r["sell"])
                for r in self.conn.execute(sql, args)]

    # -------------------------------------------------------- opportunities
    def upsert_opportunities(self, opportunities) -> int:
        """INSERT OR REPLACE opportunity dicts (scanner output objects).

        Extra keys beyond the table columns (est_profit_per_hour, actions)
        are ignored; ``path``/``flags`` lists are stored as JSON text.
        Missing ``ts`` defaults to now (UTC).
        """
        prepared = []
        for opp in opportunities:
            o = dict(opp)
            o.setdefault("ts", utcnow_iso())
            o["path"] = _as_text(o.get("path"))
            o["flags"] = _as_text(o.get("flags"))
            prepared.append(tuple(o.get(c) for c in _OPP_COLS))
        if not prepared:
            return 0
        self.conn.executemany(
            "INSERT OR REPLACE INTO opportunities({}) VALUES ({})".format(
                ",".join(_OPP_COLS), ",".join("?" * len(_OPP_COLS))),
            prepared)
        self.conn.commit()
        return len(prepared)

    def opportunities(self, since: str | None = None) -> list[dict]:
        """Stored opportunities (optionally with ts >= *since*), newest first.

        ``path``/``flags`` are decoded back to lists when they hold JSON.
        """
        sql = "SELECT * FROM opportunities"
        args: tuple = ()
        if since is not None:
            sql += " WHERE ts >= ?"
            args = (since,)
        sql += " ORDER BY ts DESC"
        out = []
        for r in self.conn.execute(sql, args):
            d = dict(r)
            for key in ("path", "flags"):
                if d.get(key):
                    try:
                        d[key] = json.loads(d[key])
                    except (ValueError, TypeError):
                        pass  # legacy plain-text values pass through
            out.append(d)
        return out

    # ----------------------------------------------------------- executions
    def record_execution(self, opp_id: str, legs, realized_profit_c: float,
                         minutes: float, notes: str = "",
                         ts: str | None = None,
                         exec_id: str | None = None,
                         expected_profit_c: float | None = None,
                         kind: str | None = None) -> str:
        """Journal one manual trade execution; returns its id.

        ``expected_profit_c``/``kind`` snapshot the opportunity at journal
        time (rescans overwrite the live opportunities row). Every
        execution recorded here was performed by a human — this is a
        journal, never an action trigger.
        """
        exec_id = exec_id or uuid.uuid4().hex
        self.conn.execute(
            "INSERT OR REPLACE INTO executions"
            "(id, opp_id, ts, legs, realized_profit_c, minutes, notes,"
            " expected_profit_c, kind)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (exec_id, opp_id, ts or utcnow_iso(), _as_text(legs),
             realized_profit_c, minutes, notes, expected_profit_c, kind))
        self.conn.commit()
        return exec_id

    def executions(self, opp_id: str | None = None) -> list[dict]:
        """Journaled executions (optionally for one opportunity), oldest first."""
        sql = "SELECT * FROM executions"
        args: tuple = ()
        if opp_id is not None:
            sql += " WHERE opp_id = ?"
            args = (opp_id,)
        sql += " ORDER BY ts ASC"
        out = []
        for r in self.conn.execute(sql, args):
            d = dict(r)
            if d.get("legs"):
                try:
                    d["legs"] = json.loads(d["legs"])
                except (ValueError, TypeError):
                    pass
            out.append(d)
        return out

    # ---------------------------------------------------------- maintenance
    def prune(self, days: float) -> int:
        """Delete snapshots and opportunities older than *days*.

        Executions (the trade journal) are never pruned. Returns the total
        number of rows deleted.
        """
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(days=days)).isoformat(timespec="seconds")
        cur = self.conn.cursor()
        cur.execute("DELETE FROM snapshots WHERE ts < ?", (cutoff,))
        deleted = cur.rowcount
        cur.execute("DELETE FROM opportunities WHERE ts < ?", (cutoff,))
        deleted += cur.rowcount
        self.conn.commit()
        return deleted
