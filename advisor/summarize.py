#!/usr/bin/env python3
"""Patch-note summarizer: raw patch notes -> data/<patch>/summary.json.

Usage:
  python advisor/summarize.py <patchnotes.txt> [--out data/3.29/summary.json]
                              [--patch 3.29]

Paste GGG's patch notes into a text file; the LLM (deep tier) converts
them into the structured summary format from docs/INTERFACES.md:

  {"patch": "3.29", "items": [{"id", "kind", "change", "direction",
                               "quote", "source"}]}

Every item carries a short verbatim quote as evidence — the downstream
tools (advise, exposure, market watchlist) cite these ids.

Degrades: when the LLM is unavailable (POE_TOOLS_LLM=off, missing API
key, or llm/client.py not present) this exits with a clear message —
there is no offline fallback for language understanding.
"""
import argparse
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:
    from llm.client import LLM, LLMDisabled, LLMError  # noqa: E402
except Exception:                                     # llm client absent
    LLM = None

    class LLMDisabled(RuntimeError):
        """Stand-in while llm/client.py does not exist yet."""

    class LLMError(RuntimeError):
        """Stand-in while llm/client.py does not exist yet."""

from advisor.prompts import SUMMARIZE_PROMPT          # noqa: E402

# Patch-note summary format from docs/INTERFACES.md.
SUMMARY_SCHEMA = {
    "type": "object",
    "required": ["patch", "items"],
    "properties": {
        "patch": {"type": "string"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "kind", "change", "direction",
                             "quote", "source"],
                "properties": {
                    "id": {"type": "string"},
                    "kind": {"type": "string",
                             "enum": ["skill", "support", "unique", "base",
                                      "keystone", "mechanic"]},
                    "change": {"type": "string"},
                    "direction": {"type": "string",
                                  "enum": ["buff", "nerf", "neutral"]},
                    "quote": {"type": "string"},
                    "source": {"type": "string"},
                },
            },
        },
    },
}


def summarize(text, patch="3.29", llm=None):
    """Raw patch-note text -> summary dict (INTERFACES.md format).

    Raises LLMDisabled when no LLM is available; callers degrade.
    """
    if llm is None:
        if LLM is None:
            raise LLMDisabled("llm/client.py is not available")
        llm = LLM("deep")
    data = llm.complete(
        system=SUMMARIZE_PROMPT,
        messages=[{"role": "user",
                   "content": f"Patch version: {patch}\n\n"
                              f"PATCH NOTES:\n{text}"}],
        max_tokens=8192,
        feature="advisor_summarize",
        json_schema=SUMMARY_SCHEMA,
    )
    if not data.get("patch"):
        data["patch"] = patch
    return data


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("notes", help="text file with the pasted patch notes")
    ap.add_argument("--out", default=None,
                    help="output path (default data/<patch>/summary.json)")
    ap.add_argument("--patch", default="3.29")
    a = ap.parse_args(argv)

    out = a.out or os.path.join("data", a.patch, "summary.json")
    with open(a.notes, encoding="utf-8") as f:
        text = f.read()
    if not text.strip():
        raise SystemExit(f"{a.notes} is empty — paste the patch notes in")

    try:
        data = summarize(text, a.patch)
    except LLMDisabled as e:
        raise SystemExit(
            f"LLM unavailable ({e}). Summarizing patch notes needs the LLM: "
            "set ANTHROPIC_API_KEY, unset POE_TOOLS_LLM=off, and make sure "
            "llm/client.py is in place. Nothing was written.")
    except LLMError as e:
        raise SystemExit(
            f"LLM call failed ({e}). Nothing was written — re-run once the "
            "API is reachable (the SDK already retried).")

    d = os.path.dirname(out)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"wrote {out} ({len(data['items'])} items, patch {data['patch']})")


if __name__ == "__main__":
    main()
