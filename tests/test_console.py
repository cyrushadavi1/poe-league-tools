"""Headless tests: execution console (render/commands/journal) + PnL math.

Offline by construction: a tmp SQLite store, an injected fake scanner,
fake input/print/clipboard callables — no network, no Qt, no real
clipboard, no LLM.
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from collections import deque
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "tools")]

from market.console import Console, _as_list, load_config   # noqa: E402
import pnl                                                   # noqa: E402

# ------------------------------------------------- tmp store (docs schema)
tmp = tempfile.mkdtemp(prefix="poe_console_test_")
DB = os.path.join(tmp, "market.db")
con = sqlite3.connect(DB)
con.executescript("""
CREATE TABLE snapshots(ts TEXT, source TEXT, league TEXT, item TEXT,
  buy REAL, sell REAL, buy_vol REAL, sell_vol REAL, raw TEXT,
  PRIMARY KEY(ts, source, item));
CREATE TABLE opportunities(id TEXT PRIMARY KEY, ts TEXT, kind TEXT, path TEXT,
  margin_pct REAL, est_profit_c REAL, liq_score REAL, confidence TEXT, flags TEXT);
CREATE TABLE executions(id TEXT PRIMARY KEY, opp_id TEXT, ts TEXT,
  legs TEXT, realized_profit_c REAL, minutes REAL, notes TEXT);
