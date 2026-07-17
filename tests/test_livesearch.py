"""Headless tests for market/livesearch.py + tools/snipe.py.

Offline by construction: connectors, fetchers, urlopen, clipboard and
browser are all faked; no network, no Qt, no websocket-client needed.
"""
import io
import json
import os
import sys
import tempfile
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "tools"), ROOT]

from market import livesearch, ratelimit                    # noqa: E402
from market.livesearch import (                             # noqa: E402
    Alert, Fetcher, LiveSearchMonitor, LiveSearchUnavailable, SearchSpec,
    create_search, parse_fetch_response, parse_ws_message, results_url,
)
import snipe                                                # noqa: E402

FIXTURE = os.path.join(ROOT, "tests", "fixtures_market",
                       "livesearch_fetch.json")


# ---------------------------------------------------------------- parsing
def test_parse_ws_message():
    assert parse_ws_message('{"new": ["a", "b", 3]}') == ["a", "b", "3"]
    assert parse_ws_message('{"auth": true}') == []
    assert parse_ws_message('{"new": "not-a-list"}') == []
    assert parse_ws_message("not json") == []
    assert parse_ws_message(None) == []
    assert parse_ws_message("[1,2]") == []


def test_parse_fetch_response_fixture():
    with open(FIXTURE, encoding="utf-8") as f:
        payload = json.load(f)
    spec = SearchSpec("s1", "taryns")
    alerts = parse_fetch_response(payload, spec, "https://x/results")
    assert len(alerts) == 3
    a = alerts[0]
    assert a.item_name == "Taryn's Shiver Maelström Staff"
    assert a.price_amount == 2 and a.price_currency == "chaos"
    assert a.price_type == "~b/o"
    assert a.account.startswith("seller_account")
    assert "I would like to buy" in a.whisper
    assert a.label == "taryns" and a.search_id == "s1"
    assert "2 chaos" in a.line()
    b = alerts[1]                       # unpriced, no whisper
    assert b.price_amount is None and b.whisper == ""
    assert b.item_name == "Pearlescent Amulet"
    assert "unpriced" in b.line()
    c = alerts[2]                       # degenerate entry tolerated
    assert c.item_name == "?" and c.listing_id == "bad-entry-tolerated"
    assert parse_fetch_response("junk", spec, "u") == []
    assert parse_fetch_response({"result": None}, spec, "u") == []


def test_results_and_ws_urls():
    assert results_url("Curse of the Allflame", "aBc") == (
        "https://www.pathofexile.com/trade/search/"
        "Curse%20of%20the%20Allflame/aBc")
    mon = LiveSearchMonitor([SearchSpec("aBc")], "My League", "sid",
                            on_alert=lambda a: None,
                            connector=lambda url: None)
    assert mon.ws_url(SearchSpec("aBc")) == (
        "wss://www.pathofexile.com/api/trade/live/My%20League/aBc")


# ---------------------------------------------------------- create_search
def test_create_search():
    class FakeResp(io.BytesIO):
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *a): return False
    calls = {}
    def fake_urlopen(req, timeout=0):
        calls["url"] = req.full_url
        calls["body"] = json.loads(req.data.decode())
        calls["ua"] = req.headers.get("User-agent", "")
        return FakeResp(b'{"id": "NEWID", "total": 5, "result": []}')
    old = livesearch._urlopen
    livesearch._urlopen = fake_urlopen
    try:
        sid = create_search({"query": {"term": "x"}}, "Curse of the Allflame")
    finally:
        livesearch._urlopen = old
    assert sid == "NEWID"
    assert "search/Curse%20of%20the%20Allflame" in calls["url"]
    assert calls["body"] == {"query": {"term": "x"}}
    assert "poe-league-tools" in calls["ua"]


def test_create_search_no_id():
    class FakeResp(io.BytesIO):
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *a): return False
    old = livesearch._urlopen
    livesearch._urlopen = lambda req, timeout=0: FakeResp(b'{"error": "x"}')
    try:
        try:
            create_search({}, "L")
            raise AssertionError("expected LiveSearchUnavailable")
        except LiveSearchUnavailable:
            pass
    finally:
        livesearch._urlopen = old


