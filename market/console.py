"""Execution console: plain-terminal market dashboard + trade journal.

Renders the scanner's ranked opportunities as a text table (Windows-first:
no curses, ASCII only, terminal bell for alerts) above a blocking one-line
command prompt:

    r        refresh: re-run the scanner over the latest snapshots
    c <row>  copy the next leg's whisper/instruction text to the clipboard
    j <row>  journal a fill (prompts realized profit + minutes -> executions)
    x <row>  dismiss the row for this session
    ? <row>  LLM anomaly explanation (advisory; degrades when LLM disabled)
    q        quit

ToS line (absolute): every send and every trade is a human action. This
console never touches the game, never sends whispers/messages, never talks
to any website's message functions. Copying pre-drafted text to the LOCAL
clipboard on an explicit keypress is the ceiling of what it does.

All IO is injected (input/print/clipboard callables) so the class is fully
testable headless. Import-safe: no side effects at import time; the
sibling market modules (scanner/store/brief) and llm.client are imported
lazily and everything degrades when they are absent.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(ROOT, "market", "market.db")
DEFAULT_CONFIG = os.path.join(ROOT, "market", "config.json")

# Console-local config key (not part of the scanner parameter set in
# market/config.json; absent -> this default). Chaos-per-hour threshold:
# rows at/above it ring the terminal bell and get a '>>>' marker.
DEFAULT_ALERT_PPH = 500.0

# Exact documented schema from docs/INTERFACES.md ("Market DB").
# expected_profit_c/kind snapshot the opportunity AS SEEN AT JOURNAL TIME:
# opportunity ids are stable per path and later rescans overwrite
# est_profit_c, so tools/pnl.py must not calibrate against the live row.
_EXECUTIONS_SQL = (
    "CREATE TABLE IF NOT EXISTS executions(id TEXT PRIMARY KEY, opp_id TEXT,"
    " ts TEXT,\n  legs TEXT, realized_profit_c REAL, minutes REAL, notes TEXT,"
    "\n  expected_profit_c REAL, kind TEXT)"
)
# Additive migration for executions tables created before the snapshot
# columns existed (old DBs / market.store's base schema).
_EXECUTIONS_MIGRATIONS = (
    "ALTER TABLE executions ADD COLUMN expected_profit_c REAL",
    "ALTER TABLE executions ADD COLUMN kind TEXT",
)

_HELP = ("commands: r=refresh  c <n>=copy next leg  j <n>=journal fill  "
         "x <n>=dismiss  ? <n>=explain  q=quit")


def load_config(path: str | None = None) -> dict:
    """Best-effort read of market/config.json (owned by market-store)."""
    p = path or DEFAULT_CONFIG
    try:
        with open(p, encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg if isinstance(cfg, dict) else {}
    except (OSError, ValueError):
        return {}


def _as_list(value) -> list:
    """Decode a DB TEXT column that holds a JSON list (or fall back)."""
    if not value:
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else [str(parsed)]
    except ValueError:
        return [s for s in str(value).split(",") if s]


class Console:
    """Terminal dashboard over the scanner's opportunity list.

    render(rows) -> str is pure string building (plus remembering the
    displayed order so row numbers in commands match what is on screen);
    handle(cmd) -> bool executes one command and returns False on quit.
    """

    def __init__(self, db_path: str = DEFAULT_DB, config: dict | None = None,
                 input_fn=input, print_fn=print, clipboard_fn=None,
                 refresh_fn=None, now_fn=datetime.now):
        self.db_path = db_path
        self.config = config or {}
        self.league = str(self.config.get("league", "?"))
        self.alert_pph = float(
            self.config.get("alert_profit_per_hour_c", DEFAULT_ALERT_PPH))
        self.input_fn = input_fn
        self.print_fn = print_fn
        self.clipboard_fn = clipboard_fn or self._default_clipboard
        self.refresh_fn = refresh_fn          # tests inject a fake scanner
        self.now_fn = now_fn
        self.rows: list[dict] = []            # currently displayed, ranked
        self.dismissed: set[str] = set()      # opp ids hidden this session
        self._leg_cursor: dict[str, int] = {}  # opp id -> next action index
        self._computed: list[dict] = []       # last scanner output, kept
        #   in memory so rows retain actions/est_profit_per_hour even when
        #   a later refresh has to fall back to the (lossy) DB table.

    # ------------------------------------------------------------ data
    def refresh(self) -> list[dict]:
        """Re-run the scanner (or the injected fake), rank, filter, store."""
        fn = self.refresh_fn or self._default_refresh
        try:
            rows = fn() or []
        except Exception as e:                        # never crash the loop
            self.print_fn(f"refresh failed: {e}")
            rows = self.rows
        rows = [r for r in rows if r.get("id") not in self.dismissed]
        rows.sort(key=lambda r: (float(r.get("est_profit_per_hour") or 0.0),
                                 float(r.get("est_profit_c") or 0.0)),
                  reverse=True)
        self.rows = rows
        return rows

    def _default_refresh(self) -> list[dict]:
        """Re-run market.scanner.scan over the latest snapshots in the store.

        Real APIs (aligned 2026-07-07): market.store.Store(db).
        latest_snapshots() feeds market.scanner.scan(rows, params) with
        this console's config as the parameter dict.  The freshly
        computed opportunities (which carry actions and
        est_profit_per_hour, unlike the DB table) are kept in memory; a
        later failing scan re-serves them, and only a console that never
        scanned falls back to the lossy opportunities table.
        """
        try:
            try:
                from market.scanner import scan
                from market.store import Store
            except ImportError:  # executed as a script: sys.path[0]=market/
                from scanner import scan          # type: ignore
                from store import Store           # type: ignore
            if not os.path.exists(self.db_path):
                raise FileNotFoundError(f"no snapshot db at {self.db_path}")
            store = Store(self.db_path)
            try:
                snapshots = store.latest_snapshots()
            finally:
                store.close()
            self._computed = scan(snapshots, self.config)
            return list(self._computed)
        except Exception as e:                    # never crash the loop
            if self._computed:
                self.print_fn(f"scan failed ({e}); keeping the last "
                              "computed opportunities")
                return list(self._computed)
            self.print_fn(f"scan failed ({e}); "
                          "falling back to stored opportunities")
            return self._load_opportunities_from_db()

    def _load_opportunities_from_db(self) -> list[dict]:
        """Read the opportunities table (docs/INTERFACES.md schema).

        The table has no est_profit_per_hour / actions columns, so rows
        loaded this way rank by est_profit_c and have nothing to copy.
        """
        if not os.path.exists(self.db_path):
            return []
        con = sqlite3.connect(self.db_path)
        try:
            try:
                cur = con.execute(
                    "SELECT id, ts, kind, path, margin_pct, est_profit_c,"
                    " liq_score, confidence, flags FROM opportunities")
            except sqlite3.OperationalError:
                return []
            return [{"id": r[0], "ts": r[1], "kind": r[2] or "?",
                     "path": _as_list(r[3]),
                     "margin_pct": r[4] or 0.0,
                     "est_profit_c": r[5] or 0.0,
                     "est_profit_per_hour": 0.0,
                     "liq_score": r[6] or 0.0,
                     "confidence": r[7] or "?",
                     "flags": _as_list(r[8]),
                     "actions": []} for r in cur]
        finally:
            con.close()

    # ---------------------------------------------------------- render
    def render(self, rows: list[dict]) -> str:
        """Status header + ranked table + command footer, as one string.

        Rows at/above the alert threshold get a '>>>' marker and the
        whole render is prefixed with the terminal bell.
        """
        self.rows = list(rows)                 # row numbers == screen order
        ts = self.now_fn().strftime("%Y-%m-%d %H:%M:%S")
        head = (f"MARKET CONSOLE  league={self.league}  "
                f"{len(rows)} opportunities ({len(self.dismissed)} dismissed)"
                f"  alert >= {self.alert_pph:.0f} c/h  {ts}")
        lines = [head, "-" * len(head)]
        if not rows:
            lines.append("(no opportunities - press r to rescan)")
        else:
            lines.append(f"     # {'id':<8} {'kind':<6} {'path':<40} "
                         f"{'margin%':>7} {'c/h':>6} {'est_c':>6} "
                         f"{'liq':>4} {'conf':<4} flags")
            alert = False
            for i, r in enumerate(rows, 1):
                pph = float(r.get("est_profit_per_hour") or 0.0)
                hot = pph >= self.alert_pph
                alert = alert or hot
                path = " | ".join(str(p) for p in (r.get("path") or []))
                flags = ",".join(r.get("flags") or []) or "-"
                lines.append(
                    f"{'>>>' if hot else '   '} {i:>2} "
                    f"{str(r.get('id', '?'))[:8]:<8} "
                    f"{str(r.get('kind', '?'))[:6]:<6} "
                    f"{path[:40]:<40} "
                    f"{float(r.get('margin_pct') or 0.0):>7.1f} "
                    f"{pph:>6.0f} "
                    f"{float(r.get('est_profit_c') or 0.0):>6.0f} "
                    f"{float(r.get('liq_score') or 0.0):>4.2f} "
                    f"{str(r.get('confidence', '?'))[:4]:<4} "
                    f"{flags}")
            if alert:
                lines[0] = "\a" + lines[0]     # terminal bell, no toast dep
        lines.append(_HELP)
        return "\n".join(lines)

    # ---------------------------------------------------------- commands
    def handle(self, cmd: str) -> bool:
        """Execute one command line. Returns False when the user quits."""
        parts = (cmd or "").strip().split(maxsplit=1)
        if not parts:
            return True
        op = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        if op == "q":
            return False
        if op == "r":
            self.refresh()
            self.print_fn(self.render(self.rows))
            return True
        if op in ("h", "help"):
            self.print_fn(_HELP)
            return True
        if op in ("c", "j", "x", "?"):
            opp = self._row_arg(op, arg)
            if opp is not None:
                if op == "c":
                    self._copy(opp)
                elif op == "j":
                    self._journal(opp)
                elif op == "x":
                    self._dismiss(opp)
                else:
                    self._explain(opp)
            return True
        self.print_fn(f"unknown command '{op}' - {_HELP}")
        return True

    def _row_arg(self, op: str, arg: str) -> dict | None:
        try:
            n = int(arg)
        except ValueError:
            self.print_fn(f"usage: {op} <row>")
            return None
        if not 1 <= n <= len(self.rows):
            self.print_fn(f"no row {n} (1..{len(self.rows)})")
            return None
        return self.rows[n - 1]

    # -------------------------------------------------------- c: copy leg
    def _copy(self, opp: dict) -> None:
        """Copy the next leg's whisper/instruction to the LOCAL clipboard.

        Explicit human keypress -> local clipboard. Nothing is ever sent
        anywhere; the human pastes and presses Enter in-game themselves.
        Repeated 'c' on the same row walks through the legs (clamping on
        the last one); journaling the fill resets the walk.
        """
        actions = opp.get("actions") or []
        if not actions:
            self.print_fn("no whisper/instruction text on this row "
                          "(stored rows carry no actions - press r)")
            return
        oid = str(opp.get("id", ""))
        i = min(self._leg_cursor.get(oid, 0), len(actions) - 1)
        action = actions[i]
        text = action.get("text") or action.get("instruction") or ""
        ok = self.clipboard_fn(text)
        self._leg_cursor[oid] = min(i + 1, len(actions) - 1)
        self.print_fn(f"leg {i + 1}/{len(actions)} "
                      f"({action.get('type', '?')}) -> "
                      + ("copied to clipboard - paste it yourself"
                         if ok else "printed above for manual copy"))

    def _default_clipboard(self, text: str) -> bool:
        if sys.platform.startswith("win"):
            # VERIFY: Windows 'clip' reads stdin and fills the clipboard
            # (documented Win10/11 behavior; not testable on this Mac).
            # clip.exe only treats stdin as Unicode when it starts with a
            # UTF-16LE BOM (otherwise it decodes in the OEM codepage and
            # non-ASCII item names become mojibake) — Python's "utf-16"
            # codec emits exactly that BOM.
            cmd, payload = ["clip"], text.encode("utf-16")
        elif sys.platform == "darwin":
            # live-verified: round-trips via pbpaste
            cmd, payload = ["pbcopy"], text.encode("utf-8")
        else:
            cmd, payload = None, b""
        if cmd is not None:
            try:
                subprocess.run(cmd, input=payload, check=True, shell=False)
                return True
            except Exception:
                pass
        self.print_fn("--- copy manually ---\n" + text
                      + "\n---------------------")
        return False

    # ------------------------------------------------------ j: journal
    def _journal(self, opp: dict) -> None:
        """Prompt for the fill's realized numbers -> executions table."""
        raw_profit = self.input_fn("realized profit (chaos): ")
        raw_minutes = self.input_fn("minutes spent: ")
        try:
            profit = float(raw_profit)
            minutes = float(raw_minutes)
        except ValueError:
            self.print_fn("need numbers - journal entry aborted")
            return
        notes = self.input_fn("notes (optional): ") or ""
        expected = opp.get("est_profit_c")
        row = (uuid.uuid4().hex, str(opp.get("id", "")),
               self.now_fn().isoformat(timespec="seconds"),
               json.dumps(opp.get("path") or []), profit, minutes, notes,
               float(expected) if expected is not None else None,
               str(opp.get("kind")) if opp.get("kind") else None)
        con = sqlite3.connect(self.db_path)
        try:
            con.execute(_EXECUTIONS_SQL)
            for stmt in _EXECUTIONS_MIGRATIONS:
                try:
                    con.execute(stmt)
                except sqlite3.OperationalError:
                    pass                      # column already exists
            con.execute("INSERT INTO executions VALUES(?,?,?,?,?,?,?,?,?)",
                        row)
            con.commit()
        finally:
            con.close()
        self._leg_cursor.pop(str(opp.get("id", "")), None)
        self.print_fn(f"journaled: {profit:+.1f}c in {minutes:g} min "
                      f"on {opp.get('id')} (tools/pnl.py reports on these)")

    # ------------------------------------------------------ x: dismiss
    def _dismiss(self, opp: dict) -> None:
        oid = str(opp.get("id", ""))
        self.dismissed.add(oid)
        self.rows = [r for r in self.rows if str(r.get("id", "")) != oid]
        self.print_fn(self.render(self.rows))

    # ------------------------------------------------------ ?: explain
    def _explain(self, opp: dict) -> None:
        """Advisory LLM anomaly explanation via market/brief.py (task 26).

        Degrades to a one-line notice when the LLM is disabled or the
        brief module isn't built yet; never changes scanner output.
        """
        try:
            from llm.client import LLMDisabled  # documented shared API
        except Exception:                       # llm client not built yet
            class LLMDisabled(RuntimeError):
                pass
        try:
            try:
                import market.brief as brief
            except ImportError:  # executed as a script: sys.path[0]=market/
                import brief                      # type: ignore
            self.print_fn(str(brief.explain_opportunity(
                opp, db_path=self.db_path)))
        except LLMDisabled:
            self.print_fn("anomaly explain skipped: LLM disabled "
                          "(scanner output is unaffected)")
        except Exception as e:
            self.print_fn(f"anomaly explain unavailable: {e}")

    # ---------------------------------------------------------- loop
    def run(self) -> None:
        """Blocking interactive loop: render once, then prompt commands."""
        self.refresh()
        self.print_fn(self.render(self.rows))
        while True:
            try:
                cmd = self.input_fn("market> ")
            except (EOFError, KeyboardInterrupt):
                self.print_fn("")
                break
            if not self.handle(cmd):
                break


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Market execution console (terminal dashboard + "
                    "trade journal). Read-only market data in; every "
                    "trade action is a human keypress.")
    ap.add_argument("--db", default=DEFAULT_DB, help="path to market.db")
    ap.add_argument("--config", default=DEFAULT_CONFIG,
                    help="path to market/config.json")
    ap.add_argument("--alert", type=float, default=None,
                    help="alert threshold in chaos/hour (bell + '>>>')")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    if args.alert is not None:
        cfg["alert_profit_per_hour_c"] = args.alert
    Console(db_path=args.db, config=cfg).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
