"""Shared LLM wrapper — the one API every LLM feature codes against.

Contract: docs/INTERFACES.md, section "llm/client.py".

    from llm.client import LLM, LLMDisabled, LLMError

    llm = LLM("standard")                    # tier: "fast" | "standard" | "deep"
    text = llm.complete(
        system="...",
        messages=[{"role": "user", "content": "..."}],   # or a plain str
        max_tokens=1024,
        feature="item_eval",                 # usage-meter tag, required
        json_schema=None,                    # if given -> returns validated dict
    )

Kill switch / degrade: constructing an ``LLM`` raises ``LLMDisabled`` when
``POE_TOOLS_LLM=off``, the ``anthropic`` package is missing, or no
``ANTHROPIC_API_KEY`` is set. Callers MUST catch ``LLMDisabled`` and degrade.

Every API call appends one JSON line to ``llm_usage.jsonl`` at the repo root:
``{"ts", "feature", "tier", "model", "in_tokens", "out_tokens"}``.
``tools/llm_report.py`` turns that file into a per-feature spend report.

Stdlib only apart from the guarded ``anthropic`` import below (the single
allowed exception per INTERFACES.md invariant 2). Import-safe: no side
effects at import time.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

try:  # guarded import — absence is a supported, degraded configuration
    import anthropic
except ImportError:  # pragma: no cover - exercised via monkeypatching in tests
    anthropic = None

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
DEFAULT_CONFIG_PATH = os.path.join(_HERE, "config.json")
DEFAULT_USAGE_PATH = os.path.join(_ROOT, "llm_usage.jsonl")


class LLMDisabled(RuntimeError):
    """LLM unavailable (kill switch, missing SDK, or missing API key).

    Every feature that uses the LLM must catch this and degrade gracefully.
    """


class LLMError(RuntimeError):
    """An LLM call failed: API error after SDK retries, a refusal
    (stop_reason == "refusal"), or structured output that failed validation
    even after one reprompt."""


# ---------------------------------------------------------------- validation

_JSON_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "null": type(None),
}


def _validate(value, schema, path="$"):
    """Minimal structural validation: required keys present, basic types.

    Returns a list of human-readable error strings (empty when valid).
    Deliberately not a full JSON-Schema implementation.
    """
    errors = []
    expected = schema.get("type")
    if expected in _JSON_TYPES:
        # bool is a subclass of int in Python — reject it for numeric types
        if expected in ("integer", "number") and isinstance(value, bool):
            return [f"{path}: expected {expected}, got boolean"]
        if not isinstance(value, _JSON_TYPES[expected]):
            return [f"{path}: expected {expected}, got {type(value).__name__}"]
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing required key '{key}'")
        for key, sub in schema.get("properties", {}).items():
            if key in value and isinstance(sub, dict):
                errors.extend(_validate(value[key], sub, f"{path}.{key}"))
    elif isinstance(value, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for i, item in enumerate(value):
                errors.extend(_validate(item, items, f"{path}[{i}]"))
    return errors


def _parse_and_validate(raw, schema):
    """json.loads *raw* and validate it. Returns (data, errors)."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as exc:
        return None, [f"not valid JSON: {exc}"]
    return data, _validate(data, schema)


# --------------------------------------------------------------------- LLM