# ---------------------------------------------------------------- fetcher
def test_fetcher_chunks_floor_and_429():
    with open(FIXTURE, encoding="utf-8") as f:
        body = f.read().encode()

    class FakeResp(io.BytesIO):
        headers = {"X-Rate-Limit-Rules": "Ip",
                   "X-Rate-Limit-Ip": "6:4:10",
                   "X-Rate-Limit-Ip-State": "1:4:0"}
        def __enter__(self): return self
        def __exit__(self, *a): return False

    urls, slept = [], []
    clock_val = [0.0]
    def clock(): return clock_val[0]
    def sleep(s):
        slept.append(round(s, 3))
        clock_val[0] += s
    responses = []
    def fake_urlopen(req, timeout=0):
        urls.append(req.full_url)
        if responses:
            nxt = responses.pop(0)
            if isinstance(nxt, Exception):
                raise nxt
        return FakeResp(body)

    old = livesearch._urlopen
    livesearch._urlopen = fake_urlopen
    try:
        fetch = Fetcher("L", "SECRET", clock=clock, sleep=sleep)
        spec = SearchSpec("sid1", "x")
        ids = [f"i{n}" for n in range(12)]          # 12 -> chunks of 10+2
        alerts = fetch(ids, spec)
        assert len(urls) == 2
        assert urls[0].endswith("?query=sid1")
        assert urls[0].count(",") == 9              # 10 ids in chunk 1
        assert len(alerts) == 6                     # fixture parsed twice
        # global floor: second call waited ~1 s
        assert any(abs(s - 1.0) < 0.01 for s in slept)

        # 429 with Retry-After honored, chunk dropped, no crash
        err = urllib.error.HTTPError(
            "u", 429, "too many", {"Retry-After": "7"}, io.BytesIO(b""))
        responses.append(err)
        slept.clear()
        alerts = fetch(["a", "b"], spec)
        assert alerts == []
        before = clock_val[0]
        fetch(["c"], spec)                          # next call waits >= 7 s
        assert clock_val[0] - before >= 7.0
    finally:
        livesearch._urlopen = old


def test_ratelimit_helpers():
    assert ratelimit.retry_after_seconds("12") == 12.0
    assert ratelimit.retry_after_seconds(None) == 2.0
    h = {"X-Rate-Limit-Rules": "Ip,Account",
         "X-Rate-Limit-Ip": "8:10:60", "X-Rate-Limit-Ip-State": "8:10:0",
         "X-Rate-Limit-Account": "3:5:60",
         "X-Rate-Limit-Account-State": "1:5:45"}
    # Ip bucket full (window 10), Account penalty active (45)
    assert ratelimit.bucket_deadline(h, 100.0) == 145.0
    assert ratelimit.bucket_deadline(None, 5.0) == 5.0
    assert ratelimit.bucket_deadline({}, 5.0) == 5.0


# ---------------------------------------------------------------- monitor
class ScriptedConn:
    """recv() plays back a script; entries may be str or Exception."""
    def __init__(self, frames):
        self.frames = list(frames)
        self.closed = False
    def recv(self):
        if not self.frames:
            raise ConnectionError("script exhausted")
        item = self.frames.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
    def close(self):
        self.closed = True


def test_monitor_dedupe_fetch_and_reconnect():
    conns = [
        ScriptedConn(['{"new": ["a", "b"]}', '{"new": ["b", "c"]}',
                      ConnectionError("drop")]),
        ScriptedConn(['{"new": ["c", "d"]}']),      # then exhausts -> stop
    ]
    made = []
    def connector(url):
        made.append(url)
        if not conns:
            raise ConnectionError("no more")
        return conns.pop(0)
    fetched = []
    def fetcher(ids, spec):
        fetched.append(list(ids))
        return [Alert(spec.label, spec.search_id, i, "Item", 1.0,
                      "chaos", "~b/o", "acct", "char", "w", "t", "u")
                for i in ids]
    alerts, slept = [], []
    spec = SearchSpec("sid", "lbl")
    mon = LiveSearchMonitor([spec], "L", "sess", alerts.append,
                            connector=connector, fetcher=fetcher,
                            sleep=slept.append, backoff_start=2.0,
                            backoff_cap=60.0)
    mon.run_search(spec, max_connects=2)
    # dedupe across frames AND across reconnects: a,b then c then d
    assert fetched == [["a", "b"], ["c"], ["d"]]
    assert [a.listing_id for a in alerts] == ["a", "b", "c", "d"]
    assert len(made) == 2 and made[0].endswith("/live/L/sid")
    assert slept and slept[0] == 2.0                # backoff after drop


