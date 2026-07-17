"""Live trade-search monitor: official trade-site live search -> alerts.

Watches one or more saved trade-site searches over the official live-search
WebSocket and turns new listings into Alert objects the moment they are
indexed. The human acts on every alert — this module NEVER buys, never
sends whispers or messages, never touches the game client. With Merchant's
Tabs (async buyout, 3.27+) the intended flow is: alert -> human opens the
listing -> human clicks buy. That keeps every trade a human action, per
the project invariant (see DECISIONS.md).

Protocol (structured from the trade site's own network traffic; each
VERIFY below is on the Mirage-rehearsal checklist):

  POST https://www.pathofexile.com/api/trade/search/<league>
      body = query JSON (tools/tradeq.py emits this) -> {"id": ...}
      (no auth needed; same endpoint tradeq --post uses, live-verified
      2026-07-07)
  WSS  wss://www.pathofexile.com/api/trade/live/<league>/<search-id>
      requires a logged-in session: Cookie POESESSID=..., Origin header.
      Messages are JSON; new listings arrive as {"new": [<result-id>...]}.
      VERIFY: exact message shapes and server ping cadence.
  GET  https://www.pathofexile.com/api/trade/fetch/<ids>?query=<search-id>
      at most 10 ids per request -> {"result": [{listing, item}, ...]}.
      VERIFY: whether Merchant's Tab buyout listings carry a marker
      (listing.method / price.type) distinguishing them from whisper
      listings.

Auth: the POESESSID session cookie is read from the POESESSID environment
variable by the CLI (tools/snipe.py). It is never written to disk.

Rate limits: fetches share a global 1 s floor and honor Retry-After and
X-Rate-Limit-* headers (market/ratelimit.py). WebSocket connections are
long-lived (one per search, keep the count small — the site itself caps
concurrent live searches).

Degrades: the WebSocket transport needs the optional `websocket-client`
package (pip install websocket-client). Everything importable here is
stdlib; constructing a monitor without the package and without an
injected connector raises LiveSearchUnavailable with instructions.
Tests inject fake connectors/fetchers and never touch the network.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from market.ratelimit import bucket_deadline, retry_after_seconds

USER_AGENT = "poe-league-tools/1.0 (contact: cyrus@hadavi.net)"
SITE = "https://www.pathofexile.com"
WS_SITE = "wss://www.pathofexile.com"
FETCH_CHUNK = 10          # trade API fetch accepts at most 10 ids
FETCH_FLOOR_S = 1.0       # global floor between fetch GETs
BACKOFF_START_S = 2.0     # reconnect backoff, doubles up to the cap
BACKOFF_CAP_S = 60.0
RECV_TIMEOUT_S = 90.0     # no frame (server pings ~30 s) -> reconnect
MAX_SEEN_IDS = 4096       # per-search dedupe window

_urlopen = urllib.request.urlopen   # monkeypatch point for tests


class LiveSearchUnavailable(RuntimeError):
    """WebSocket transport missing or not authenticated; caller degrades."""


@dataclass
class SearchSpec:
    """One armed live search."""
    search_id: str
    label: str = ""

    def __post_init__(self):
        if not self.label:
            self.label = self.search_id


@dataclass
class Alert:
    """One newly indexed listing, ready to show a human."""
    label: str
    search_id: str
    listing_id: str
    item_name: str
    price_amount: float | None
    price_currency: str
    price_type: str          # e.g. "~b/o"; VERIFY Merchant's Tab marker
    account: str
    character: str
    whisper: str             # "" when the listing has none (async buyout)
    indexed: str
    results_url: str

    def line(self) -> str:
        price = ("unpriced" if self.price_amount is None else
                 f"{self.price_amount:g} {self.price_currency}")
        return f"[{self.label}] {self.item_name} — {price} — {self.account}"


def results_url(league: str, search_id: str) -> str:
    return f"{SITE}/trade/search/{urllib.parse.quote(league)}/{search_id}"


def parse_ws_message(text) -> list[str]:
    """One WebSocket frame -> new result ids ([] for anything else)."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    ids = data.get("new")
    if not isinstance(ids, list):
        return []
    return [str(i) for i in ids if isinstance(i, (str, int))]


