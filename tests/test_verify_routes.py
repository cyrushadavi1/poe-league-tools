"""Headless tests for tools/verify_routes_llm.py: the HTML-to-text stripper,
the wiki cache-hit path (fake fetcher), the deterministic route checks, the
LLM layer with an injected fake (which asserts the corrupted route lines and
the wiki text actually reach the prompt), the LLMDisabled degrade path, and
the advisory guarantee (route files are never edited).
Offline: fake fetcher + sys.modules-faked llm.client. No network, no Qt,
no real API keys."""
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

import verify_routes_llm as vr                 # noqa: E402

# ------------------------------------------------------------ slug / url
assert vr.slugify("Act 1") == "Act_1"
assert vr.slugify("The Siren's Cadence") == "The_Siren_s_Cadence"
assert vr.page_url("Act 1") == "https://www.poewiki.net/wiki/Act_1"
assert vr.page_url("The Siren's Cadence") == \
    "https://www.poewiki.net/wiki/The_Siren%27s_Cadence"
assert vr.pages_for_act(1)[0] == "Act 1"
assert "Trial of Ascendancy" in vr.pages_for_act(1)
assert "Mercy Mission" in vr.pages_for_act(1)
assert "Trial of Ascendancy" not in vr.pages_for_act(4), \
    "act 4 has no labyrinth trial"

# ------------------------------------------------------ HTML -> text strip
HTML_DOC = """<html><head><title>Act 1</title>
<style>.x{color:red}</style><script>alert("nope");</script></head>
<body><h2>Quests &amp; Trials</h2>
<p>Mercy&nbsp;Mission gives a   Quicksilver Flask</p>
<ul><li>Trial of Ascendancy</li><li>Nessa &#8594; flask</li></ul>
<table><tr><td>The Coast</td><td>Level 2</td></tr></table>
</body></html>"""
text = vr.strip_html(HTML_DOC)
assert "alert" not in text and "color:red" not in text, "script/style stripped"
assert "Quests & Trials" in text, "entities decoded"
assert "Mercy Mission gives a Quicksilver Flask" in text, \
    "nbsp + runs of spaces collapsed"
assert "Nessa → flask" in text, "numeric charref decoded"
assert "Trial of Ascendancy" in text
assert "The Coast Level 2" in text, "table cells separated by a space"
assert "\n\n" not in text and not text.startswith("\n"), "no blank lines"

