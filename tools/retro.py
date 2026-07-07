"""Post-run retro (addendum 5E, task 28b).

Usage:
    python tools/retro.py runs/<file>.json [--pb runs/pb.json] [--no-llm]

Deterministic core: loads a run file (format in docs/INTERFACES.md), computes
per-act splits with deltas vs the personal best, level-vs-time milestones
(every 10 levels), the death list with per-act counts, and the total time,
then prints an aligned plain-text stats table.

LLM layer: standard tier via llm.client — the stats table (which includes the
deaths) is sent to the model, which appends a half-page retro with exactly
three concrete changes for the next run. Degrades to the stats table only
(exit 0) when the LLM is unavailable (LLMDisabled, LLMError, or llm package
missing). Import-safe: no side effects at import time; the llm import is
deferred into generate_retro().
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

MILESTONE_EVERY = 10

RETRO_SYSTEM = (
    "You are a Path of Exile campaign speedrun coach reviewing one run.\n"
    "You are given a plain-text stats table: per-act splits (with deltas vs\n"
    "the personal best where available, negative = faster than PB), level\n"
    "milestones, and every death with its act and timestamp.\n"
    "Write a half-page retro (about 150-250 words), plain text, no markdown:\n"
    "first a short paragraph on where time was won or lost and what the\n"
    "deaths suggest — name the specific acts and levels from the data —\n"
    "then EXACTLY three concrete, actionable changes for the next run, as a\n"
    "numbered list (1., 2., 3.). No generic advice; every change must tie to\n"
    "something visible in the stats."
)

LLM_SECTION_HEADER = "-- Retro (LLM) --"


# --------------------------------------------------------------- loading

def load_run(path):
    """Load a run/PB JSON file (format per docs/INTERFACES.md)."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return data


# --------------------------------------------------------- deterministic core

def act_splits(run):
    """Per-act splits: [{"act", "split" (this act's seconds), "cum", "level"}].

    Run files store `t` as cumulative seconds since run start at the moment
    the act was completed; the per-act split is the delta to the previous act.
    """
    splits = sorted(run.get("splits") or [], key=lambda s: s["t"])
    out, prev = [], 0
    for s in splits:
        out.append({"act": s["act"], "split": s["t"] - prev,
                    "cum": s["t"], "level": s.get("level")})
        prev = s["t"]
    return out


def split_deltas(run_splits, pb_splits):
    """{act: {"split": run-pb per-act seconds, "cum": cumulative delta}}.

    Negative = faster than PB. Acts missing from either side are omitted.
    """
    pb_by_act = {s["act"]: s for s in pb_splits}
    out = {}
    for s in run_splits:
        p = pb_by_act.get(s["act"])
        if p is not None:
            out[s["act"]] = {"split": s["split"] - p["split"],
                             "cum": s["cum"] - p["cum"]}
    return out


def level_milestones(levels, every=MILESTONE_EVERY):
    """[{"level": m, "t": first t at which level >= m}] for m = every, 2*every…

    Handles level-up lists that skip an exact milestone (e.g. 9 -> 11): the
    milestone timestamp is the first recorded level at or above it.
    """
    entries = sorted(levels or [], key=lambda e: e["t"])
    if not entries:
        return []
    max_level = max(e["level"] for e in entries)
    out = []
    for m in range(every, max_level + 1, every):
        t = next(e["t"] for e in entries if e["level"] >= m)
        out.append({"level": m, "t": t})
    return out


def death_stats(run):
    """(deaths, counts): deaths = [{"t", "who", "act"}] time-ordered;
    counts = {act: n}. A death's act is the act in progress at its `t`
    (before that act's split time); past the last split it's the next act.
    """
    splits = sorted(run.get("splits") or [], key=lambda s: s["t"])
    deaths_in = sorted(run.get("deaths") or [], key=lambda d: d["t"])
    deaths, counts = [], {}
    for d in deaths_in:
        act = None
        for s in splits:
            if d["t"] < s["t"]:
                act = s["act"]
                break
        if act is None:
            act = splits[-1]["act"] + 1 if splits else 1
        deaths.append({"t": d["t"], "who": d.get("who", "?"), "act": act})
        counts[act] = counts.get(act, 0) + 1
    return deaths, counts


def total_time(run):
    """(seconds, finished). finished=True when started/ended parse; otherwise
    falls back to the largest `t` seen anywhere in the run (run in progress).
    """
    started, ended = run.get("started"), run.get("ended")
    if started and ended:
        try:
            dt = (datetime.fromisoformat(ended)
                  - datetime.fromisoformat(started)).total_seconds()
            if dt >= 0:
                return dt, True
        except ValueError:
            pass
    ts = [e["t"] for key in ("splits", "levels", "deaths")
          for e in (run.get(key) or [])]
    return (max(ts) if ts else 0), False


# ------------------------------------------------------------- formatting

def fmt_t(seconds):
    """45:00, 2:06:40 — h:mm:ss when >= 1 h, else m:ss."""
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"


def fmt_delta(seconds):
    """Signed delta: +5:00 (slower than PB) / -3:20 (faster)."""
    s = int(round(seconds))
    return ("-" if s < 0 else "+") + fmt_t(abs(s))


