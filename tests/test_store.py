"""Headless tests: market.store schema/CRUD + market.daemon cycle logic.

Offline by design: tmp-file SQLite databases and a fake source object —
market.sources is never imported (the daemon imports it lazily).
"""
import json
import os
import shutil
import sys
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT]

from market.store import Store, utcnow_iso     # noqa: E402
from market import daemon                      # noqa: E402

assert "market.sources" not in sys.modules, \
    "importing market.daemon must not import market.sources"


def iso(**delta):
    """UTC ISO timestamp offset from now, matching the store's format."""
    return (datetime.now(timezone.utc)
            + timedelta(**delta)).isoformat(timespec="seconds")


tmp = tempfile.mkdtemp(prefix="poe_store_test_")
try:
    db = os.path.join(tmp, "sub", "market.db")   # parent dir auto-created

    # ------------------------------------------------------- schema creates
    store = Store(db)
    names = {r[0] for r in store.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"snapshots", "opportunities", "executions"} <= names, names
    cols = [r[1] for r in store.conn.execute("PRAGMA table_info(snapshots)")]
    assert cols == ["ts", "source", "league", "item", "buy", "sell",
                    "buy_vol", "sell_vol", "raw"], cols
    cols = [r[1] for r in store.conn.execute(
        "PRAGMA table_info(opportunities)")]
    assert cols == ["id", "ts", "kind", "path", "margin_pct", "est_profit_c",
                    "liq_score", "confidence", "flags"], cols
    cols = [r[1] for r in store.conn.execute("PRAGMA table_info(executions)")]
    assert cols == ["id", "opp_id", "ts", "legs", "realized_profit_c",
                    "minutes", "notes", "expected_profit_c", "kind"], cols

    # migrate is idempotent and reopening an existing DB works
    store.migrate()
    store.init()
    store.close()
    store = Store(db)

    # ---------------------------------------------- insert/replace semantics
    t0 = iso(hours=-2)
    n = store.insert_snapshots([
        {"ts": t0, "source": "ninja", "league": "L", "item": "Divine Orb",
         "buy": 210.0, "sell": 208.0, "buy_vol": 900, "sell_vol": 850,
         "raw": {"pay": 210.0}},                        # dict raw -> JSON text
    ])
    assert n == 1
    # same (ts, source, item) again -> replaced, not duplicated
    store.insert_snapshots([
        {"ts": t0, "source": "ninja", "league": "L", "item": "Divine Orb",
         "buy": 215.0, "sell": 209.0, "buy_vol": 901, "sell_vol": 851},
    ])
    rows = list(store.conn.execute(
        "SELECT * FROM snapshots WHERE item='Divine Orb'"))
    assert len(rows) == 1 and rows[0]["buy"] == 215.0
    assert rows[0]["raw"] is None, "replace overwrites every column"
    # raw stored as JSON text when given a dict
    store.insert_snapshots([
        {"ts": t0, "source": "ninja", "league": "L", "item": "Exalted Orb",
         "buy": 20.0, "sell": 19.5, "raw": {"pay": 20.0}},
    ])
    raw = store.conn.execute(
        "SELECT raw FROM snapshots WHERE item='Exalted Orb'").fetchone()[0]
    assert json.loads(raw) == {"pay": 20.0}
    # missing ts is defaulted
    store.insert_snapshots([{"source": "trade", "league": "L",
                             "item": "Chaos Orb", "buy": 1.0, "sell": 1.0}])
    ts = store.conn.execute(
        "SELECT ts FROM snapshots WHERE item='Chaos Orb'").fetchone()[0]
    assert ts and ts.startswith("20"), ts
    assert store.insert_snapshots([]) == 0

    # ------------------------------------- latest_snapshots: newest per pair
    t1, t2 = iso(hours=-1), iso(minutes=-5)
    store.insert_snapshots([
        {"ts": t1, "source": "ninja", "league": "L", "item": "Divine Orb",
         "buy": 220.0, "sell": 218.0},
        {"ts": t2, "source": "ninja", "league": "L", "item": "Divine Orb",
         "buy": 230.0, "sell": 228.0},
        {"ts": t1, "source": "trade", "league": "L", "item": "Divine Orb",
         "buy": 231.0, "sell": 229.0},
    ])
    latest = store.latest_snapshots()
    by_key = {(r["source"], r["item"]): r for r in latest}
    assert len(latest) == len(by_key), "one row per (source, item)"
    assert by_key[("ninja", "Divine Orb")]["buy"] == 230.0, "newest ts wins"
    assert by_key[("trade", "Divine Orb")]["buy"] == 231.0
    assert ("ninja", "Exalted Orb") in by_key
    only_ninja = store.latest_snapshots(source="ninja")
    assert only_ninja and all(r["source"] == "ninja" for r in only_ninja)

    # ------------------------------------------------------- trendline order
    old = iso(hours=-50)                     # outside a 24 h window
    store.insert_snapshots([
        {"ts": old, "source": "ninja", "league": "L", "item": "Divine Orb",
         "buy": 100.0, "sell": 99.0},
    ])
    trend = store.trendline("Divine Orb", hours=24)
    assert [t[0] for t in trend] == sorted(t[0] for t in trend), \
        "trendline must be ascending by ts"
    assert old not in [t[0] for t in trend], "outside-window rows excluded"
    assert (t0, 215.0, 209.0) in trend and trend[-1] == (t2, 230.0, 228.0)
    assert all(len(t) == 3 for t in trend)
    assert store.trendline("Divine Orb", hours=24, source="trade") == \
        [(t1, 231.0, 229.0)]
    assert store.trendline("No Such Item", hours=24) == []

    # ---------------------------------------------------- opportunities upsert
    opp = {"id": "opp1", "ts": t2, "kind": "cycle",
           "path": ["chaos->divine", "divine->chaos"],
           "margin_pct": 6.2, "est_profit_c": 140, "liq_score": 0.7,
           "confidence": "high", "flags": [],
           "est_profit_per_hour": 900,       # extra keys ignored
           "actions": [{"type": "exchange", "instruction": "..."}]}
    assert store.upsert_opportunities([opp]) == 1
    store.upsert_opportunities([dict(opp, margin_pct=7.5,
                                     flags=["price_fixing_suspect"])])
    opps = store.opportunities()
    assert len(opps) == 1 and opps[0]["id"] == "opp1"
    assert opps[0]["margin_pct"] == 7.5, "upsert replaces on same id"
    assert opps[0]["path"] == ["chaos->divine", "divine->chaos"]
    assert opps[0]["flags"] == ["price_fixing_suspect"]
    assert "actions" not in opps[0]
    assert store.opportunities(since=iso(minutes=-1)) == []

    # ---------------------------------------------- executions round-trip
    eid = store.record_execution("opp1", legs=["chaos->divine x140"],
                                 realized_profit_c=120.5, minutes=6.0,
                                 notes="slow whisper")
    assert isinstance(eid, str) and eid
    eid2 = store.record_execution("opp2", legs="manual note", exec_id="e2",
                                  realized_profit_c=-3.0, minutes=2.0,
                                  ts=iso(minutes=-30))
    assert eid2 == "e2"
    execs = store.executions()
    assert [e["id"] for e in execs] == ["e2", eid], "ordered by ts ascending"
    got = store.executions(opp_id="opp1")
    assert len(got) == 1
    assert got[0]["legs"] == ["chaos->divine x140"]
    assert got[0]["realized_profit_c"] == 120.5
    assert got[0]["minutes"] == 6.0 and got[0]["notes"] == "slow whisper"
    assert got[0]["ts"].startswith("20")
    assert store.executions(opp_id="opp2")[0]["legs"] == "manual note"

    # ----------------------------------------------------------------- prune
    store.insert_snapshots([
        {"ts": iso(days=-10), "source": "ninja", "league": "L",
         "item": "Stale Orb", "buy": 1.0, "sell": 1.0},
    ])
    store.upsert_opportunities([{"id": "old", "ts": iso(days=-10),
                                 "kind": "spread", "path": ["a->b"],
                                 "margin_pct": 5.0, "est_profit_c": 1,
                                 "liq_score": 0.1, "confidence": "low",
                                 "flags": []}])
    before = store.conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    deleted = store.prune(days=7)
    assert deleted >= 2, deleted     # the stale snapshot + the old opportunity
    after = store.conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    assert after < before
    items = {r[0] for r in store.conn.execute("SELECT item FROM snapshots")}
    assert "Stale Orb" not in items and "Divine Orb" in items
    remaining_ts = {r[0] for r in store.conn.execute(
        "SELECT ts FROM snapshots")}
    assert old in remaining_ts, "50 h-old row is newer than the 7-day cutoff"
    assert [o["id"] for o in store.opportunities()] == ["opp1"]
    assert len(store.executions()) == 2, "executions are never pruned"
    store.close()

    # ------------------------------------------------- daemon: cycle w/ fake
    class FakeSource:
        def __init__(self, fail_items=False):
            self.fail_items = fail_items
            self.calls = []

        def currency_rows(self):
            self.calls.append("currency")
            return [{"ts": iso(), "source": "ninja", "league": "T",
                     "item": "Divine Orb", "buy": 200.0, "sell": 198.0}]

        def item_rows(self):
            self.calls.append("items")
            if self.fail_items:
                raise RuntimeError("boom")
            return [{"ts": iso(), "source": "ninja", "league": "T",
                     "item": "Mageblood", "buy": 90000.0, "sell": 88000.0}]

    dstore = Store(os.path.join(tmp, "daemon.db"))
    fake = FakeSource()
    status = daemon.run_cycle(dstore, fake)
    assert status["currency_rows"] == 1 and status["item_rows"] == 1
    assert status["errors"] == [] and fake.calls == ["currency", "items"]
    line = daemon.format_status(status, do_currency=True, do_items=True)
    assert "currency=1" in line and "items=1" in line and "ok" in line

    # one failing feed doesn't kill the other
    status = daemon.run_cycle(dstore, FakeSource(fail_items=True))
    assert status["currency_rows"] == 1 and status["item_rows"] == 0
    assert status["errors"] and "boom" in status["errors"][0]
    assert "errors=" in daemon.format_status(status, do_currency=True,
                                             do_items=True)

    # feed gating: skip items when not due
    fake = FakeSource()
    daemon.run_cycle(dstore, fake, do_items=False)
    assert fake.calls == ["currency"]
    assert "items=-" in daemon.format_status(
        daemon.run_cycle(dstore, FakeSource(), do_items=False),
        do_currency=True, do_items=False)
    assert {r["item"] for r in dstore.latest_snapshots()} == \
        {"Divine Orb", "Mageblood"}
    dstore.close()

    # ------------------------------------------------------- daemon: config
    cfg = daemon.load_config()                       # the shipped config.json
    for key in ("league", "bankroll_c", "haircut", "min_margin_pct",
                "min_vol", "poll_currency_s", "poll_items_s"):
        assert key in cfg, f"market/config.json missing {key}"
    assert cfg["poll_currency_s"] == 300 and cfg["poll_items_s"] == 1800
    assert daemon.load_config(os.path.join(tmp, "nope.json"))["league"] == \
        "Standard", "missing config falls back to defaults"
    cfg_path = os.path.join(tmp, "cfg.json")
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump({"league": "TestLeague", "poll_currency_s": 1,
                   "poll_items_s": 1}, f)
    assert daemon.load_config(cfg_path)["league"] == "TestLeague"

    # NinjaSource normalization fills source/league/ts (no network involved)
    ns = daemon.NinjaSource("TestLeague")
    norm = ns._normalize([{"item": "Chaos Orb", "buy": 1.0, "sell": 1.0}])
    assert norm[0]["source"] == "ninja" and norm[0]["league"] == "TestLeague"
    assert norm[0]["ts"]

    # --------------------------------------------- daemon: main --once, faked
    main_db = os.path.join(tmp, "main.db")
    orig = daemon.NinjaSource
    daemon.NinjaSource = lambda league: FakeSource()   # no network
    try:
        rc = daemon.main(["--once", "--db", main_db, "--config", cfg_path])
    finally:
        daemon.NinjaSource = orig
    assert rc == 0
    check = Store(main_db)
    assert {r["item"] for r in check.latest_snapshots()} == \
        {"Divine Orb", "Mageblood"}
    check.close()

    # --once with a broken source: degrades to an error status + nonzero
    # exit, never a crash. (Never exercise the real NinjaSource fetch path
    # here — it would import market.sources, whose fetches are network.)
    class BrokenSource:
        def currency_rows(self):
            raise RuntimeError("no sources module")

        def item_rows(self):
            raise RuntimeError("no sources module")

    daemon.NinjaSource = lambda league: BrokenSource()
    try:
        rc = daemon.main(["--once", "--db", os.path.join(tmp, "err.db"),
                          "--config", cfg_path])
    finally:
        daemon.NinjaSource = orig
    assert rc == 1
    assert "market.sources" not in sys.modules, \
        "tests must stay offline: market.sources was imported"
finally:
    shutil.rmtree(tmp)

print("ALL TESTS PASSED")