def parse_fetch_response(payload, spec: SearchSpec, url: str) -> list[Alert]:
    """fetch endpoint JSON -> Alerts (defensively; fields may be absent)."""
    alerts = []
    if not isinstance(payload, dict):
        return alerts
    for entry in payload.get("result") or []:
        if not isinstance(entry, dict):
            continue
        listing = entry.get("listing") or {}
        item = entry.get("item") or {}
        account = listing.get("account") or {}
        price = listing.get("price") or {}
        name = " ".join(x for x in (item.get("name"), item.get("typeLine"))
                        if x) or "?"
        try:
            amount = float(price["amount"])
        except (KeyError, TypeError, ValueError):
            amount = None
        alerts.append(Alert(
            label=spec.label,
            search_id=spec.search_id,
            listing_id=str(entry.get("id", "")),
            item_name=name,
            price_amount=amount,
            price_currency=str(price.get("currency", "")),
            price_type=str(price.get("type", "")),
            account=str(account.get("name", "?")),
            character=str(account.get("lastCharacterName", "")),
            whisper=str(listing.get("whisper", "") or ""),
            indexed=str(listing.get("indexed", "")),
            results_url=url,
        ))
    return alerts


def create_search(query_obj: dict, league: str) -> str:
    """POST a query to the trade API and return the search id.

    Read-only from a trading standpoint (creates a saved search, exactly
    what pressing Search on the website does). No auth required.
    """
    url = f"{SITE}/api/trade/search/{urllib.parse.quote(league)}"
    req = urllib.request.Request(
        url, data=json.dumps(query_obj).encode("utf-8"),
        headers={"User-Agent": USER_AGENT,
                 "Content-Type": "application/json"})
    with _urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    search_id = data.get("id") if isinstance(data, dict) else None
    if not search_id:
        raise LiveSearchUnavailable(
            f"search POST returned no id (keys: "
            f"{sorted(data) if isinstance(data, dict) else type(data).__name__})")
    return str(search_id)


class Fetcher:
    """Rate-limited fetch client for listing details (thread-shared)."""

    def __init__(self, league: str, session_id: str = "",
                 clock=time.monotonic, sleep=time.sleep):
        self.league = league
        self.session_id = session_id
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def _wait_turn(self):
        with self._lock:
            now = self._clock()
            delay = self._next_allowed - now
            if delay > 0:
                self._sleep(delay)
                now = self._clock()
            self._next_allowed = now + FETCH_FLOOR_S

    def _note_headers(self, headers):
        with self._lock:
            self._next_allowed = max(
                self._next_allowed,
                bucket_deadline(headers, self._clock()))

    def __call__(self, ids: list[str], spec: SearchSpec) -> list[Alert]:
        out: list[Alert] = []
        rurl = results_url(self.league, spec.search_id)
        for i in range(0, len(ids), FETCH_CHUNK):
            chunk = ids[i:i + FETCH_CHUNK]
            self._wait_turn()
            url = (f"{SITE}/api/trade/fetch/{','.join(chunk)}"
                   f"?query={spec.search_id}")
            headers = {"User-Agent": USER_AGENT}
            if self.session_id:
                headers["Cookie"] = f"POESESSID={self.session_id}"
            req = urllib.request.Request(url, headers=headers)
            try:
                with _urlopen(req, timeout=30) as resp:
                    self._note_headers(getattr(resp, "headers", None))
                    payload = json.load(resp)
            except urllib.error.HTTPError as exc:      # noqa: PERF203
                self._note_headers(getattr(exc, "headers", None))
                if exc.code == 429:
                    with self._lock:
                        self._next_allowed = max(
                            self._next_allowed,
                            self._clock() + retry_after_seconds(
                                (exc.headers or {}).get("Retry-After")))
                continue                                # drop chunk, carry on
            except (urllib.error.URLError, OSError, ValueError):
                continue
            out.extend(parse_fetch_response(payload, spec, rurl))
        return out


