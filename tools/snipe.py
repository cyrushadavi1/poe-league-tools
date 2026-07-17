#!/usr/bin/env python3
"""snipe — arm live trade searches and alert the human on new listings.

    export POESESSID=...          # your logged-in session cookie
    python tools/snipe.py --search-id AbCdEf --search-id XyZ123
    python tools/snipe.py --query taryns.json --label "Taryn's" --open

Sources of searches (mix freely, at least one required):
  --search-id ID      a search you saved on pathofexile.com/trade
                      (build it in the site UI, copy the id from the URL)
  --query FILE.json   a trade-API query JSON (tools/tradeq.py emits these);
                      snipe POSTs it once to obtain a search id

Per alert: one console line + terminal bell. Options:
  --open              open the search results page in the browser ONCE per
                      alert burst, so the listing is one click from Buy
  --copy-whisper      put the listing's whisper text on the clipboard
                      (pbcopy/clip.exe); YOU paste and send it in game
  --log FILE.jsonl    append every alert as one JSON line
  --probe             connect, print the first raw frame per search, exit
                      (rehearsal verification of the VERIFY notes)

ToS: this tool never buys, never sends whispers or messages, never touches
the game client. Every alert requires a human to act. Keep the number of
armed searches small — the site limits concurrent live searches per
account, and getting rate-banned at league start is worse than missing a
snipe.

Degrades: without the optional websocket-client package or a POESESSID
this exits immediately with instructions (nothing armed, nothing sent).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import webbrowser

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from market import livesearch                          # noqa: E402
from market.livesearch import (                        # noqa: E402
    Alert, LiveSearchMonitor, LiveSearchUnavailable, SearchSpec,
    create_search, results_url,
)

MARKET_CONFIG_PATH = os.path.join(ROOT, "market", "config.json")
OPEN_COOLDOWN_S = 10.0        # browser-open at most once per burst


def default_league(path: str = MARKET_CONFIG_PATH) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            league = (json.load(f) or {}).get("league", "")
        if league:
            return str(league)
    except (OSError, ValueError):
        pass
    return "Standard"


def copy_clipboard(text: str) -> bool:
    """Best-effort clipboard copy (pbcopy on Mac, clip on the PC)."""
    cmd = ["pbcopy"] if sys.platform == "darwin" else ["clip"]
    try:
        subprocess.run(cmd, input=text.encode("utf-8"), check=True,
                       timeout=5)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


class AlertSink:
    """Console/bell/log/browser handling for alerts (thread-safe)."""

    def __init__(self, *, open_browser=False, copy_whisper=False,
                 log_path=None, out=sys.stdout, opener=webbrowser.open,
                 clock=time.monotonic):
        self.open_browser = open_browser
        self.copy_whisper = copy_whisper
        self.log_path = log_path
        self.out = out
        self.opener = opener
        self._clock = clock
        self._lock = threading.Lock()
        self._last_open = 0.0
        self.count = 0

    def __call__(self, alert: Alert):
        with self._lock:
            self.count += 1
            stamp = time.strftime("%H:%M:%S")
            print(f"\a{stamp} {alert.line()}", file=self.out, flush=True)
            if alert.whisper:
                if self.copy_whisper and copy_clipboard(alert.whisper):
                    print("         whisper copied — paste it in game "
                          "yourself", file=self.out, flush=True)
                else:
                    print(f"         whisper: {alert.whisper}",
                          file=self.out, flush=True)
            else:
                print(f"         no whisper on listing (async buyout?) — "
                      f"buy via {alert.results_url}",
                      file=self.out, flush=True)
            if self.log_path:
                try:
                    with open(self.log_path, "a", encoding="utf-8") as f:
                        f.write(json.dumps(vars(alert)) + "\n")
                except OSError:
                    pass
            now = self._clock()
            if (self.open_browser
                    and now - self._last_open >= OPEN_COOLDOWN_S):
                self._last_open = now
                try:
                    self.opener(alert.results_url)
                except Exception:
                    pass


def probe(specs, monitor: LiveSearchMonitor, out=sys.stdout) -> int:
    """Connect to each search once and print the first raw frame."""
    for spec in specs:
        url = monitor.ws_url(spec)
        print(f"[{spec.label}] connecting {url}", file=out)
        try:
            conn = monitor._connector(url)
        except Exception as exc:
            print(f"[{spec.label}] FAILED: {exc}", file=out)
            return 1
        try:
            frame = conn.recv()
            print(f"[{spec.label}] first frame: {frame!r}", file=out)
        except Exception as exc:
            print(f"[{spec.label}] connected; recv failed: {exc}",
                  file=out)
        finally:
            try:
                conn.close()
            except Exception:
                pass
    return 0


def build_specs(args, league: str) -> list[SearchSpec]:
    specs = [SearchSpec(sid) for sid in args.search_ids or []]
    for path in args.queries or []:
        with open(path, encoding="utf-8") as f:
            query_obj = json.load(f)
        sid = create_search(query_obj, league)
        label = os.path.splitext(os.path.basename(path))[0]
        specs.append(SearchSpec(sid, label))
    labels = args.labels or []
    for spec, label in zip(specs, labels):
        spec.label = label
    return specs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--search-id", dest="search_ids", action="append",
                    help="existing trade-site search id (repeatable)")
    ap.add_argument("--query", dest="queries", action="append",
                    help="trade-API query JSON file (repeatable)")
    ap.add_argument("--label", dest="labels", action="append",
                    help="label for the Nth search (repeatable)")
    ap.add_argument("--league", default=None)
    ap.add_argument("--open", action="store_true",
                    help="open the results page on alert (rate-limited)")
    ap.add_argument("--copy-whisper", action="store_true",
                    help="copy the whisper to the clipboard on alert")
    ap.add_argument("--log", default=None, help="append alerts as JSONL")
    ap.add_argument("--probe", action="store_true",
                    help="connect once per search, print raw frame, exit")
    args = ap.parse_args(argv)

    league = args.league or default_league()
    session_id = os.environ.get("POESESSID", "").strip()

    try:
        specs = build_specs(args, league)
        if not specs:
            ap.error("nothing to watch: pass --search-id and/or --query")
        sink = AlertSink(open_browser=args.open,
                         copy_whisper=args.copy_whisper,
                         log_path=args.log)
        monitor = LiveSearchMonitor(specs, league, session_id, sink)
        if args.probe:
            return probe(specs, monitor)
        # Fail fast on missing transport/auth before starting threads.
        if not session_id:
            raise LiveSearchUnavailable(
                "set the POESESSID environment variable (your logged-in "
                "session cookie from pathofexile.com)")
        try:
            import websocket  # noqa: F401  (optional transport)
        except ImportError:
            raise LiveSearchUnavailable(
                "pip install websocket-client (optional dependency for "
                "live monitoring)") from None
        for spec in specs:
            print(f"armed [{spec.label}] -> "
                  f"{results_url(league, spec.search_id)}")
        print(f"watching {len(specs)} search(es) on {league} — Ctrl+C "
              "to stop. Every buy/whisper is YOUR action.")
        monitor.run()
        print(f"\nstopped; {sink.count} alert(s) this session")
        return 0
    except LiveSearchUnavailable as exc:
        print(f"live search unavailable: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
