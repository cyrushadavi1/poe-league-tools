#!/usr/bin/env python3
"""Nerf-exposure report: one PoB code vs. the patch-note summary.

Usage:
  python advisor/exposure.py <code-or-file> \\
      [--summary data/3.29/summary.json] [--out exposure_witch.md]

Extracts the build's components deterministically — gems (skill sets),
uniques (Items blocks), keystones (best-effort; tree-allocated keystones
cannot be resolved offline, see pob.extract_keystones) — then asks the
LLM (standard tier) to map each component against the patch summary.

Output: a markdown table {component, change, direction, source, quote}
plus an overall verdict, written to exposure_<class>.md (or --out).
Components the summary does not mention are tagged source=assumption.

Degrades: when the LLM or the summary file is missing, every component
is listed with 'no patch data' and the verdict says why.
"""
import argparse
import json
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "buildgen")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from llm.client import LLM, LLMDisabled, LLMError  # noqa: E402
except Exception:                                     # llm client absent
    LLM = None

    class LLMDisabled(RuntimeError):
        """Stand-in while llm/client.py does not exist yet."""

    class LLMError(RuntimeError):
        """Stand-in while llm/client.py does not exist yet."""

import pob                                            # noqa: E402
from advisor.prompts import EXPOSURE_PROMPT           # noqa: E402

EXPOSURE_SCHEMA = {
    "type": "object",
    "required": ["rows", "verdict"],
    "properties": {
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["component", "change", "direction",
                             "source", "quote"],
                "properties": {
                    "component": {"type": "string"},
                    "change": {"type": "string"},
                    "direction": {"type": "string",
                                  "enum": ["buff", "nerf", "neutral",
                                           "unknown"]},
                    "source": {"type": "string"},
                    "quote": {"type": "string"},
                },
            },
        },
        "verdict": {"type": "string"},
    },
}


def load_summary(path):
    """summary.json -> dict, or None when missing/corrupt (degrade)."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return data
    except (OSError, ValueError):
        pass
    return None


def collect_components(root):
    """Deterministic component list -> [(kind, name)], deduplicated."""
    comps, seen = [], set()

    def add(kind, name):
        if name and name not in seen:
            seen.add(name)
            comps.append((kind, name))

    for ss in pob.skill_sets(root):
        for g in ss["groups"]:
            for gem in g["gems"]:
                add("gem", gem)
    for it in pob.extract_items(root):
        if (it.get("rarity") or "").upper() in ("UNIQUE", "RELIC"):
            add("unique", it["name"])
    for ks in pob.extract_keystones(root):
        add("keystone", ks)
    return comps


def _cell(text):
    return str(text).replace("|", "\\|").replace("\n", " ").strip() or "—"


def report(root, summary, llm=None):
    """Decoded PoB + summary dict (or None) -> markdown report string."""
    info = pob.build_info(root)
    comps = collect_components(root)

    rows_by, verdict, note = {}, None, None
    if summary is None:
        note = "no patch summary available — run advisor/summarize.py first"
    else:
        try:
            if llm is None:
                if LLM is None:
                    raise LLMDisabled("llm/client.py is not available")
                llm = LLM("standard")
            user = ("BUILD COMPONENTS (one row each, `component` exactly "
                    "as written, without the kind annotation):\n"
                    + "\n".join(f"- {name} ({kind})" for kind, name in comps)
                    + "\n\nPATCH SUMMARY ITEMS:\n"
                    + json.dumps(summary["items"], indent=1))
            data = llm.complete(
                system=EXPOSURE_PROMPT,
                messages=[{"role": "user", "content": user}],
                max_tokens=4096,
                feature="advisor_exposure",
                json_schema=EXPOSURE_SCHEMA,
            )
            for row in data.get("rows", []):
                rows_by[row.get("component")] = row
            verdict = data.get("verdict")
        except (LLMDisabled, LLMError) as e:
            # API failure/refusal degrades like the kill switch: the
            # deterministic component table must still be written.
            note = f"LLM unavailable: {e}"

    title = f"# Nerf exposure — {info['class']}"
    if info["ascendancy"]:
        title += f" ({info['ascendancy']})"
    lines = [title, "",
             "| Component | Change | Direction | Source | Quote |",
             "|---|---|---|---|---|"]
    for kind, name in comps:
        row = rows_by.get(name)
        if row is None:
            # LLM ran but skipped it -> unsupported => assumption;
            # degraded run (no LLM / no summary) -> plain 'no patch data'.
            row = {"change": "no patch data", "direction": "unknown",
                   "source": "assumption" if note is None else "—",
                   "quote": ""}
        lines.append("| " + " | ".join(_cell(c) for c in (
            f"{name} ({kind})", row.get("change"), row.get("direction"),
            row.get("source"), row.get("quote"))) + " |")
    if not comps:
        lines.append("| — | no components found in PoB | — | — | — |")

    lines += ["",
              "**Overall verdict:** "
              + (verdict if verdict
                 else "no verdict — "
                      + (note or "the model returned an empty verdict")),
              "",
              "*Keystones are best-effort: tree-allocated keystones cannot "
              "be resolved offline and may be missing from this table.*",
              ""]
    return "\n".join(lines)


def default_out_name(info):
    """Default output filename: exposure_<class>.md."""
    return "exposure_" + re.sub(r"[^\w-]", "_", info["class"].lower()) + ".md"


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("code", help="PoB code or file")
    ap.add_argument("--summary",
                    default=os.path.join("data", "3.29", "summary.json"))
    ap.add_argument("--out", default=None,
                    help="output path (default exposure_<class>.md)")
    a = ap.parse_args(argv)

    root = pob.decode(pob.read_code(a.code))
    spath = a.summary
    if not os.path.exists(spath):            # also try repo-root-relative
        alt = os.path.join(_ROOT, a.summary)
        spath = alt if os.path.exists(alt) else spath
    md = report(root, load_summary(spath))

    out = a.out or default_out_name(pob.build_info(root))
    with open(out, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