tmp = tempfile.mkdtemp(prefix="poe_verify_test_")
try:
    # ------------------------------------------------------ cache-hit path
    cache = os.path.join(tmp, "wiki_cache")
    calls = []

    def counting_fetch(url):
        calls.append(url)
        return f"<html><body><p>page for {url}</p></body></html>"

    h1, u1, hit1 = vr.get_page("Act 1", cache, fetch=counting_fetch)
    assert not hit1 and calls == [vr.page_url("Act 1")]
    assert os.path.exists(os.path.join(cache, "Act_1.html"))
    h2, u2, hit2 = vr.get_page("Act 1", cache, fetch=counting_fetch)
    assert hit2 and h2 == h1 and u2 == u1
    assert len(calls) == 1, "cache hit must skip the fetch"
    vr.get_page("Act 1", cache, fetch=counting_fetch, refresh=True)
    assert len(calls) == 2, "--refresh forces a refetch"

    # ------------------------------------------------ deterministic checks
    bad = vr.deterministic_findings("{not json", 1)
    assert len(bad) == 1 and bad[0]["severity"] == "error"
    assert "JSON" in bad[0]["issue"] and bad[0]["layer"] == "deterministic"

    assert vr.deterministic_findings("[1, 2]", 1)[0]["severity"] == "error"
    assert vr.deterministic_findings('{"act": 1, "steps": []}', 1)[0][
        "issue"].startswith("'steps'")

    broken = {"act": 2, "steps": [
        {"zone": "A", "kind": "walk", "do": ["x"]},           # bad kind enum
        {"zone": "B", "kind": "travel", "do": []},            # empty do
        {"zone": "B", "kind": "town", "do": ["y"]},           # consecutive dup
        {"kind": "kill", "do": ["z"]},                        # missing zone
        {"zone": "C", "kind": "trial", "do": ["k"],
         "arealvl": "9", "tip": 7},                           # bad types
    ]}
    fnd = vr.deterministic_findings(json.dumps(broken), 1)
    by_issue = " | ".join(f"{f['step_id']} {f['issue']}" for f in fnd)
    assert any(f["step_id"] == "0:A" and "kind" in f["issue"]
               for f in fnd), by_issue
    assert any(f["step_id"] == "1:B" and "'do'" in f["issue"]
               for f in fnd), by_issue
    assert any(f["step_id"] == "2:B" and "duplicate consecutive" in f["issue"]
               for f in fnd), by_issue
    assert any(f["step_id"] == "3:?" and "'zone'" in f["issue"]
               for f in fnd), by_issue
    assert any(f["step_id"] == "4:C" and "arealvl" in f["issue"]
               for f in fnd), by_issue
    assert any(f["step_id"] == "4:C" and "'tip'" in f["issue"]
               for f in fnd), by_issue
    assert any(f["severity"] == "warn" and "'act'" in f["issue"]
               for f in fnd), "act-number mismatch is a warn"
    assert all(f["layer"] == "deterministic" for f in fnd)

    with open(os.path.join(ROOT, "routes", "act1.json"),
              encoding="utf-8") as f:
        clean_raw = f.read()
    clean_fnd = vr.deterministic_findings(clean_raw, 1)
    assert not [f for f in clean_fnd if f["severity"] == "error"], \
        f"clean act1 must yield 0 deterministic errors: {clean_fnd}"

    # ------------------------------------------- corrupted act1 (3 plants)
    data = json.loads(clean_raw)
    steps = data["steps"]
    # plant 1: wrong reward NPC in a do-line (Quicksilver is Nessa's reward)
    mercy = next(s for s in steps
                 if any("QUICKSILVER" in d for d in s["do"]))
    mercy["do"] = [d.replace("(Nessa)", "(Bestel)") for d in mercy["do"]]
    assert any("QUICKSILVER FLASK (Bestel)" in d for d in mercy["do"])
    # plant 2: remove the trial step entirely
    trial_i = next(i for i, s in enumerate(steps) if s["kind"] == "trial")
    removed = steps.pop(trial_i)
    assert removed["zone"] == "The Lower Prison"
    # plant 3: fake skill point claim
    FAKE_CLAIM = ("Also collect the +1 passive skill point from "
                  "Enemy at the Gate (Tarkleigh)")
    steps[1]["do"].insert(1, FAKE_CLAIM)

    routes_dir = os.path.join(tmp, "routes")
    os.makedirs(routes_dir)
    corrupted_raw = json.dumps(data, indent=1)
    corr_path = os.path.join(routes_dir, "act1.json")
    with open(corr_path, "w", encoding="utf-8") as f:
        f.write(corrupted_raw)

    # --------------------------------------------------- fake wiki + fetch
    WIKI = {
        vr.page_url("Act 1"):
            "<html><body><h1>Act 1</h1><p>The Lower Prison contains the "
            "first Trial of Ascendancy for the Labyrinth.</p></body></html>",
        vr.page_url("Mercy Mission"):
            "<html><body><h1>Mercy Mission</h1><p>Nessa rewards a "
            "Quicksilver Flask for slaying Hailrake.</p></body></html>",
        vr.page_url("Enemy at the Gate"):
            "<html><body><p>Enemy at the Gate: the reward is a skill gem "
            "from Tarkleigh. It grants no passive skill point."
            "</p></body></html>",
    }
    fetch_log = []

    def wiki_fetch(url):
        fetch_log.append(url)
        return WIKI.get(url, f"<html><body><p>stub page {url}</p></body></html>")

    def no_fetch(url):
        raise AssertionError(f"unexpected network fetch: {url}")

    # ------------------------------------------------------- fake llm.client
    def install_fake_llm(client_mod):
        pkg = types.ModuleType("llm")
        pkg.client = client_mod
        sys.modules["llm"] = pkg
        sys.modules["llm.client"] = client_mod

    class LLMDisabled(RuntimeError):
        pass

    class LLMError(RuntimeError):
        pass

    seen = {}

    class FakeLLM:
        """Returns a finding per planted corruption visible in the prompt,
        asserting the prompt really carries the route lines + wiki text."""

        def __init__(self, tier):
            seen["tier"] = tier

        def complete(self, system, messages, max_tokens, feature,
                     json_schema=None):
            prompt = (messages if isinstance(messages, str)
                      else messages[0]["content"])
            seen.update(feature=feature, prompt=prompt, max_tokens=max_tokens)
            assert json_schema is vr.FINDINGS_SCHEMA
            assert isinstance(system, str) and "advisory" in system.lower()
            assert "SOURCE: " + vr.page_url("Mercy Mission") in prompt, \
                "wiki source urls must reach the model"
            assert "Nessa rewards a Quicksilver Flask" in prompt, \
                "stripped wiki text must reach the model"
            assert "Trial of Ascendancy for the Labyrinth" in prompt
            findings = []
            if "QUICKSILVER FLASK (Bestel)" in prompt:      # plant 1 present
                findings.append({
                    "step_id": "4:Lioneye's Watch", "severity": "error",
                    "issue": "Quicksilver Flask is Nessa's reward, not "
                             "Bestel's",
                    "evidence": "Nessa rewards a Quicksilver Flask",
                    "source_url": vr.page_url("Mercy Mission")})
            if '"zone": "The Lower Prison"' not in prompt:  # plant 2: removed
                findings.append({
                    "step_id": "missing:The Lower Prison", "severity": "error",
                    "issue": "route skips the Trial of Ascendancy in The "
                             "Lower Prison",
                    "evidence": "The Lower Prison contains the first Trial "
                                "of Ascendancy",
                    "source_url": vr.page_url("Act 1")})
            if FAKE_CLAIM in prompt:                        # plant 3 present
                findings.append({
                    "step_id": "1:Lioneye's Watch", "severity": "error",
                    "issue": "Enemy at the Gate rewards a gem, not a "
                             "passive skill point",
                    "evidence": "It grants no passive skill point",
                    "source_url": vr.page_url("Enemy at the Gate")})
            return {"findings": findings}

    fake = types.ModuleType("llm.client")
    fake.LLM = FakeLLM
    fake.LLMDisabled = LLMDisabled
    fake.LLMError = LLMError
    install_fake_llm(fake)

    def run_cli(argv, fetch):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = vr.main(argv, fetch=fetch)
        return rc, out.getvalue(), err.getvalue()

    out_dir = os.path.join(tmp, "out")
    base_args = ["--route-dir", routes_dir, "--cache-dir",
                 os.path.join(tmp, "cache2"), "--out-dir", out_dir]

    # ------------------------------------- corrupted route -> >= 3 findings
    rc, out, err = run_cli(["1"] + base_args, fetch=wiki_fetch)
    assert rc == 0, err
    assert seen["tier"] == "standard" and seen["feature"] == "route_verify"
    prompt = seen["prompt"]
    assert "QUICKSILVER FLASK (Bestel)" in prompt, \
        "corrupted NPC line must reach the model"
    assert FAKE_CLAIM in prompt, "fake skill-point line must reach the model"
    assert '"zone": "The Lower Prison"' not in prompt, \
        "removed trial step must be absent from the prompt"
    assert '"zone": "The Upper Prison"' in prompt, \
        "the rest of the route JSON must be in the prompt verbatim"

    rep_path = os.path.join(out_dir, "verify_act1.json")
    with open(rep_path, encoding="utf-8") as f:
        rep = json.load(f)
    llm_fnd = [f for f in rep["findings"] if f["layer"] == "llm"]
    assert rep["llm_ran"] and len(llm_fnd) >= 3, \
        f"all 3 planted errors must surface: {llm_fnd}"
    assert all(f["severity"] == "error" for f in llm_fnd)
    joined = " ".join(f["issue"] for f in llm_fnd)
    assert "Nessa" in joined and "Trial" in joined and "skill point" in joined
    assert all(f["evidence"] and f["source_url"] for f in llm_fnd), \
        "findings carry evidence quotes + source urls"
    det_err = [f for f in rep["findings"]
               if f["layer"] == "deterministic" and f["severity"] == "error"]
    assert not det_err, "semantic plants pass the deterministic layer"
    # readable table on stdout
    assert "STEP" in out and "ISSUE" in out
    assert "4:Lioneye's Watch" in out and "missing:The Lower Prison" in out
    assert "advisory only" in out
    # ADVISORY ONLY: the tool never edits route files
    with open(corr_path, encoding="utf-8") as f:
        assert f.read() == corrupted_raw, "route file must be untouched"

    # ------------------------- second run: all pages cached, zero fetches
    n_fetched = len(fetch_log)
    assert n_fetched == len(vr.pages_for_act(1))
    rc, out, _ = run_cli(["1"] + base_args, fetch=no_fetch)
    assert rc == 0 and len(fetch_log) == n_fetched
    assert "Nessa rewards a Quicksilver Flask" in seen["prompt"], \
        "cached wiki text still reaches the model"

    # --------------------------------------- clean act1 -> zero findings
    clean_dir = os.path.join(tmp, "routes_clean")
    os.makedirs(clean_dir)
    with open(os.path.join(clean_dir, "act1.json"), "w",
              encoding="utf-8") as f:
        f.write(clean_raw)
    clean_out = os.path.join(tmp, "out_clean")
    rc, out, _ = run_cli(["1", "--route-dir", clean_dir, "--cache-dir",
                          os.path.join(tmp, "cache2"), "--out-dir",
                          clean_out], fetch=no_fetch)
    assert rc == 0
    with open(os.path.join(clean_out, "verify_act1.json"),
              encoding="utf-8") as f:
        rep = json.load(f)
    assert rep["llm_ran"] and rep["findings"] == [], \
        f"clean act1 must yield 0 findings: {rep['findings']}"
    assert "no findings" in out and "0 error(s)" in out

    # ------------------------------------------- degrade: LLMDisabled
    class DisabledLLM:
        def __init__(self, tier):
            raise LLMDisabled("POE_TOOLS_LLM=off")

    fake.LLM = DisabledLLM
    rc, out, err = run_cli(["1", "--route-dir", clean_dir, "--cache-dir",
                            os.path.join(tmp, "cache3"), "--out-dir",
                            clean_out], fetch=no_fetch)
    assert rc == 0, "LLMDisabled must degrade, not crash"
    assert "skipped" in out.lower() and "deterministic" in out.lower(), \
        "output must say the LLM layer was skipped"
    assert not os.path.exists(os.path.join(tmp, "cache3", "Act_1.html")), \
        "no wiki fetching when the LLM is disabled (and no_fetch not hit)"
    with open(os.path.join(clean_out, "verify_act1.json"),
              encoding="utf-8") as f:
        rep = json.load(f)
    assert not rep["llm_ran"] and "skipped" in rep["note"]
    assert not [f for f in rep["findings"] if f["severity"] == "error"], \
        "clean act1 -> 0 errors from the deterministic layer"

    # ------------------------------------------- degrade: LLMError mid-call
    class ErroringLLM:
        def __init__(self, tier):
            pass

        def complete(self, **kw):
            raise LLMError("boom")

    fake.LLM = ErroringLLM
    rc, out, _ = run_cli(["1"] + base_args, fetch=wiki_fetch)
    assert rc == 0 and "failed" in out.lower()
    with open(rep_path, encoding="utf-8") as f:
        assert not json.load(f)["llm_ran"]

    # ------------------------------------------------ --no-llm short-circuit
    class ExplodingLLM:
        def __init__(self, tier):
            raise AssertionError("--no-llm must never construct the LLM")

    fake.LLM = ExplodingLLM
    rc, out, _ = run_cli(["1", "--no-llm"] + base_args, fetch=no_fetch)
    assert rc == 0 and "--no-llm" in out

    # --------------------------------------------------- CLI arg handling
    fake.LLM = FakeLLM
    rc, out, err = run_cli(["all"] + base_args, fetch=wiki_fetch)
    assert rc == 0 and "act 2: no route file" in out, \
        "'all' skips missing acts and keeps going"
    assert "=== act 1" in out

    rc, _, err = run_cli(["7"] + base_args, fetch=no_fetch)
    assert rc == 2 and "no route file" in err, \
        "explicit act with no route file is an error"
    rc, _, err = run_cli(["11"] + base_args, fetch=no_fetch)
    assert rc == 2 and "invalid act" in err
    rc, _, err = run_cli(["nope"] + base_args, fetch=no_fetch)
    assert rc == 2 and "invalid act" in err
finally:
    for k in ("llm", "llm.client"):
        sys.modules.pop(k, None)
    shutil.rmtree(tmp)

print("ALL TESTS PASSED")
print("  planted corruptions surfaced: wrong NPC, removed trial, fake "
      "skill point")