""")

# ---------------------------------------- synthetic scanner opportunities
WHISPER = ("@Seller Hi, I'd like to buy your 100 Chaos Orb for my "
           "1 Divine Orb in Curse of the Allflame.")
EXCH = "Sell 1 divine for 105 exalted on the currency exchange"
OPPS = [
    {"id": "opp-low", "kind": "spread", "path": ["scarab: exch -> bulk"],
     "margin_pct": 5.1, "est_profit_c": 80, "est_profit_per_hour": 420,
     "liq_score": 0.5, "confidence": "low", "flags": ["price_fixing_suspect"],
     "actions": [{"type": "exchange",
                  "instruction": "Buy 40x Rusted Scarab at 2c each"}]},
    {"id": "opp-hot", "kind": "cycle",
     "path": ["chaos->divine", "divine->exalt", "exalt->chaos"],
     "margin_pct": 6.2, "est_profit_c": 140, "est_profit_per_hour": 900,
     "liq_score": 0.7, "confidence": "high", "flags": [],
     "actions": [{"type": "whisper", "text": WHISPER},
                 {"type": "exchange", "instruction": EXCH}]},
    {"id": "opp-mid", "kind": "cycle", "path": ["alt->fusing", "fusing->alt"],
     "margin_pct": 5.5, "est_profit_c": 60, "est_profit_per_hour": 600,
     "liq_score": 0.9, "confidence": "high", "flags": [],
     "actions": [{"type": "whisper", "text": "@Bulk hi, 200 alts pls"}]},
]
for o in OPPS:   # mirror them into the store the way the scanner would
    con.execute("INSERT INTO opportunities VALUES(?,?,?,?,?,?,?,?,?)",
                (o["id"], "2026-07-25T10:00:00", o["kind"],
                 json.dumps(o["path"]), o["margin_pct"], o["est_profit_c"],
                 o["liq_score"], o["confidence"], json.dumps(o["flags"])))
con.commit()
con.close()

# --------------------------------------------------------- injected IO
prints, copied = [], []
inputs = deque()
console = Console(
    db_path=DB,
    config={"league": "3.29", "alert_profit_per_hour_c": 700},
    input_fn=lambda prompt="": inputs.popleft(),
    print_fn=lambda *a, **k: prints.append(" ".join(str(x) for x in a)),
    clipboard_fn=lambda text: (copied.append(text), True)[1],
    refresh_fn=lambda: [dict(o) for o in OPPS],
    now_fn=lambda: datetime(2026, 7, 25, 12, 0, 0),
)

# ------------------------------------------------- render: ranked + flags
rows = console.refresh()
assert [r["id"] for r in rows] == ["opp-hot", "opp-mid", "opp-low"], \
    "ranked by est_profit_per_hour desc"
out = console.render(rows)
assert out.startswith("\a"), "terminal bell when a row beats the threshold"
assert "league=3.29" in out and "3 opportunities (0 dismissed)" in out
assert out.index("opp-hot") < out.index("opp-mid") < out.index("opp-low")
hot_line = [l for l in out.splitlines() if "opp-hot" in l][0]
mid_line = [l for l in out.splitlines() if "opp-mid" in l][0]
low_line = [l for l in out.splitlines() if "opp-low" in l][0]
assert hot_line.startswith(">>>"), "alert marker on the 900 c/h row"
assert mid_line.startswith("   ") and low_line.startswith("   "), \
    "600/420 c/h rows are below the 700 threshold"
assert "price_fixing_suspect" in low_line, "flags column rendered"
assert "6.2" in hot_line and "900" in hot_line and "140" in hot_line
assert "chaos->divine" in hot_line and "cycle" in hot_line
assert "high" in hot_line and "0.70" in hot_line

# ---------------------------------------------- c: copy legs to clipboard
assert console.handle("c 1") is True
assert copied == [WHISPER], "first leg of the top row -> injected clipboard"
assert console.handle("c 1") is True
assert copied == [WHISPER, EXCH], "second 'c' walks to the next leg"
assert console.handle("c 1") is True
assert copied[-1] == EXCH, "cursor clamps on the last leg"
assert console.handle("c 99") is True and len(copied) == 3, \
    "bad row number copies nothing"
assert console.handle("c") is True and len(copied) == 3
assert any("no row 99" in p for p in prints)

# --------------------------------------------------- j: journal the fill
inputs.extend(["150", "12.5", "two whispers, instant fill"])
assert console.handle("j 2") is True          # row 2 == opp-mid
db = sqlite3.connect(DB)
execs = db.execute("SELECT opp_id, legs, realized_profit_c, minutes, notes,"
                   " expected_profit_c, kind FROM executions").fetchall()
db.close()
assert len(execs) == 1
assert execs[0][0] == "opp-mid"
assert json.loads(execs[0][1]) == ["alt->fusing", "fusing->alt"]
assert execs[0][2] == 150.0 and execs[0][3] == 12.5
assert execs[0][4] == "two whispers, instant fill"
assert execs[0][5] == 60.0 and execs[0][6] == "cycle", \
    "journal snapshots est_profit_c/kind at fill time (rescans overwrite " \
    "the live opportunities row)"
inputs.extend(["not-a-number", "5"])
assert console.handle("j 1") is True, "bad input aborts, does not crash"
db = sqlite3.connect(DB)
assert db.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 1
db.close()

# --------------------------------------------------------- x: dismiss
assert console.handle("x 1") is True          # dismiss opp-hot
assert "opp-hot" not in console.render(console.rows)
assert [r["id"] for r in console.rows] == ["opp-mid", "opp-low"]
console.refresh()                             # fake scanner returns all 3
assert [r["id"] for r in console.rows] == ["opp-mid", "opp-low"], \
    "dismissed rows stay hidden across refresh"
out2 = console.render(console.rows)
assert "(1 dismissed)" in out2
assert not out2.startswith("\a"), "no bell once the hot row is dismissed"

# ------------------------------------------------- r / q / unknown / help
assert console.handle("r") is True
assert "MARKET CONSOLE" in prints[-1]
assert console.handle("h") is True and "commands:" in prints[-1]
assert console.handle("zzz") is True and "unknown command" in prints[-1]
assert console.handle("") is True
assert console.handle("q") is False, "q quits"

# --------------------------- ?: degrades to 'skipped' when the LLM is off
_prev_kill = os.environ.get("POE_TOOLS_LLM")
os.environ["POE_TOOLS_LLM"] = "off"           # kill switch: never call out
try:
    assert console.handle("? 1") is True, "anomaly explain never crashes"
finally:
    if _prev_kill is None:
        os.environ.pop("POE_TOOLS_LLM", None)
    else:
        os.environ["POE_TOOLS_LLM"] = _prev_kill
assert any("anomaly explain skipped" in p and "LLM disabled" in p
           for p in prints[-2:]), \
    "console '?' reaches brief.explain_opportunity and degrades cleanly"

# ----------------------------------- DB fallback loader (no scanner/fake)
plain = Console(db_path=DB, config={},
                input_fn=lambda p="": "", print_fn=lambda *a, **k: None,
                refresh_fn=None)
db_rows = plain._load_opportunities_from_db()
assert {r["id"] for r in db_rows} == {"opp-hot", "opp-mid", "opp-low"}
assert db_rows[0]["actions"] == [] and isinstance(db_rows[0]["path"], list)
assert _as_list('["a","b"]') == ["a", "b"] and _as_list(None) == []
assert load_config(os.path.join(tmp, "missing.json")) == {}

# --------------------- default refresh: real store + scanner seam (offline)
# A snapshot row with sell > buy on one source is a mispriced quote the
# scanner surfaces as an implied 2-leg cycle — the console's default
# refresh must find it via Store.latest_snapshots() + scanner.scan().
from market.store import Store                               # noqa: E402

seam_db = os.path.join(tmp, "seam.db")
seam_store = Store(seam_db)
seam_store.insert_snapshots([
    {"ts": "2026-07-25T10:00:00", "source": "poe.ninja", "league": "Mirage",
     "item": "Mirror Shard", "buy": 100.0, "sell": 130.0,
     "buy_vol": 50.0, "sell_vol": 45.0},
])
seam_store.close()
seam_prints = []
seam = Console(db_path=seam_db,
               config={"league": "Mirage", "haircut": 0.04,
                       "min_margin_pct": 5, "min_vol": 20,
                       "bankroll_c": 2000},
               input_fn=lambda p="": "",
               print_fn=lambda *a, **k: seam_prints.append(
                   " ".join(str(x) for x in a)),
               refresh_fn=None)
seam_rows = seam.refresh()
assert len(seam_rows) == 1 and seam_rows[0]["kind"] == "cycle"
assert seam_rows[0]["est_profit_per_hour"] > 0, "scanner pph preserved"
assert seam_rows[0]["actions"], "freshly scanned rows carry actions"
# computed opportunities are kept in memory: a later failing scan
# (db vanished) re-serves them instead of degrading to lossy DB rows
seam.db_path = os.path.join(tmp, "gone.db")
kept = seam.refresh()
assert [r["id"] for r in kept] == [seam_rows[0]["id"]]
assert kept[0]["actions"], "kept rows still carry actions"
assert any("keeping the last computed" in p for p in seam_prints)
# a console that never scanned falls back to the stored table (empty here)
cold = Console(db_path=os.path.join(tmp, "gone2.db"), config={},
               input_fn=lambda p="": "", print_fn=lambda *a, **k: None,
               refresh_fn=None)
assert cold.refresh() == []

# ------------------------------------------------------------- pnl math
db = sqlite3.connect(DB)
db.execute("DELETE FROM executions")
fills = [  # (id, opp_id, ts, legs, realized, minutes, notes) — legacy rows
    ("e1", "opp-hot", "2026-07-25T10:00:00", "[]", 100.0, 10.0, ""),
    ("e2", "opp-low", "2026-07-25T12:00:00", "[]", 50.0, 5.0, ""),
    ("e3", "opp-mid", "2026-07-26T09:00:00", "[]", -10.0, 15.0, "misread"),
    ("e4", "gone", "2026-07-26T11:00:00", "[]", 5.0, 2.0, "opp pruned"),
]
db.executemany("INSERT INTO executions(id, opp_id, ts, legs,"
               " realized_profit_c, minutes, notes) VALUES(?,?,?,?,?,?,?)",
               fills)
db.commit()
db.close()

agg = pnl.aggregate(pnl.load_rows(DB))
assert agg["total"] == {"realized": 145.0, "expected": 280.0,
                        "minutes": 32.0, "n": 4}
assert agg["by_day"]["2026-07-25"] == {"realized": 150.0, "expected": 220.0,
                                       "minutes": 15.0, "n": 2}
assert agg["by_day"]["2026-07-26"] == {"realized": -5.0, "expected": 60.0,
                                       "minutes": 17.0, "n": 2}
assert agg["by_kind"]["cycle"] == {"realized": 90.0, "expected": 200.0,
                                   "minutes": 25.0, "n": 2}, \
    "opp-hot (140) + opp-mid (60) expected; e4's kind is unknown"
assert agg["by_kind"]["spread"] == {"realized": 50.0, "expected": 80.0,
                                    "minutes": 5.0, "n": 1}
assert agg["by_kind"]["?"] == {"realized": 5.0, "expected": 0.0,
                               "minutes": 2.0, "n": 1}
assert agg["ratio"] == 140.0 / 280.0, "matched fills only (e4 excluded)"
assert agg["ratio_n"] == 3
assert agg["realized_per_hour"] == 145.0 / (32.0 / 60.0)

report = pnl.format_report(agg)
assert "4 fills journaled" in report
assert "2026-07-25" in report and "2026-07-26" in report
assert "cycle" in report and "spread" in report
assert "realized/expected = 0.50" in report
assert "raising" in report and "haircut" in report, \
    "0.50 ratio -> hint to raise the haircut"
assert "realized/h 271.9c" in report

assert pnl.aggregate([]) ["ratio"] is None
assert "No executions journaled yet" in pnl.format_report(pnl.aggregate([]))
empty_db = os.path.join(tmp, "empty.db")
sqlite3.connect(empty_db).close()
assert pnl.load_rows(empty_db) == [], "missing tables -> empty, no crash"
assert pnl.main(["--db", os.path.join(tmp, "nope.db")]) == 1

# journal-time snapshot beats the (rescanned, decayed) live opportunity row
snap_db = os.path.join(tmp, "snap.db")
scon = sqlite3.connect(snap_db)
scon.executescript("""
CREATE TABLE opportunities(id TEXT PRIMARY KEY, ts TEXT, kind TEXT, path TEXT,
  margin_pct REAL, est_profit_c REAL, liq_score REAL, confidence TEXT, flags TEXT);
CREATE TABLE executions(id TEXT PRIMARY KEY, opp_id TEXT, ts TEXT,
  legs TEXT, realized_profit_c REAL, minutes REAL, notes TEXT,
  expected_profit_c REAL, kind TEXT);
""")
scon.execute("INSERT INTO opportunities VALUES('opp','2026-07-25T10:00:00',"
             "'spread','[]',5.0,20.0,0.5,'high','[]')")  # decayed rescan
scon.execute("INSERT INTO executions VALUES('e','opp','2026-07-25T11:00:00',"
             "'[]',120.0,10.0,'',140.0,'cycle')")        # journal snapshot
scon.commit()
scon.close()
snap_rows = pnl.load_rows(snap_db)
assert snap_rows[0]["expected"] == 140.0 and snap_rows[0]["kind"] == "cycle", \
    "calibration uses the est_profit_c/kind seen at journal time"

shutil.rmtree(tmp)

print("ALL TESTS PASSED")
print(f"  console table line: {hot_line}")
print(f"  pnl ratio: {agg['ratio']:.2f} over {agg['ratio_n']} matched fills")
