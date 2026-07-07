"""Headless tests: llm.client (kill switch, completion, json_schema,
refusal, usage meter) and tools/llm_report. Offline — the anthropic client
is a fake object; no network, no Qt, no real API keys are ever used."""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "tools")]

# Neutralise the environment BEFORE constructing anything, so a real key on
# this machine can never be picked up.
_SAVED_ENV = {k: os.environ.pop(k, None)
              for k in ("POE_TOOLS_LLM", "ANTHROPIC_API_KEY")}

import llm.client as llm_client                 # noqa: E402
from llm.client import LLM, LLMDisabled, LLMError  # noqa: E402
import llm_report                               # noqa: E402


# ------------------------------------------------------------------- fakes
class Obj:
    """Attribute bag standing in for SDK response objects."""
    def __init__(self, **kw):
        self.__dict__.update(kw)


def make_response(text, stop_reason="end_turn", in_tokens=100, out_tokens=20,
                  content=None):
    if content is None:
        content = [Obj(type="text", text=text)]
    return Obj(content=content, stop_reason=stop_reason,
               usage=Obj(input_tokens=in_tokens, output_tokens=out_tokens))


class FakeMessages:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class FakeClient:
    def __init__(self, *responses):
        self.messages = FakeMessages(responses)


tmp = tempfile.mkdtemp(prefix="poe_llm_test_")
try:
    # ------------------------------------------------------- kill switch
    assert issubclass(LLMDisabled, RuntimeError)

    os.environ["POE_TOOLS_LLM"] = "off"
    try:
        LLM("fast", client=FakeClient())
        assert False, "POE_TOOLS_LLM=off must raise LLMDisabled"
    except LLMDisabled:
        pass
    # ... even on an already-constructed instance
    del os.environ["POE_TOOLS_LLM"]
    llm = LLM("fast", client=FakeClient(make_response("x")),
              usage_path=os.path.join(tmp, "kill.jsonl"))
    os.environ["POE_TOOLS_LLM"] = "off"
    try:
        llm.complete(system="s", messages="m", max_tokens=8, feature="t")
        assert False, "kill switch must also gate complete()"
    except LLMDisabled:
        pass
    del os.environ["POE_TOOLS_LLM"]

    # missing anthropic package -> LLMDisabled (no client injected)
    _real_anthropic = llm_client.anthropic
    llm_client.anthropic = None
    try:
        LLM("fast")
        assert False, "missing anthropic package must raise LLMDisabled"
    except LLMDisabled:
        pass

    # package "present" but no ANTHROPIC_API_KEY -> LLMDisabled
    llm_client.anthropic = Obj()   # never reached past the key check
    assert "ANTHROPIC_API_KEY" not in os.environ
    try:
        LLM("standard")
        assert False, "missing ANTHROPIC_API_KEY must raise LLMDisabled"
    except LLMDisabled:
        pass
    # Keep anthropic=None for the rest of the run: any accidental
    # real-client path raises LLMDisabled instead of touching the network.
    llm_client.anthropic = None

    # unknown tier is a programming error, not a degrade case
    try:
        LLM("turbo", client=FakeClient())
        assert False, "unknown tier must raise ValueError"
    except ValueError:
        pass

    # --------------------------------------------- plain-text completion
    usage_path = os.path.join(tmp, "usage.jsonl")
    fake = FakeClient(make_response("hello exile", in_tokens=120, out_tokens=7))
    llm = LLM("standard", client=fake, usage_path=usage_path)
    out = llm.complete(system="sys prompt",
                       messages=[{"role": "user", "content": "hi"}],
                       max_tokens=64, feature="item_eval")
    assert out == "hello exile"
    call = fake.messages.calls[0]
    assert call["model"] == "claude-sonnet-5", "standard tier -> sonnet"
    assert call["system"] == "sys prompt"
    assert call["max_tokens"] == 64
    assert call["messages"] == [{"role": "user", "content": "hi"}]
    assert "output_config" not in call, "no schema -> no output_config"

    with open(usage_path, encoding="utf-8") as f:
        records = [json.loads(line) for line in f]
    assert len(records) == 1, "exactly one usage line per API call"
    rec = records[0]
    assert rec["feature"] == "item_eval"
    assert rec["tier"] == "standard"
    assert rec["model"] == "claude-sonnet-5"
    assert rec["in_tokens"] == 120 and rec["out_tokens"] == 7
    assert rec["ts"], "usage line must carry a timestamp"

    # missing feature tag is rejected
    try:
        llm.complete(system="s", messages="m", max_tokens=8, feature="")
        assert False, "empty feature tag must raise ValueError"
    except ValueError:
        pass

    # ------------------------------------------------------ str shorthand
    fake = FakeClient(make_response("ok"))
    llm = LLM("fast", client=fake, usage_path=os.path.join(tmp, "u2.jsonl"))
    llm.complete(system="s", messages="just a string", max_tokens=10,
                 feature="tradeq")
    assert fake.messages.calls[0]["messages"] == \
        [{"role": "user", "content": "just a string"}]
    assert fake.messages.calls[0]["model"] == "claude-haiku-4-5"

    # -------------------------------------------------- json_schema: happy
    SCHEMA = {
        "type": "object",
        "properties": {"name": {"type": "string"},
                       "score": {"type": "integer"}},
        "required": ["name", "score"],
        "additionalProperties": False,
    }
    fake = FakeClient(make_response('{"name": "Rolling Magma", "score": 7}'))
    llm = LLM("deep", client=fake, usage_path=os.path.join(tmp, "u3.jsonl"))
    data = llm.complete(system="s", messages="rate it", max_tokens=100,
                        feature="brief", json_schema=SCHEMA)
    assert data == {"name": "Rolling Magma", "score": 7}
    call = fake.messages.calls[0]
    assert call["model"] == "claude-opus-4-8", "deep tier -> opus"
    assert call["output_config"] == \
        {"format": {"type": "json_schema", "schema": SCHEMA}}

    # ------------------------------------- json_schema: invalid then valid
    u4 = os.path.join(tmp, "u4.jsonl")
    fake = FakeClient(
        make_response('{"name": "x"}'),                # missing 'score'
        make_response('{"name": "x", "score": 3}'),    # fixed on reprompt
    )
    llm = LLM("deep", client=fake, usage_path=u4)
    data = llm.complete(system="s", messages="rate it", max_tokens=100,
                        feature="brief", json_schema=SCHEMA)
    assert data == {"name": "x", "score": 3}
    assert len(fake.messages.calls) == 2, "exactly one reprompt"
    retry_msgs = fake.messages.calls[1]["messages"]
    assert retry_msgs[0] == {"role": "user", "content": "rate it"}
    assert retry_msgs[-2] == {"role": "assistant", "content": '{"name": "x"}'}
    assert retry_msgs[-1]["role"] == "user", "no trailing assistant prefill"
    assert "score" in retry_msgs[-1]["content"], \
        "reprompt must carry the validation error"
    assert fake.messages.calls[1]["output_config"] == \
        {"format": {"type": "json_schema", "schema": SCHEMA}}
    with open(u4, encoding="utf-8") as f:
        assert len(f.readlines()) == 2, "one usage line per API call (2 calls)"

    # unparseable JSON first, then valid -> also recovered by the reprompt
    fake = FakeClient(
        make_response("Sure! Here you go: name=x score=3"),
        make_response('{"name": "x", "score": 3}'),
    )
    llm = LLM("deep", client=fake, usage_path=os.path.join(tmp, "u5.jsonl"))
    data = llm.complete(system="s", messages="rate it", max_tokens=100,
                        feature="brief", json_schema=SCHEMA)
    assert data["score"] == 3 and len(fake.messages.calls) == 2

    # ------------------------------------- json_schema: invalid twice -> error
    fake = FakeClient(
        make_response('{"name": "x", "score": "high"}'),   # wrong type
        make_response('{"name": "x", "score": "high"}'),   # still wrong
    )
    llm = LLM("deep", client=fake, usage_path=os.path.join(tmp, "u6.jsonl"))
    try:
        llm.complete(system="s", messages="rate it", max_tokens=100,
                     feature="brief", json_schema=SCHEMA)
        assert False, "invalid twice must raise LLMError"
    except LLMError as exc:
        assert "score" in str(exc)
    assert len(fake.messages.calls) == 2, "exactly one retry, then give up"

    # ---------------------------------------------------- refusal -> LLMError
    # Refusal is checked BEFORE content is read (content may be empty).
    fake = FakeClient(make_response("", stop_reason="refusal", content=[]))
    llm = LLM("standard", client=fake, usage_path=os.path.join(tmp, "u7.jsonl"))
    try:
        llm.complete(system="s", messages="m", max_tokens=8, feature="t")
        assert False, "refusal must raise LLMError"
    except LLMError as exc:
        assert "refus" in str(exc).lower()

    # ------------------------------------------- API exception -> LLMError
    class BoomMessages:
        def create(self, **kwargs):
            raise RuntimeError("boom: connection reset")

    llm = LLM("fast", client=Obj(messages=BoomMessages()),
              usage_path=os.path.join(tmp, "u8.jsonl"))
    try:
        llm.complete(system="s", messages="m", max_tokens=8, feature="t")
        assert False, "SDK exception must surface as LLMError"
    except LLMError as exc:
        assert "boom" in str(exc)

    # ------------------------------------------------------- llm_report
    prices = {"claude-haiku-4-5": [1.0, 5.0],
              "claude-sonnet-5": [3.0, 15.0],
              "claude-opus-4-8": [5.0, 25.0]}
    synth = os.path.join(tmp, "synthetic.jsonl")
    rows = [
        {"ts": "t", "feature": "brief", "tier": "fast",
         "model": "claude-haiku-4-5", "in_tokens": 1_000_000,
         "out_tokens": 200_000},                       # $1.00 + $1.00 = $2.00
        {"ts": "t", "feature": "brief", "tier": "standard",
         "model": "claude-sonnet-5", "in_tokens": 100_000,
         "out_tokens": 10_000},                        # $0.30 + $0.15 = $0.45
        {"ts": "t", "feature": "advisor", "tier": "deep",
         "model": "claude-opus-4-8", "in_tokens": 200_000,
         "out_tokens": 40_000},                        # $1.00 + $1.00 = $2.00
        {"ts": "t", "feature": "retro", "tier": "fast",
         "model": "mystery-model", "in_tokens": 50_000,
         "out_tokens": 5_000},                         # unpriced
    ]
    with open(synth, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
        f.write("this line is not json\n")

    per, totals = llm_report.aggregate(synth, prices)
    assert set(per) == {"brief", "advisor", "retro"}
    assert per["brief"]["calls"] == 2
    assert per["brief"]["in_tokens"] == 1_100_000
    assert per["brief"]["out_tokens"] == 210_000
    assert abs(per["brief"]["cost"] - 2.45) < 1e-9
    assert abs(per["advisor"]["cost"] - 2.00) < 1e-9
    assert per["retro"]["cost"] == 0.0
    assert per["retro"]["unknown_models"] == {"mystery-model"}
    assert totals["calls"] == 4
    assert totals["in_tokens"] == 1_350_000
    assert totals["out_tokens"] == 255_000
    assert abs(totals["cost"] - 4.45) < 1e-9
    assert totals["skipped"] == 1

    text = llm_report.format_report(per, totals)
    assert "brief" in text and "advisor" in text and "TOTAL" in text
    assert "2.4500" in text and "4.4500" in text
    assert "mystery-model" in text, "unpriced models are called out"

    # a file written by the real client aggregates too
    per2, totals2 = llm_report.aggregate(u4, prices)
    assert totals2["calls"] == 2 and set(per2) == {"brief"}

    # missing and empty files are handled
    per3, totals3 = llm_report.aggregate(os.path.join(tmp, "nope.jsonl"), prices)
    assert per3 == {} and totals3["calls"] == 0 and totals3["cost"] == 0.0
    empty = os.path.join(tmp, "empty.jsonl")
    open(empty, "w").close()
    per4, _ = llm_report.aggregate(empty, prices)
    assert per4 == {}
    assert llm_report.main([os.path.join(tmp, "nope.jsonl")]) == 0
    assert llm_report.main([synth]) == 0

finally:
    shutil.rmtree(tmp)
    llm_client.anthropic = _real_anthropic
    for key, value in _SAVED_ENV.items():
        if value is not None:
            os.environ[key] = value

print("ALL TESTS PASSED")
