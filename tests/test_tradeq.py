"""Headless tests for tools/tradeq.py: catalog validation, mocked-LLM
end-to-end print path, reprompt-on-bad-id, LLMDisabled degrade, --post.
Offline: the LLM and urlopen are faked; no network, no Qt, no API keys."""
import contextlib
import io
import json
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "tools"), ROOT]

# Fake llm package installed BEFORE importing tradeq so `from llm.client
# import ...` inside main() resolves to these doubles even if the real
# llm/client.py exists (sys.modules wins) — guarantees offline.


class LLMDisabled(RuntimeError):
    pass


class LLMError(RuntimeError):
    pass


class FakeLLM:
    """Scriptable llm.client.LLM double: pops canned replies per call."""
    script = []          # list of dicts (or exceptions) shared by tier
    calls = []

    def __init__(self, tier):
        assert tier == "standard", f"tradeq must use standard tier, got {tier}"

    def complete(self, system=None, messages=None, max_tokens=None,
                 feature=None, json_schema=None):
        assert feature == "tradeq", "usage-meter tag must be 'tradeq'"
        assert json_schema is not None, "tradeq must constrain with a schema"
        assert isinstance(system, str) and "Stat catalog" in system
        FakeLLM.calls.append({"messages": messages})
        reply = FakeLLM.script.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


_fake_client = types.ModuleType("llm.client")
_fake_client.LLM = FakeLLM
_fake_client.LLMDisabled = LLMDisabled
_fake_client.LLMError = LLMError
_fake_pkg = types.ModuleType("llm")
_fake_pkg.client = _fake_client
sys.modules["llm"] = _fake_pkg
sys.modules["llm.client"] = _fake_client

import tradeq                                  # noqa: E402

# ------------------------------------------------------ fixture catalog
FIXTURE_CATALOG = {
    "pseudo.pseudo_total_cold_resistance": "+#% total to Cold Resistance",
    "pseudo.pseudo_total_life": "+# total maximum Life",
    "explicit.stat_2250533757": "#% increased Movement Speed",
    "explicit.stat_3299347043": "+# to maximum Life",
}


def good_query(max_chaos=5):
    return {
        "query": {
            "status": {"option": "online"},
            "stats": [{"type": "and", "filters": [
                {"id": "explicit.stat_2250533757", "value": {"min": 30}},
                {"id": "pseudo.pseudo_total_life"},
                {"id": "pseudo.pseudo_total_cold_resistance",
                 "value": {"min": 20}},
            ]}],
            "filters": {"type_filters": {"filters": {
                            "category": {"option": "armour.boots"}}},
                        "trade_filters": {"filters": {
                            "price": {"option": "chaos",
                                      "max": max_chaos}}}},
        },
        "sort": {"price": "asc"},
    }


# ------------------------------------------------- catalog validation
errs = tradeq.validate_query(good_query(), FIXTURE_CATALOG)
assert errs == [], f"real ids from the fixture catalog must pass: {errs}"

bad = good_query()
bad["query"]["stats"][0]["filters"].append(
    {"id": "explicit.stat_9999999999", "value": {"min": 1}})
errs = tradeq.validate_query(bad, FIXTURE_CATALOG)
assert any("unknown stat id: explicit.stat_9999999999" in e for e in errs), \
    f"fake id must be rejected: {errs}"

assert tradeq.validate_query("not a dict", FIXTURE_CATALOG)
assert tradeq.validate_query({}, FIXTURE_CATALOG)
malformed = good_query()
malformed["query"]["stats"][0]["type"] = "xor"
assert any(".type" in e for e in
           tradeq.validate_query(malformed, FIXTURE_CATALOG))
nostatus = good_query()
nostatus["query"]["status"] = {"option": "invisible"}
assert any("status" in e for e in
           tradeq.validate_query(nostatus, FIXTURE_CATALOG))

# the shipped trimmed catalog loads and contains the ~40 verified stats
shipped = tradeq.load_catalog()
assert 40 <= len(shipped) <= 60, len(shipped)
assert "pseudo.pseudo_total_cold_resistance" in shipped
assert shipped["explicit.stat_3299347043"] == "+# to maximum Life"
assert tradeq.validate_query(good_query(), shipped) == []

# ------------------------------------------------- build_query + reprompt
FakeLLM.script = [good_query()]
FakeLLM.calls = []
out = tradeq.build_query("boots", FIXTURE_CATALOG, FakeLLM("standard"))
assert out == good_query() and len(FakeLLM.calls) == 1

# bad id first -> exactly one reprompt carrying the error, then success
FakeLLM.script = [bad, good_query()]
FakeLLM.calls = []
out = tradeq.build_query("boots", FIXTURE_CATALOG, FakeLLM("standard"))
assert out == good_query() and len(FakeLLM.calls) == 2
reprompt = FakeLLM.calls[1]["messages"]
assert reprompt[-1]["role"] == "user"
assert "explicit.stat_9999999999" in reprompt[-1]["content"]

# still bad after the reprompt -> ValueError (no third call)
FakeLLM.script = [bad, bad]
FakeLLM.calls = []
try:
    tradeq.build_query("boots", FIXTURE_CATALOG, FakeLLM("standard"))
    raise AssertionError("second bad reply must raise ValueError")
except ValueError as e:
    assert "reprompt" in str(e)
assert len(FakeLLM.calls) == 2

