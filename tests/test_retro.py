"""Headless tests for tools/retro.py: splits vs PB, level curve, deaths,
rendered table, LLM path with an injected fake, and degrade paths.
Offline — no network, no Qt, no real API keys; llm.client is faked via
sys.modules (retro.py imports it lazily inside generate_retro())."""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "tools")]

import retro                                   # noqa: E402

# ------------------------------------------------------- synthetic run + PB
RUN = {
    "league": "3.29", "character": "TestChar", "class": "Witch",
    "started": "2026-07-24T20:00:00", "ended": "2026-07-24T22:06:40",
    "splits": [
        {"act": 1, "t": 2700, "level": 12},
        {"act": 2, "t": 5100, "level": 19},
        {"act": 3, "t": 7500, "level": 24},
    ],
    "levels": [
        {"level": 2, "t": 95}, {"level": 5, "t": 600},
        {"level": 10, "t": 1800}, {"level": 12, "t": 2650},
        {"level": 15, "t": 3600}, {"level": 20, "t": 5400},
        {"level": 24, "t": 7400},
    ],
    "deaths": [
        {"t": 300, "who": "TestChar"},
        {"t": 1200, "who": "TestChar"},
        {"t": 5200, "who": "TestChar"},
    ],
}
PB = {
    "league": "3.28", "character": "OldChar", "class": "Witch",
    "started": "2026-04-04T10:00:00", "ended": "2026-04-04T12:10:00",
    "splits": [
        {"act": 1, "t": 2400, "level": 12},   # per-act PB splits:
        {"act": 2, "t": 5000, "level": 18},   # 2400, 2600, 2800
        {"act": 3, "t": 7800, "level": 25},
    ],
    "levels": [], "deaths": [],
}

