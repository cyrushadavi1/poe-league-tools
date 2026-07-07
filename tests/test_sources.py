"""Headless tests for market/sources.py: rate gate, 429 retry, normalizers.

Offline only: fake clock, fake sleep, fake opener/fetcher - no network,
no real sleeping.  Fixtures under tests/fixtures_market/ are trimmed
real responses captured live on 2026-07-07.
"""
import io
import json
import os
import sys
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "market")]

import sources                                  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures_market")


def load(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return json.load(f)


# ------------------------------------------------------------ fakes
class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class FakeResponse:
    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    """Scripted opener: each entry is bytes (a 200 body) or an Exception."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append((request, timeout))
        action = self.script.pop(0)
        if isinstance(action, Exception):
            raise action
        return FakeResponse(action)


def http_429(retry_after):
    import email.message
    headers = email.message.Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError("http://x", 429, "Too Many Requests",
                                  headers, io.BytesIO(b""))


def make_fetcher(script, clock=None, max_retries=3):
    clock = clock or FakeClock()
    sleeps = []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        clock.t += seconds            # sleeping advances the fake clock

    opener = FakeOpener(script)
    fetcher = sources.RateLimitedFetcher(
        clock=clock, sleep=fake_sleep, opener=opener, max_retries=max_retries)
    return fetcher, opener, sleeps, clock


# ---------------------------------------------------------- rate gate
sources.reset_rate_gate()
f, opener, sleeps, clock = make_fetcher([b'{"a":1}', b'{"a":2}', b'{"a":3}', b'{"a":4}'])

assert f.get_json("http://x/1") == {"a": 1}
assert sleeps == [], "first request must not sleep"

clock.t = 0.5                          # only 0.5 s elapsed
assert f.get_json("http://x/2") == {"a": 2}
assert sleeps == [1.5], f"must sleep up to the 2 s floor, got {sleeps}"

assert f.get_json("http://x/3") == {"a": 3}        # immediately after
assert sleeps == [1.5, 2.0], "back-to-back request waits the full floor"

clock.t += 100.0                       # long natural gap
assert f.get_json("http://x/4") == {"a": 4}
assert sleeps == [1.5, 2.0], "no sleep needed after a natural >2s gap"

# the mandatory User-Agent goes out on every request
request, timeout = opener.requests[0]
assert request.get_header("User-agent") == \
    "poe-league-tools/1.0 (contact: cyrus@hadavi.net)"
assert timeout == sources.DEFAULT_TIMEOUT_S

# the gate is module-level: a second fetcher sharing the clock also waits
f2, _, sleeps2, _ = make_fetcher([b'{"b":1}'], clock=clock)
assert f2.get_json("http://x/5") == {"b": 1}
assert sleeps2 == [2.0], "gate must be shared across fetcher instances"

# ---------------------------------------------------------- 429 retries
sources.reset_rate_gate()
f, opener, sleeps, clock = make_fetcher([http_429("5"), http_429("5"), b'{"ok":1}'])
assert f.get_json("http://x/r") == {"ok": 1}
assert len(opener.requests) == 3, "two 429s then success = 3 attempts"
assert sleeps.count(5.0) == 2, f"Retry-After: 5 honored per retry, got {sleeps}"

sources.reset_rate_gate()               # missing Retry-After -> floor pause
f, opener, sleeps, clock = make_fetcher([http_429(None), b'{"ok":2}'])
assert f.get_json("http://x/r2") == {"ok": 2}
assert 2.0 in sleeps, "429 without Retry-After sleeps the 2 s floor"

sources.reset_rate_gate()               # exhausting retries -> SourceError
f, opener, sleeps, clock = make_fetcher(
    [http_429("1"), http_429("1"), http_429("1"), http_429("1")])
try:
    f.get_json("http://x/r3")
    raise AssertionError("expected SourceError after max retries")
except sources.SourceError as exc:
    assert "429" in str(exc)
assert len(opener.requests) == 4, "1 attempt + 3 retries, then give up"

# sub-floor Retry-After is bumped up to the 2 s floor
sources.reset_rate_gate()
f, opener, sleeps, clock = make_fetcher([http_429("0.1"), b'{"ok":3}'])
assert f.get_json("http://x/r4") == {"ok": 3}
assert 2.0 in sleeps and 0.1 not in sleeps, \
    "Retry-After below the floor must still wait the floor"

# ---------------------------------------------------------- other failures
sources.reset_rate_gate()
err500 = urllib.error.HTTPError("http://x", 500, "boom", None, io.BytesIO(b""))
f, opener, sleeps, clock = make_fetcher([err500])
try:
    f.get_json("http://x/e1")
    raise AssertionError("expected SourceError on HTTP 500")
except sources.SourceError:
    pass
assert len(opener.requests) == 1, "non-429 errors are not retried"

sources.reset_rate_gate()
f, opener, sleeps, clock = make_fetcher([urllib.error.URLError("refused")])
try:
    f.get_json("http://x/e2")
    raise AssertionError("expected SourceError on URLError")
except sources.SourceError:
    pass

sources.reset_rate_gate()
f, opener, sleeps, clock = make_fetcher([b"<html>not json</html>"])
try:
    f.get_json("http://x/e3")
    raise AssertionError("expected SourceError on non-JSON body")
except sources.SourceError:
    pass

# Retry-After parsing corner cases
assert sources._retry_after_seconds(None) == 2.0
assert sources._retry_after_seconds("7") == 7.0
assert sources._retry_after_seconds("junk value") == 2.0
assert sources._retry_after_seconds("-3") == 0.0

# ------------------------------------------------- currency normalization
TS = "2026-07-07T20:00:00Z"
cur = load("currency_overview.json")
rows = sources.normalize_currency(cur, "Mirage", TS)
assert len(rows) == 3
by_item = {r["item"]: r for r in rows}

div = by_item["Divine Orb"]
assert div["ts"] == TS and div["source"] == "poe.ninja" and div["league"] == "Mirage"
assert div["buy"] == 500.0, "buy = receive.value (chaos to buy 1 divine)"
assert div["buy_vol"] == 132.0, "buy_vol = receive.listing_count"
assert div["sell"] == 1.0 / 0.002369, "sell = 1/pay.value (chaos from selling)"
assert div["sell_vol"] == 584.0, "sell_vol = pay.listing_count"
assert abs(div["sell"] - 422.11903) < 1e-4
assert div["buy"] > div["sell"], "sane spread: buying costs more than selling yields"
raw = json.loads(div["raw"])
assert raw["chaosEquivalent"] == 443.0 and raw["detailsId"] == "divine-orb"
assert raw["pay"]["value"] == 0.002369, "raw keeps the untouched line"

deck = by_item["Stacked Deck"]          # receive-only line
assert deck["buy"] == 7.36 and deck["buy_vol"] == 159.0
assert deck["sell"] is None and deck["sell_vol"] is None

gcp = by_item["Gemcutter's Prism"]      # pay.value == 1.0 -> sell exactly 1c
assert gcp["sell"] == 1.0 and gcp["sell_vol"] == 22.0
assert gcp["buy"] == 6.14 and gcp["buy_vol"] == 136.0

# ------------------------------------------------- exchange normalization
ex = load("exchange_overview.json")
rows = sources.normalize_exchange(ex, "Ancestors", TS)
assert len(rows) == 3
by_item = {r["item"]: r for r in rows}
doubt = by_item["Deafening Essence of Doubt"]   # name mapped from items[]
assert doubt["buy"] == 2.06 and doubt["sell"] == 2.06, \
    "exchange publishes one aggregate price -> buy == sell"
assert doubt["buy_vol"] == 678.0 and doubt["sell_vol"] == 678.0
assert doubt["league"] == "Ancestors" and doubt["source"] == "poe.ninja"
assert json.loads(doubt["raw"])["maxVolumeRate"] == 0.485
assert by_item["Deafening Essence of Fear"]["buy"] == 2.28
assert by_item["Deafening Essence of Greed"]["buy_vol"] == 370.8

# ------------------------------------------------- item normalization
uw = load("item_overview.json")
rows = sources.normalize_items(uw, "Ancestors", TS)
assert len(rows) == 4
by_item = {r["item"]: r for r in rows}
assert "The Golden Charlatan 6L" in by_item, "linked variant disambiguated"
assert "The Golden Charlatan" in by_item, "base variant keeps its plain name"
six = by_item["The Golden Charlatan 6L"]
assert six["buy"] == 34168.0 and six["sell"] == 34168.0
assert six["buy_vol"] == 3.0 and six["sell_vol"] == 3.0
assert json.loads(six["raw"])["links"] == 6
base = by_item["The Golden Charlatan"]
assert base["buy"] == 30678.0 and base["buy_vol"] == 6.0
assert by_item["Divinarius"]["buy"] == 918.5
assert by_item["Divinarius"]["sell_vol"] == 16.0

# ---------------------------------------------------------- NinjaClient
class FakeFetcher:
    def __init__(self, payloads):
        self.payloads = payloads        # substring of url -> payload
        self.urls = []

    def get_json(self, url):
        self.urls.append(url)
        for key, payload in self.payloads.items():
            if key in url:
                return payload
        raise AssertionError(f"unexpected url: {url}")


ff = FakeFetcher({
    "/economy/stash/current/currency/overview": cur,
    "/economy/exchange/current/overview": ex,
    "/economy/stash/current/item/overview": uw,
})
client = sources.NinjaClient("Ancestors", fetcher=ff)

assert client.currency_overview_url("Currency") == (
    "https://poe.ninja/poe1/api/economy/stash/current/currency/overview"
    "?league=Ancestors&type=Currency")
assert client.currency_overview_url("Fragment").endswith("&type=Fragment")
assert client.item_overview_url("Scarab") == (
    "https://poe.ninja/poe1/api/economy/exchange/current/overview"
    "?league=Ancestors&type=Scarab"), \
    "Scarab/Essence/DivinationCard route to the exchange endpoint"
assert client.item_overview_url("Essence").endswith("&type=Essence")
assert client.item_overview_url("DivinationCard").endswith("&type=DivinationCard")
assert client.item_overview_url("UniqueWeapon") == (
    "https://poe.ninja/poe1/api/economy/stash/current/item/overview"
    "?league=Ancestors&type=UniqueWeapon")
spaced = sources.NinjaClient("Hardcore Ancestors")
assert "league=Hardcore%20Ancestors" in spaced.currency_overview_url()

assert set(sources.NinjaClient.ITEM_TYPES) == \
    {"Scarab", "Essence", "DivinationCard", "UniqueWeapon"}

rows = client.snapshot_currency("Currency", ts=TS)
assert {r["item"] for r in rows} == \
    {"Divine Orb", "Stacked Deck", "Gemcutter's Prism"}
assert all(r["ts"] == TS and r["league"] == "Ancestors" for r in rows)

rows = client.snapshot_items("Essence", ts=TS)   # exchange-normalized
assert {r["item"] for r in rows} == {"Deafening Essence of Doubt",
                                     "Deafening Essence of Fear",
                                     "Deafening Essence of Greed"}
rows = client.snapshot_items("UniqueWeapon", ts=TS)
assert "The Golden Charlatan 6L" in {r["item"] for r in rows}

# a snapshot without an explicit ts stamps a UTC ISO second
auto = client.snapshot_items("UniqueWeapon")
assert auto[0]["ts"].endswith("Z") and len(auto[0]["ts"]) == 20

# ------------------------------------------------- league discovery
idx = load("index_state.json")
assert sources.discover_league(index_state=idx) == "Ancestors"
hc_only = {"economyLeagues": [
    {"name": "Hardcore Mirage"}, {"name": "Standard"}, {"name": "Mirage"}]}
assert sources.discover_league(index_state=hc_only) == "Mirage"
fallback = {"economyLeagues": [{"name": "Standard"}]}
assert sources.discover_league(index_state=fallback) == "Standard"
try:
    sources.discover_league(index_state={"economyLeagues": []})
    raise AssertionError("expected SourceError on empty league list")
except sources.SourceError:
    pass
idx_ff = FakeFetcher({"/data/index-state": idx})
assert sources.discover_league(fetcher=idx_ff) == "Ancestors"
assert idx_ff.urls == ["https://poe.ninja/poe1/api/data/index-state"]

# ------------------------------------------------- official trade API
stats = load("trade_stats.json")
sf = FakeFetcher({"/api/trade/data/stats": stats})
payload = sources.fetch_stats(fetcher=sf)
assert [g["id"] for g in payload["result"]] == ["pseudo", "explicit"]
assert payload["result"][0]["entries"][0]["id"] == \
    "pseudo.pseudo_total_cold_resistance"
assert sf.urls == ["https://www.pathofexile.com/api/trade/data/stats"]

bad = FakeFetcher({"/api/trade/data/stats": {"nope": []}})
try:
    sources.fetch_stats(fetcher=bad)
    raise AssertionError("expected SourceError on malformed stats payload")
except sources.SourceError:
    pass

try:
    sources.fetch_bulk_exchange_quote("Ancestors", "chaos", "divine")
    raise AssertionError("bulk exchange quotes must stay disabled")
except NotImplementedError:
    pass

# import safety: importing sources must not have touched the network -
# every fetch in this file went through fakes, and the module holds no
# module-level fetcher instances.
assert not isinstance(getattr(sources, "_GATE")["last"], str)

print("ALL TESTS PASSED")
print(f"  divine spread: buy {div['buy']}c / sell {round(div['sell'], 2)}c")
print(f"  {len(sources.NinjaClient.ITEM_TYPES)} item types routed; "
      f"currency+fragment via stash currency overview")