# ------------------------------------------------- end-to-end print path
tmp = tempfile.mkdtemp(prefix="poe_tradeq_test_")
fixture_path = os.path.join(tmp, "trade_stats.json")
with open(fixture_path, "w", encoding="utf-8") as f:
    json.dump({"stats": [{"id": k, "text": v, "type": k.split(".")[0]}
                         for k, v in FIXTURE_CATALOG.items()]}, f)

FakeLLM.script = [good_query()]
FakeLLM.calls = []
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc = tradeq.main(["boots 30 movespeed, life, cold res, max 5c",
                      "--league", "Mirage", "--catalog", fixture_path])
text = buf.getvalue()
assert rc == 0
assert "explicit.stat_2250533757" in text, "validated JSON is printed"
assert '"max": 5' in text, "price cap survives into the printed JSON"
assert "https://www.pathofexile.com/trade/search/Mirage" in text
assert "?q=" in text, "direct pre-filled link printed"
assert "whisper sellers YOURSELF" in text
assert "never sends whispers" in text.lower() or \
       "never sends whispers" in text

# ------------------------------------------------- LLMDisabled degrade
FakeLLM.script = [LLMDisabled("POE_TOOLS_LLM=off")]
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc = tradeq.main(["boots, life", "--league", "Mirage",
                      "--catalog", fixture_path])
text = buf.getvalue()
assert rc == 0, "degrade is not a failure"
assert "manual query template" in text
assert '"stats"' in text and '"trade_filters"' in text, \
    "template shows a hand-editable query skeleton"
assert "pseudo.pseudo_total_cold_resistance" in text, \
    "catalog ids listed for hand-assembly"
assert "https://www.pathofexile.com/trade/search/Mirage" in text

# LLMError degrades too (with the error surfaced) and exits nonzero
FakeLLM.script = [LLMError("refusal")]
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc = tradeq.main(["boots", "--catalog", fixture_path])
assert rc == 1 and "manual query template" in buf.getvalue()

# ------------------------------------------------- --post (fake urlopen)


class FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


posted = {}


def fake_urlopen(req, timeout=None):
    posted["url"] = req.full_url
    posted["ua"] = req.get_header("User-agent")
    posted["body"] = json.loads(req.data.decode())
    return FakeResp({"id": "abc123XY", "total": 37, "result": ["x"] * 10})


tradeq._urlopen = fake_urlopen
tradeq._last_request_ts = 0.0
tradeq.STATE_PATH = os.path.join(tmp, "state_post.json")
FakeLLM.script = [good_query()]
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc = tradeq.main(["boots max 5c", "--league", "Mirage Event",
                      "--catalog", fixture_path, "--post"])
text = buf.getvalue()
assert rc == 0
assert "37 result(s)" in text
assert "Mirage%20Event/abc123XY" in text, "results URL includes search id"
assert posted["url"].endswith("/api/trade/search/Mirage%20Event")
assert posted["ua"] == "poe-league-tools/1.0 (contact: cyrus@hadavi.net)"
assert posted["body"] == good_query()
with open(tradeq.STATE_PATH, encoding="utf-8") as f:
    state = json.load(f)
assert state.get("last_post_ts", 0) > 0, \
    "the 2s floor is persisted across invocations"

# POST failure degrades to print-only (query JSON still printed, rc 0)


def broken_urlopen(req, timeout=None):
    raise tradeq.urllib.error.URLError("no route to host")


tradeq._urlopen = broken_urlopen
tradeq._last_request_ts = 0.0
tradeq.STATE_PATH = os.path.join(tmp, "state_fail.json")
FakeLLM.script = [good_query()]
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc = tradeq.main(["boots", "--catalog", fixture_path, "--post"])
text = buf.getvalue()
assert rc == 0
assert "explicit.stat_2250533757" in text, "query still printed"
assert "search POST failed" in text, "degrade message printed"

# ---------------------------- persisted Retry-After honored across "runs"
import time as _time                                          # noqa: E402


def rate_limited_urlopen(req, timeout=None):
    raise tradeq.urllib.error.HTTPError(
        req.full_url, 429, "Too Many Requests",
        {"Retry-After": "300"}, None)


tradeq._urlopen = rate_limited_urlopen
tradeq._last_request_ts = 0.0
tradeq.STATE_PATH = os.path.join(tmp, "state_429.json")
try:
    tradeq.post_search(good_query(), "Mirage")
    raise AssertionError("429 must raise")
except RuntimeError as e:
    assert "not retrying" in str(e)
with open(tradeq.STATE_PATH, encoding="utf-8") as f:
    state = json.load(f)
assert state["blocked_until"] - _time.time() > 290, \
    "the full uncapped Retry-After deadline is persisted"

# a fresh process (module state reset) must refuse to POST before the
# persisted deadline, without touching the network
calls_before = dict(posted)
tradeq._urlopen = fake_urlopen
tradeq._last_request_ts = 0.0
try:
    tradeq.post_search(good_query(), "Mirage")
    raise AssertionError("must refuse while the deadline is active")
except RuntimeError as e:
    assert "rate budget exhausted" in str(e)
assert posted == calls_before, "no request went out during the backoff"

print("ALL TESTS PASSED")
print(f"  shipped catalog: {len(shipped)} stats")
print(f"  end-to-end line count: {len(text.splitlines())} printed lines")