tmp = tempfile.mkdtemp(prefix="poe_retro_test_")
try:
    run_path = os.path.join(tmp, "run_1753387200.json")
    pb_path = os.path.join(tmp, "pb.json")
    with open(run_path, "w", encoding="utf-8") as f:
        json.dump(RUN, f)
    with open(pb_path, "w", encoding="utf-8") as f:
        json.dump(PB, f)

    # ------------------------------------------------- per-act splits + deltas
    splits = retro.act_splits(RUN)
    assert [s["act"] for s in splits] == [1, 2, 3]
    assert [s["split"] for s in splits] == [2700, 2400, 2400]
    assert [s["cum"] for s in splits] == [2700, 5100, 7500]
    assert [s["level"] for s in splits] == [12, 19, 24]

    deltas = retro.split_deltas(splits, retro.act_splits(PB))
    assert deltas[1] == {"split": 300, "cum": 300}     # +5:00 slower
    assert deltas[2] == {"split": -200, "cum": 100}    # -3:20 faster
    assert deltas[3] == {"split": -400, "cum": -300}   # run beats PB overall
    assert retro.split_deltas(splits, []) == {}, "no PB -> no deltas"

    # ---------------------------------------------------------- level curve
    assert retro.level_milestones(RUN["levels"]) == [
        {"level": 10, "t": 1800}, {"level": 20, "t": 5400}]
    # skipped exact milestone: first level >= m wins
    assert retro.level_milestones(
        [{"level": 9, "t": 100}, {"level": 11, "t": 150}]) == \
        [{"level": 10, "t": 150}]
    assert retro.level_milestones([]) == []

    # --------------------------------------------------------------- deaths
    deaths, counts = retro.death_stats(RUN)
    assert [d["act"] for d in deaths] == [1, 1, 3]
    assert counts == {1: 2, 3: 1}
    assert all(d["who"] == "TestChar" for d in deaths)
    # past the last split -> next act
    d2, c2 = retro.death_stats(dict(RUN, deaths=[{"t": 9000, "who": "X"}]))
    assert d2[0]["act"] == 4 and c2 == {4: 1}

    # ------------------------------------------------------------ total time
    total, finished = retro.total_time(RUN)
    assert total == 7600 and finished          # 22:06:40 - 20:00:00
    total2, fin2 = retro.total_time(dict(RUN, ended=None))
    assert total2 == 7500 and not fin2         # falls back to max t seen

    # ------------------------------------------------------------ formatting
    assert retro.fmt_t(0) == "0:00"
    assert retro.fmt_t(2700) == "45:00"
    assert retro.fmt_t(7600) == "2:06:40"
    assert retro.fmt_delta(300) == "+5:00"
    assert retro.fmt_delta(-200) == "-3:20"
    assert retro.fmt_delta(0) == "+0:00"

    # -------------------------------------------------------- rendered table
    table = retro.render_table(RUN, PB)
    for frag in (
        "TestChar (Witch), league 3.29",
        "Total:   2:06:40  (finished)",
        "Deaths:  3",
        "(vs PB)",
        "45:00", "40:00", "1:25:00", "2:05:00",          # splits + cumulative
        "+5:00", "-3:20", "-6:40", "+1:40", "-5:00",     # exact PB deltas
        "Level milestones (every 10)",
        "30:00", "1:30:00",                              # level 10 / 20 times
        "Deaths (3)",
        "Deaths per act: act 1: 2, act 3: 1",
    ):
        assert frag in table, f"missing {frag!r} in table:\n{table}"
    assert "+300" not in table, "deltas must be rendered as times"

    table_nopb = retro.render_table(RUN, None)
    assert "(no PB)" in table_nopb and "+5:00" not in table_nopb

    empty = {"league": "3.29", "character": "X", "class": "Witch",
             "started": "2026-07-24T20:00:00", "ended": None,
             "splits": [], "levels": [], "deaths": []}
    t_empty = retro.render_table(empty, None)
    assert "0:00" in t_empty and "in progress" in t_empty
    assert "Deaths per act: none" in t_empty

    # ------------------------------------------------------------ CLI helper
    def run_cli(argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = retro.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def install_fake_llm(client_mod):
        pkg = types.ModuleType("llm")
        pkg.client = client_mod
        sys.modules["llm"] = pkg
        sys.modules["llm.client"] = client_mod

    class LLMDisabled(RuntimeError):
        pass

    class LLMError(RuntimeError):
        pass

    FAKE_RETRO = ("Solid run overall; act 1 bled five minutes to two deaths.\n"
                  "1. Skip the optional act 1 side areas.\n"
                  "2. Cap resists before the act 3 boss.\n"
                  "3. Bank a portal scroll for the lab trial.")

    # ------------------------------------------------ LLM path (injected fake)
    calls = []

    class FakeLLM:
        def __init__(self, tier):
            calls.append(("tier", tier))

        def complete(self, system, messages, max_tokens, feature,
                     json_schema=None):
            calls.append(("feature", feature))
            assert isinstance(system, str) and "EXACTLY three" in system
            assert messages[0]["role"] == "user"
            assert "Deaths per act: act 1: 2, act 3: 1" in \
                messages[0]["content"], "stats+deaths must reach the LLM"
            assert json_schema is None and max_tokens > 0
            return FAKE_RETRO

    fake = types.ModuleType("llm.client")
    fake.LLM = FakeLLM
    fake.LLMDisabled = LLMDisabled
    fake.LLMError = LLMError
    install_fake_llm(fake)

    rc, out, err = run_cli([run_path, "--pb", pb_path])
    assert rc == 0
    assert "+5:00" in out and "-3:20" in out
    assert retro.LLM_SECTION_HEADER in out and FAKE_RETRO in out
    assert out.index("Deaths per act") < out.index(FAKE_RETRO.split("\n")[0]), \
        "retro must be appended after the stats table"
    assert ("tier", "standard") in calls and ("feature", "retro") in calls

    # PB auto-detected from pb.json next to the run file when --pb omitted
    rc, out, _ = run_cli([run_path])
    assert rc == 0 and "+5:00" in out

    # --no-llm skips the LLM even though the fake works
    calls.clear()
    rc, out, _ = run_cli([run_path, "--pb", pb_path, "--no-llm"])
    assert rc == 0 and FAKE_RETRO not in out and calls == []

    # ------------------------------------------------ degrade: LLMDisabled
    class DisabledLLM:
        def __init__(self, tier):
            raise LLMDisabled("POE_TOOLS_LLM=off")

    fake.LLM = DisabledLLM
    rc, out, err = run_cli([run_path, "--pb", pb_path])
    assert rc == 0, "LLMDisabled must degrade to stats table, exit 0"
    assert "45:00" in out and "Deaths per act: act 1: 2, act 3: 1" in out
    assert FAKE_RETRO not in out and retro.LLM_SECTION_HEADER not in out
    assert "disabled" in err

    # ------------------------------------------------ degrade: LLMError
    class ErroringLLM:
        def __init__(self, tier):
            pass

        def complete(self, **kw):
            raise LLMError("boom")

    fake.LLM = ErroringLLM
    rc, out, err = run_cli([run_path, "--pb", pb_path])
    assert rc == 0 and "45:00" in out and retro.LLM_SECTION_HEADER not in out

    # ------------------------------------- degrade: llm package not importable
    sys.modules["llm"] = None          # forces ImportError on `import llm`
    sys.modules["llm.client"] = None
    rc, out, err = run_cli([run_path, "--pb", pb_path])
    assert rc == 0 and "45:00" in out and retro.LLM_SECTION_HEADER not in out
    assert "unavailable" in err

    # ------------------------------------------------------------ bad inputs
    rc, out, err = run_cli([os.path.join(tmp, "nope.json")])
    assert rc == 2 and out == ""
    # unreadable PB file is ignored with a warning; run still renders
    bad_pb = os.path.join(tmp, "bad_pb.json")
    with open(bad_pb, "w", encoding="utf-8") as f:
        f.write("{not json")
    rc, out, err = run_cli([run_path, "--pb", bad_pb, "--no-llm"])
    assert rc == 0 and "(no PB)" in out and "ignoring unreadable PB" in err
    # --pb pointing at a missing file: warn, render without PB
    rc, out, err = run_cli([run_path, "--pb", os.path.join(tmp, "ghost.json"),
                            "--no-llm"])
    assert rc == 0 and "(no PB)" in out and "PB file not found" in err
finally:
    for k in ("llm", "llm.client"):
        sys.modules.pop(k, None)
    shutil.rmtree(tmp)

print("ALL TESTS PASSED")