class LLM:
    """Thin wrapper over the Anthropic Messages API with tier -> model
    mapping, a kill switch, structured-output validation, and usage metering.

    ``client`` and ``usage_path`` are injectable for offline tests.
    """

    def __init__(self, tier, *, config_path=None, usage_path=None, client=None):
        _check_kill_switch()
        cfg = self._load_config(config_path or DEFAULT_CONFIG_PATH)
        models = cfg.get("models", {})
        if tier not in models:
            raise ValueError(
                f"unknown LLM tier {tier!r}; expected one of {sorted(models)}")
        self.tier = tier
        self.model = models[tier]
        self.max_retries = int(cfg.get("max_retries", 3))
        self.usage_path = usage_path or DEFAULT_USAGE_PATH
        if client is not None:
            self._client = client
        else:
            if anthropic is None:
                raise LLMDisabled("the 'anthropic' package is not installed")
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise LLMDisabled("ANTHROPIC_API_KEY is not set")
            # Transient errors: rely on SDK retries, then raise LLMError.
            self._client = anthropic.Anthropic(max_retries=self.max_retries)

    # ------------------------------------------------------------- public

    def complete(self, system, messages, max_tokens, feature, json_schema=None):
        """One completion. Returns ``str`` (plain) or ``dict`` (json_schema).

        ``messages`` may be a plain str as shorthand for a single user turn.
        Raises ``LLMDisabled`` if the kill switch was flipped, ``LLMError``
        on API failure, refusal, or unrecoverable invalid structured output.
        """
        _check_kill_switch()
        if not feature or not isinstance(feature, str):
            raise ValueError("feature tag (str) is required for the usage meter")
        if isinstance(messages, str):
            messages = [{"role": "user", "content": messages}]

        if json_schema is None:
            return self._all_text(self._call(system, messages, max_tokens, feature))

        output_config = {"format": {"type": "json_schema", "schema": json_schema}}
        resp = self._call(system, messages, max_tokens, feature,
                          output_config=output_config)
        raw = self._first_text(resp)
        data, errors = _parse_and_validate(raw, json_schema)
        if not errors:
            return data

        # One reprompt carrying the validation error, then give up.
        retry_messages = list(messages) + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": (
                "Your previous reply failed validation: " + "; ".join(errors)
                + ". Respond again with only a single JSON object that"
                  " matches the required schema exactly — no prose.")},
        ]
        resp = self._call(system, retry_messages, max_tokens, feature,
                          output_config=output_config)
        raw = self._first_text(resp)
        data, errors = _parse_and_validate(raw, json_schema)
        if errors:
            raise LLMError(
                "structured output failed validation after one retry: "
                + "; ".join(errors))
        return data

    # ------------------------------------------------------------ internal

    @staticmethod
    def _load_config(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def _call(self, system, messages, max_tokens, feature, output_config=None):
        kwargs = {"model": self.model, "max_tokens": max_tokens,
                  "messages": messages}
        if system is not None:
            kwargs["system"] = system
        if output_config is not None:
            kwargs["output_config"] = output_config
        try:
            resp = self._client.messages.create(**kwargs)
        except Exception as exc:  # SDK has already retried max_retries times
            raise LLMError(f"LLM API call failed: {exc}") from exc
        self._record_usage(feature, resp)
        # Check stop_reason BEFORE reading content — a refusal may carry an
        # empty or partial content array.
        if getattr(resp, "stop_reason", None) == "refusal":
            raise LLMError("model refused the request (stop_reason=refusal)")
        return resp

    @staticmethod
    def _text_blocks(resp):
        blocks = getattr(resp, "content", None) or []
        return [b.text for b in blocks if getattr(b, "type", None) == "text"]

    def _all_text(self, resp):
        texts = self._text_blocks(resp)
        if not texts:
            raise LLMError("response contained no text block")
        return "".join(texts)

    def _first_text(self, resp):
        texts = self._text_blocks(resp)
        if not texts:
            raise LLMError("response contained no text block")
        return texts[0]

    def _record_usage(self, feature, resp):
        usage = getattr(resp, "usage", None)
        line = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "feature": feature,
            "tier": self.tier,
            "model": self.model,
            "in_tokens": int(getattr(usage, "input_tokens", 0) or 0),
            "out_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        }
        try:
            with open(self.usage_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(line) + "\n")
        except OSError:
            pass  # metering is best-effort; never break the feature call


def _check_kill_switch():
    if os.environ.get("POE_TOOLS_LLM", "").strip().lower() == "off":
        raise LLMDisabled("POE_TOOLS_LLM=off")
