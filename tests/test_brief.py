"""Headless tests: market.brief (watchlist / daily / explain) + prompts.

Offline by design: a fake LLM is injected through main()'s llm_factory
parameter (the real llm.client is imported only for its LLMDisabled /
LLMError exception types — no SDK, no keys, no network), and the store is
a tmp-file SQLite DB seeded with synthetic rows.
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT]

from llm.client import LLMDisabled, LLMError    # noqa: E402
from market import brief, prompts               # noqa: E402
from market.store import Store                  # noqa: E402


class FakeLLM:
    """Records every complete() call and replays canned responses."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, system, messages, max_tokens, feature,
                 json_schema=None):
        self.calls.append({"system": system, "messages": messages,
                           "max_tokens": max_tokens, "feature": feature,
                           "json_schema": json_schema})
        return self.responses.pop(0)


class DisabledMidCallLLM:
    """Kill switch flipped between construction and the call."""

    def complete(self, **kwargs):
        raise LLMDisabled("POE_TOOLS_LLM=off")


class ErrLLM:
    def complete(self, **kwargs):
        raise LLMError("boom")


def run(argv, fake=None, raise_disabled=False):
    """brief.main with an injected LLM factory; returns (rc, stdout, stderr)."""
    def factory():
        if raise_disabled:
            raise LLMDisabled("test kill switch")
        return fake

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = brief.main(argv, llm_factory=factory)
    return rc, out.getvalue(), err.getvalue()


def iso(**delta):
    """UTC ISO timestamp offset from now, matching the store's format."""
    return (datetime.now(timezone.utc)
            + timedelta(**delta)).isoformat(timespec="seconds")


def payload_of(call):
    """The JSON payload embedded in a recorded user message."""
    assert isinstance(call["messages"], str), "plain-str user turn expected"
    return json.loads(call["messages"].split("\n\n", 1)[1])


# ------------------------------------------------------------------ prompts
for name in ("WATCHLIST_PROMPT", "DAILY_BRIEF_PROMPT",
             "ANOMALY_EXPLAINER_PROMPT"):
    text = getattr(prompts, name)
    assert isinstance(text, str) and len(text) > 200, name
    assert "assumption" in text, f"{name}: cite-or-assumption not baked in"
for cause in ("price_fixing", "patch_demand", "low_liquidity", "genuine"):
    assert cause in prompts.ANOMALY_EXPLAINER_PROMPT, cause
assert "id" in prompts.WATCHLIST_PROMPT and \
    "expected_window" in prompts.WATCHLIST_PROMPT
assert "low confidence" in prompts.DAILY_BRIEF_PROMPT
for section in ("What to flip", "What to hold", "What changed"):
    assert section in prompts.DAILY_BRIEF_PROMPT, section