def _align(rows, right=()):
    """Render rows (lists of str) as aligned columns, two spaces between.
    Column indices in `right` are right-aligned, the rest left-aligned."""
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    lines = []
    for r in rows:
        cells = [c.rjust(widths[i]) if i in right else c.ljust(widths[i])
                 for i, c in enumerate(r)]
        lines.append("  ".join(cells).rstrip())
    return lines


def render_table(run, pb=None):
    """The full plain-text stats table (deterministic — no LLM)."""
    total, finished = total_time(run)
    deaths, counts = death_stats(run)
    lines = [
        f"Run retro — {run.get('character', '?')} ({run.get('class', '?')}), "
        f"league {run.get('league', '?')}",
        f"Started: {run.get('started') or '?'}",
        f"Total:   {fmt_t(total)}  ({'finished' if finished else 'in progress'})",
        f"Deaths:  {len(deaths)}",
        "",
    ]

    splits = act_splits(run)
    pb_splits = act_splits(pb) if pb else []
    deltas = split_deltas(splits, pb_splits)
    lines.append("Act splits" + ("  (vs PB)" if pb_splits else "  (no PB)"))
    if splits:
        header = ["Act", "Split", "Cumul", "Level"]
        if pb_splits:
            header += ["vs PB", "vs PB cum"]
        rows = [header]
        for s in splits:
            row = [str(s["act"]), fmt_t(s["split"]), fmt_t(s["cum"]),
                   "-" if s.get("level") is None else str(s["level"])]
            if pb_splits:
                d = deltas.get(s["act"])
                row += ([fmt_delta(d["split"]), fmt_delta(d["cum"])]
                        if d else ["-", "-"])
            rows.append(row)
        lines.extend(_align(rows, right=set(range(len(header)))))
    else:
        lines.append("  (none)")

    lines.append("")
    lines.append(f"Level milestones (every {MILESTONE_EVERY})")
    miles = level_milestones(run.get("levels") or [])
    if miles:
        rows = [["Level", "Time"]]
        rows += [[str(m["level"]), fmt_t(m["t"])] for m in miles]
        lines.extend(_align(rows, right={0, 1}))
    else:
        lines.append("  (none)")

    lines.append("")
    lines.append(f"Deaths ({len(deaths)})")
    if deaths:
        rows = [["Act", "Time", "Who"]]
        rows += [[str(d["act"]), fmt_t(d["t"]), d["who"]] for d in deaths]
        lines.extend(_align(rows, right={0, 1}))
        per_act = ", ".join(f"act {a}: {counts[a]}" for a in sorted(counts))
        lines.append(f"Deaths per act: {per_act}")
    else:
        lines.append("  (none)")
        lines.append("Deaths per act: none")

    return "\n".join(lines)


# --------------------------------------------------------------- LLM layer

def generate_retro(table_text):
    """Half-page LLM retro (standard tier) for the rendered stats table, or
    None when the LLM is unavailable — llm package missing, LLMDisabled
    (kill switch / no key), or LLMError. Callers degrade to stats only.
    """
    # `python tools/retro.py ...` puts tools/ (not the repo root) on
    # sys.path[0], so `llm.client` needs the root inserted explicitly —
    # same dance as tools/tradeq.py and tools/verify_routes_llm.py.
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from llm.client import LLM, LLMDisabled, LLMError
    except ImportError as e:
        print(f"retro: LLM unavailable ({e}); stats table only.",
              file=sys.stderr)
        return None
    try:
        llm = LLM("standard")
        return llm.complete(
            system=RETRO_SYSTEM,
            messages=[{"role": "user",
                       "content": "Here is the run:\n\n" + table_text}],
            max_tokens=700,
            feature="retro",
        )
    except LLMDisabled as e:
        print(f"retro: LLM disabled ({e}); stats table only.",
              file=sys.stderr)
        return None
    except LLMError as e:
        print(f"retro: LLM error ({e}); stats table only.", file=sys.stderr)
        return None


# --------------------------------------------------------------------- CLI

def main(argv=None):
    # Windows: redirected stdout defaults to the ANSI codepage, which can
    # not encode arbitrary LLM-generated text; force UTF-8.
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass
    ap = argparse.ArgumentParser(
        description="Post-run retro: per-act splits vs PB, level curve, "
                    "deaths, plus an optional LLM retrospective.")
    ap.add_argument("run", help="run file, e.g. runs/run_<startts>.json")
    ap.add_argument("--pb", default=None,
                    help="PB run file (default: pb.json next to the run "
                         "file, if present)")
    ap.add_argument("--no-llm", action="store_true",
                    help="print the stats table only, never call the LLM")
    args = ap.parse_args(argv)

    try:
        run = load_run(args.run)
    except (OSError, ValueError) as e:
        print(f"retro: cannot read run file: {e}", file=sys.stderr)
        return 2

    pb = None
    pb_path = args.pb or os.path.join(
        os.path.dirname(os.path.abspath(args.run)), "pb.json")
    if os.path.exists(pb_path):
        try:
            pb = load_run(pb_path)
        except (OSError, ValueError) as e:
            print(f"retro: ignoring unreadable PB file {pb_path}: {e}",
                  file=sys.stderr)
    elif args.pb:
        print(f"retro: PB file not found: {args.pb}", file=sys.stderr)

    table = render_table(run, pb)
    print(table)

    if not args.no_llm:
        retro_text = generate_retro(table)
        if retro_text:
            print()
            print(LLM_SECTION_HEADER)
            print(retro_text.strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
