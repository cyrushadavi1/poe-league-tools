#!/usr/bin/env python3
"""Build advisor: patch summary + the party's PoBs -> verdicts + picks.

Usage:
  python advisor/advise.py --summary data/3.29/summary.json \\
      --pob <code-or-file> [--pob <code-or-file> ...] [--out advice.md]

Deterministic part (always produced): a digest per build parsed straight
from the PoB code — class/ascendancy/level, skill sets, main links,
uniques, best-effort keystones.

LLM part (deep tier): per-build verdicts plus 3 ranked league-start
recommendations, markdown to stdout or --out. The prompt enforces that
every claim cites a summary item id (e.g. [skill-fireball]) or is tagged
"(assumption)".

Degrades: when the LLM is unavailable or the summary file is missing,
only the deterministic build digests are printed, with a note saying why.
"""
import argparse
import json
import os
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
from advisor.prompts import ADVISE_PROMPT             # noqa: E402


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


def build_digest(root, label):
    """Deterministic per-build facts pulled from a decoded PoB."""
    info = pob.build_info(root)
    sets = pob.skill_sets(root)
    groups = [g for ss in sets for g in ss["groups"]]
    main = max(groups, key=lambda g: len(g["gems"]))["gems"] if groups else []
    uniques = [it["name"] + (f" ({it['base']})" if it.get("base") else "")
               for it in pob.extract_items(root)
               if (it.get("rarity") or "").upper() in ("UNIQUE", "RELIC")]
    return {
        "label": label,
        "class": info["class"],
        "ascendancy": info["ascendancy"],
        "level": info["level"],
        "set_titles": [ss["title"] for ss in sets],
        "main_links": " – ".join(main),
        "uniques": uniques,
        "keystones": pob.extract_keystones(root),
    }


def digest_md(d):
    """One build digest -> markdown block."""
    head = f"### {d['label']} — {d['class']}"
    if d["ascendancy"]:
        head += f" ({d['ascendancy']})"
    head += f", level {d['level']}"
    return "\n".join([
        head,
        f"- Skill sets: {'; '.join(d['set_titles']) or '—'}",
        f"- Main links: {d['main_links'] or '—'}",
        f"- Uniques: {', '.join(d['uniques']) or 'none listed'}",
        f"- Keystones (best-effort, may be incomplete): "
        f"{', '.join(d['keystones']) or 'none found'}",
    ])


def advise(summary, digests, llm=None):
    """LLM verdicts + 3 ranked recommendations as markdown text.

    Raises LLMDisabled when no LLM is available; callers degrade.
    """
    if llm is None:
        if LLM is None:
            raise LLMDisabled("llm/client.py is not available")
        llm = LLM("deep")
    user = ("PATCH SUMMARY ITEMS (cite these ids):\n"
            + json.dumps(summary["items"], indent=1)
            + "\n\nPARTY BUILDS:\n"
            + "\n\n".join(digest_md(d) for d in digests))
    return llm.complete(
        system=ADVISE_PROMPT,
        messages=[{"role": "user", "content": user}],
        max_tokens=4096,
        feature="advisor_advise",
        json_schema=None,
    )


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--summary", required=True,
                    help="data/<patch>/summary.json from advisor/summarize.py")
    ap.add_argument("--pob", dest="pobs", action="append", required=True,
                    metavar="CODE_OR_FILE",
                    help="PoB code or file; repeat per party member")
    ap.add_argument("--out", default=None,
                    help="write markdown here instead of stdout")
    a = ap.parse_args(argv)

    digests = [build_digest(pob.decode(pob.read_code(arg)), f"Build {i}")
               for i, arg in enumerate(a.pobs, 1)]
    summary = load_summary(a.summary)

    patch = summary.get("patch", "?") if summary else "no summary"
    parts = [f"# Build advisor — patch {patch}", "",
             "## Builds (parsed from PoB)", ""]
    parts += [digest_md(d) + "\n" for d in digests]
    parts += ["## Advisor verdicts & recommendations", ""]
    if summary is None:
        parts.append(f"*(no patch summary at {a.summary} — run "
                     "advisor/summarize.py first; deterministic build "
                     "digests only)*")
    else:
        try:
            parts.append(advise(summary, digests))
        except (LLMDisabled, LLMError) as e:
            # API failure/refusal degrades exactly like the kill switch:
            # the deterministic digests must never be lost to a traceback.
            parts.append(f"*(LLM unavailable: {e} — deterministic build "
                         "digests only)*")
    md = "\n".join(parts) + "\n"

    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"wrote {a.out}")
    else:
        print(md)


if __name__ == "__main__":
    main()