tmp = tempfile.mkdtemp(prefix="poe_brief_test_")
try:
    # -------------------------------------------------------- watchlist: LLM
    summary = {"patch": "3.29", "items": [
        {"id": "sk_flameblast", "kind": "skill",
         "change": "Flameblast deals 40% more damage", "direction": "buff",
         "quote": "Flameblast: now deals 40% more damage.",
         "source": "patch notes 3.29"},
        {"id": "un_searing_touch", "kind": "unique",
         "change": "Searing Touch burning damage doubled",
         "direction": "buff", "quote": "The Searing Touch: 100% increased.",
         "source": "patch notes 3.29"},
    ]}
    summary_path = os.path.join(tmp, "summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f)
    recs_path = os.path.join(tmp, "recs.md")
    with open(recs_path, "w", encoding="utf-8") as f:
        f.write("## Recommended starters\n- Flameblast Totems (Chieftain)\n")
    wl_out = os.path.join(tmp, "watchlist.json")

    fake = FakeLLM([{"watchlist": [
        {"item": "The Searing Touch", "reason": "burning staff for the "
         "buffed Flameblast archetype", "source": "un_searing_touch",
         "expected_window": "day 1-3"},
        {"item": "Tabula Rasa", "reason": "generic league-start 6-link "
         "demand", "source": "assumption", "expected_window": "week 1"},
        {"item": "Chaos Orb", "reason": "cited id does not exist",
         "source": "made_up_id", "expected_window": "day 1"},
    ]}])
    rc, out, err = run(["watchlist", "--summary", summary_path,
                        "--recs", recs_path, "--out", wl_out], fake)
    assert rc == 0, (rc, out, err)
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["system"] == prompts.WATCHLIST_PROMPT
    assert call["feature"] == "market_watchlist"
    assert call["json_schema"] == brief.WATCHLIST_SCHEMA
    assert "watchlist" in call["json_schema"]["required"]
    data = payload_of(call)
    assert data["patch"] == "3.29"
    assert [it["id"] for it in data["summary_items"]] == \
        ["sk_flameblast", "un_searing_touch"], "summary items reach the LLM"
    assert "Flameblast Totems" in data["advisor_recommendations_md"], \
        "--recs markdown reaches the LLM"

    with open(wl_out, encoding="utf-8") as f:
        written = json.load(f)
    assert isinstance(written, list) and len(written) == 3
    known_ids = {"sk_flameblast", "un_searing_touch"}
    for entry in written:
        assert set(entry) == {"item", "reason", "source", "expected_window"}
        assert entry["source"] == "assumption" or entry["source"] in known_ids, \
            f"contract: cite a summary id or 'assumption', got {entry}"
    assert written[0]["source"] == "un_searing_touch"
    assert written[2]["source"] == "assumption", "uncited id must be coerced"
    assert "3 watchlist entries" in out and "1 uncited" in out

    # ------------------------------------------- watchlist: degrade + absent
    wl_out2 = os.path.join(tmp, "watchlist2.json")
    rc, out, err = run(["watchlist", "--summary", summary_path,
                        "--out", wl_out2], raise_disabled=True)
    assert rc == 0 and "LLM disabled - skipped" in out, (rc, out)
    assert not os.path.exists(wl_out2), "no watchlist written when disabled"

    rc, out, err = run(["watchlist", "--summary", summary_path,
                        "--out", wl_out2], fake=DisabledMidCallLLM())
    assert rc == 0 and "LLM disabled - skipped" in out, \
        "mid-call LLMDisabled degrades too"

    unused = FakeLLM([])
    rc, out, err = run(["watchlist",
                        "--summary", os.path.join(tmp, "nope.json"),
                        "--out", wl_out2], fake=unused)
    assert rc == 0 and "summary not found" in out and "skipped" in out
    assert unused.calls == [], "no LLM call without a summary to cite"

    # ------------------------------------------------------- daily: store rows
    db = os.path.join(tmp, "market.db")
    store = Store(db)
    store.insert_snapshots([
        {"ts": iso(hours=-23), "source": "ninja", "league": "L",
         "item": "divine", "buy": 200.0, "sell": 198.0,
         "buy_vol": 500, "sell_vol": 450},
        {"ts": iso(minutes=-5), "source": "ninja", "league": "L",
         "item": "divine", "buy": 220.0, "sell": 216.0,
         "buy_vol": 520, "sell_vol": 470},
        {"ts": iso(hours=-20), "source": "ninja", "league": "L",
         "item": "chaos", "buy": 1.0, "sell": 1.0,
         "buy_vol": 10000, "sell_vol": 9500},
        {"ts": iso(minutes=-10), "source": "ninja", "league": "L",
         "item": "chaos", "buy": 1.0, "sell": 1.0,
         "buy_vol": 9000, "sell_vol": 9000},
        # outside the 24h window -> must not appear anywhere
        {"ts": iso(hours=-30), "source": "ninja", "league": "L",
         "item": "exalt", "buy": 40.0, "sell": 39.0,
         "buy_vol": 100, "sell_vol": 90},
    ])
    opps = []
    for i in range(25):     # 25 stored -> only the top 20 by est_profit_c go in
        opps.append({"id": f"opp{i:02d}", "ts": iso(minutes=-30),
                     "kind": "cycle",
                     "path": ["chaos->divine", "divine->chaos"],
                     "margin_pct": 5.0 + i, "est_profit_c": 10.0 * (i + 1),
                     "liq_score": 0.7, "confidence": "high", "flags": []})
    opps[24]["confidence"] = "low"
    opps[24]["flags"] = ["price_fixing_suspect"]
    store.upsert_opportunities(opps)
    store.close()

    wl_path = os.path.join(tmp, "wl.json")
    with open(wl_path, "w", encoding="utf-8") as f:
        json.dump([
            {"item": "divine", "reason": "r", "source": "assumption",
             "expected_window": "day 1-3"},
            {"item": "Mageblood", "reason": "r", "source": "assumption",
             "expected_window": "week 1"},
        ], f)

    # --------------------------------------------------- daily: LLM + --out
    fake = FakeLLM(["# Daily Brief\n\n## What to flip\n- divine loop\n"])
    brief_out = os.path.join(tmp, "brief.md")
    rc, out, err = run(["daily", "--db", db, "--watchlist", wl_path,
                        "--out", brief_out], fake)
    assert rc == 0, (rc, out, err)
    call = fake.calls[0]
    assert call["system"] == prompts.DAILY_BRIEF_PROMPT
    assert call["feature"] == "market_daily_brief"
    assert call["json_schema"] is None, "daily brief is plain markdown"
    data = payload_of(call)
    ids = [o["id"] for o in data["opportunities"]]
    assert len(ids) == 20 and ids[0] == "opp24", ids
    assert "opp04" not in ids and "opp00" not in ids, \
        "only the top 20 by est_profit_c are briefed"
    assert data["opportunities"][0]["confidence"] == "low"
    assert data["opportunities"][0]["flags"] == ["price_fixing_suspect"]
    assert set(data["trendlines_24h"]) == {"divine", "chaos"}
    assert data["trendlines_24h"]["divine"]["buy_change_pct"] == 10.0
    assert data["trendlines_24h"]["divine"]["points"] == 2
    assert "exalt" not in call["messages"], "stale rows stay out of the brief"
    assert [h["item"] for h in data["watchlist_hits"]] == ["divine"]
    assert "Mageblood" not in call["messages"], \
        "watchlist items with no 24h data are not hits"
    with open(brief_out, encoding="utf-8") as f:
        assert f.read().startswith("# Daily Brief")
    assert "wrote daily brief" in out

    # ------------------------------------------------- daily: stdout variant
    fake = FakeLLM(["# Daily Brief (stdout)\n"])
    rc, out, err = run(["daily", "--db", db, "--watchlist", wl_path], fake)
    assert rc == 0 and "# Daily Brief (stdout)" in out

    # -------------------------------------------- daily: degrade + edge cases
    rc, out, err = run(["daily", "--db", db], raise_disabled=True)
    assert rc == 0 and "LLM disabled - skipped" in out, (rc, out)

    fake = FakeLLM([])
    empty_db = os.path.join(tmp, "empty.db")
    Store(empty_db).close()
    rc, out, err = run(["daily", "--db", empty_db], fake)
    assert rc == 0 and "nothing to brief" in out
    assert fake.calls == [], "no LLM call without market data"

    missing_db = os.path.join(tmp, "missing.db")
    rc, out, err = run(["daily", "--db", missing_db], fake)
    assert rc == 0 and "market database not found" in out
    assert not os.path.exists(missing_db), "daily must not create a DB"

    rc, out, err = run(["daily", "--db", db, "--watchlist", wl_path],
                       fake=ErrLLM())
    assert rc == 1 and "LLM error" in err, "API failure is an error, not a skip"

    # -------------------------------------------------------------- explain
    fake = FakeLLM([{"cause": "price_fixing",
                     "reasoning": "cheapest listings sit 30% below the band"}])
    rc, out, err = run(["explain", "opp24", "--db", db,
                        "--watchlist", wl_path], fake)
    assert rc == 0, (rc, out, err)
    call = fake.calls[0]
    assert call["system"] == prompts.ANOMALY_EXPLAINER_PROMPT
    assert call["feature"] == "market_anomaly_explain"
    assert call["json_schema"] == brief.EXPLAIN_SCHEMA
    assert set(call["json_schema"]["required"]) == {"cause", "reasoning"}
    data = payload_of(call)
    assert data["opportunity"]["id"] == "opp24"
    assert data["opportunity"]["flags"] == ["price_fixing_suspect"]
    quotes = {q["item"]: q for q in data["latest_quotes"]}
    assert set(quotes) == {"chaos", "divine"}, "only the path's items"
    assert quotes["divine"]["buy"] == 220.0, "latest snapshot per item"
    assert "raw" not in quotes["divine"], "bulk raw payloads stay out"
    assert set(data["trendlines_24h"]) == {"chaos", "divine"}
    assert [n["item"] for n in data["context_notes"]] == ["divine"]
    assert "probable cause: price_fixing" in out
    assert "cheapest listings sit 30% below the band" in out
    assert "advisory only" in out, "explainer is advisory only"

    # ------------------------------------------- explain: degrade + unknowns
    rc, out, err = run(["explain", "opp24", "--db", db], raise_disabled=True)
    assert rc == 0 and "LLM disabled - skipped" in out, (rc, out)

    rc, out, err = run(["explain", "no_such_opp", "--db", db],
                       fake=FakeLLM([]))
    assert rc == 1 and "not found" in err

    rc, out, err = run(["explain", "opp24", "--db", missing_db],
                       fake=FakeLLM([]))
    assert rc == 1 and "market database not found" in err
    assert not os.path.exists(missing_db)
finally:
    shutil.rmtree(tmp)

print("ALL TESTS PASSED")
print("  watchlist entries validated against the cite-or-assumption contract")
print("  daily brief payload: top-20 opportunities + 24h trendlines + hits")
print("  explain: probable-cause label over one opportunity's snapshots")
print("  all three subcommands degrade to 'LLM disabled - skipped' (exit 0)")