def default_connector(url: str, session_id: str):
    """Open the live-search WebSocket via the optional websocket-client.

    Returns an object with .recv() -> str and .close(). recv raising
    socket.timeout / WebSocketTimeoutException means "no frame in
    RECV_TIMEOUT_S" and the monitor reconnects.
    """
    try:
        import websocket  # type: ignore  # optional: websocket-client
    except ImportError as exc:
        raise LiveSearchUnavailable(
            "live monitoring needs the optional websocket-client package: "
            ".venv/bin/pip install websocket-client (on the PC: "
            "python -m pip install websocket-client)") from exc
    if not session_id:
        raise LiveSearchUnavailable(
            "live search requires a logged-in session: set the POESESSID "
            "environment variable (browser dev tools -> Cookies)")
    return websocket.create_connection(
        url, timeout=RECV_TIMEOUT_S,
        header={"User-Agent": USER_AGENT, "Origin": SITE},
        cookie=f"POESESSID={session_id}")


class LiveSearchMonitor:
    """Run one thread per armed search; call on_alert for each listing.

    on_alert runs on monitor threads — keep it fast (print/log/beep).
    connector/fetcher/sleep are injectable for offline tests.
    """

    def __init__(self, specs: list[SearchSpec], league: str,
                 session_id: str, on_alert,
                 connector=None, fetcher=None, sleep=time.sleep,
                 backoff_start: float = BACKOFF_START_S,
                 backoff_cap: float = BACKOFF_CAP_S):
        if not specs:
            raise ValueError("no searches to monitor")
        self.specs = specs
        self.league = league
        self.session_id = session_id
        self.on_alert = on_alert
        self._connector = connector or (
            lambda url: default_connector(url, session_id))
        self._fetch = fetcher or Fetcher(league, session_id)
        self._sleep = sleep
        self._backoff_start = backoff_start
        self._backoff_cap = backoff_cap
        self.stop = threading.Event()
        self._seen: dict[str, dict] = {s.search_id: {} for s in specs}

    def _fresh(self, spec: SearchSpec, ids: list[str]) -> list[str]:
        seen = self._seen[spec.search_id]
        fresh = []
        for i in ids:
            if i in seen:
                continue
            seen[i] = None
            fresh.append(i)
        while len(seen) > MAX_SEEN_IDS:                 # bounded, FIFO-ish
            seen.pop(next(iter(seen)))
        return fresh

    def ws_url(self, spec: SearchSpec) -> str:
        return (f"{WS_SITE}/api/trade/live/"
                f"{urllib.parse.quote(self.league)}/{spec.search_id}")

    def run_search(self, spec: SearchSpec, max_connects: int | None = None):
        """Connect/reconnect loop for one search (blocking)."""
        backoff = self._backoff_start
        connects = 0
        while not self.stop.is_set():
            if max_connects is not None and connects >= max_connects:
                return
            connects += 1
            try:
                conn = self._connector(self.ws_url(spec))
            except LiveSearchUnavailable:
                raise
            except Exception:
                self._sleep(backoff)
                backoff = min(self._backoff_cap, backoff * 2)
                continue
            try:
                while not self.stop.is_set():
                    frame = conn.recv()
                    if frame is None:
                        break                            # server closed
                    backoff = self._backoff_start        # healthy link
                    ids = self._fresh(spec, parse_ws_message(frame))
                    if not ids:
                        continue
                    for alert in self._fetch(ids, spec):
                        self.on_alert(alert)
            except Exception:
                pass                                     # fall through: retry
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
            if not self.stop.is_set():
                self._sleep(backoff)
                backoff = min(self._backoff_cap, backoff * 2)

    def run(self):
        """Monitor all searches until KeyboardInterrupt / stop is set."""
        threads = [threading.Thread(target=self.run_search, args=(s,),
                                    daemon=True, name=f"live:{s.label}")
                   for s in self.specs]
        for t in threads:
            t.start()
        try:
            while any(t.is_alive() for t in threads):
                for t in threads:
                    t.join(timeout=0.5)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop.set()