def test_monitor_backoff_caps_and_stop():
    def connector(url):
        raise ConnectionError("refused")
    slept = []
    spec = SearchSpec("s")
    mon = LiveSearchMonitor([spec], "L", "x", lambda a: None,
                            connector=connector, fetcher=lambda i, s: [],
                            sleep=slept.append, backoff_start=1.0,
                            backoff_cap=4.0)
    mon.run_search(spec, max_connects=5)
    assert slept == [1.0, 2.0, 4.0, 4.0, 4.0]       # doubles then caps
    mon.stop.set()
    slept.clear()
    mon.run_search(spec, max_connects=5)            # stopped: no work
    assert slept == []


def test_monitor_requires_specs_and_degrades():
    try:
        LiveSearchMonitor([], "L", "x", lambda a: None,
                          connector=lambda u: None)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    # default connector without websocket-client and/or POESESSID degrades
    if "websocket" not in sys.modules:
        try:
            livesearch.default_connector("wss://x", "")
            raise AssertionError("expected LiveSearchUnavailable")
        except LiveSearchUnavailable as exc:
            assert "websocket-client" in str(exc) or "POESESSID" in str(exc)


# -------------------------------------------------------------------- CLI
def test_snipe_sink_and_log():
    with tempfile.TemporaryDirectory() as td:
        log = os.path.join(td, "alerts.jsonl")
        out = io.StringIO()
        opened = []
        clock_val = [100.0]
        sink = snipe.AlertSink(open_browser=True, log_path=log, out=out,
                               opener=opened.append,
                               clock=lambda: clock_val[0])
        a1 = Alert("lbl", "sid", "id1", "Taryn's Shiver", 2.0, "chaos",
                   "~b/o", "acct", "char", "@char hi", "t", "https://r")
        a2 = Alert("lbl", "sid", "id2", "Quiet Item", None, "", "", "acct2",
                   "", "", "t", "https://r")
        sink(a1)
        sink(a2)                        # within cooldown: no second open
        text = out.getvalue()
        assert "Taryn's Shiver" in text and "2 chaos" in text
        assert "whisper: @char hi" in text
        assert "no whisper on listing" in text
        assert opened == ["https://r"]
        clock_val[0] += 11
        sink(a1)
        assert opened == ["https://r", "https://r"]
        with open(log, encoding="utf-8") as f:
            rows = [json.loads(line) for line in f]
        assert len(rows) == 3 and rows[0]["listing_id"] == "id1"
        assert sink.count == 3


def test_snipe_build_specs_and_league():
    with tempfile.TemporaryDirectory() as td:
        qpath = os.path.join(td, "taryns.json")
        with open(qpath, "w", encoding="utf-8") as f:
            json.dump({"query": {}}, f)

        class FakeResp(io.BytesIO):
            headers = {}
            def __enter__(self): return self
            def __exit__(self, *a): return False
        old = livesearch._urlopen
        livesearch._urlopen = (
            lambda req, timeout=0: FakeResp(b'{"id": "QID"}'))
        try:
            ns = snipe.argparse.Namespace(
                search_ids=["MANUAL"], queries=[qpath],
                labels=["first-label"])
            specs = snipe.build_specs(ns, "L")
        finally:
            livesearch._urlopen = old
        assert [(s.search_id, s.label) for s in specs] == [
            ("MANUAL", "first-label"), ("QID", "taryns")]

        cfg = os.path.join(td, "config.json")
        with open(cfg, "w", encoding="utf-8") as f:
            json.dump({"league": "Mirage"}, f)
        assert snipe.default_league(cfg) == "Mirage"
        assert snipe.default_league(os.path.join(td, "nope.json")) == \
            "Standard"


def test_snipe_main_degrades_without_transport():
    # No POESESSID, fake search id: must exit 2 with a clear message,
    # never touching the network (no monitor threads started).
    old_env = os.environ.pop("POESESSID", None)
    err = io.StringIO()
    old_stderr = sys.stderr
    sys.stderr = err
    try:
        rc = snipe.main(["--search-id", "ZZZ", "--league", "L"])
    finally:
        sys.stderr = old_stderr
        if old_env is not None:
            os.environ["POESESSID"] = old_env
    assert rc == 2
    assert "POESESSID" in err.getvalue()


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"OK ({len(fns)} tests)")
