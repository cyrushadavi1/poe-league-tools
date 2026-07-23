"""Deterministic league-start wealth planner.

Turns the curated ``data/wealth_playbook.json`` into a short plan filtered
by league day, bankroll, risk tolerance, and optional strategy categories.
It performs no network calls, pricing, messaging, trading, or game input.
Every recommendation carries a live-price verification step and stop-loss.

Usage:
    python -m market.wealth --day 1 --bankroll-c 150 --risk low
    python -m market.wealth --day 6 --bankroll-c 1200 --risk medium --json
    python -m market.wealth --day 10 --bankroll-c 4000 \
        --category investment --category flipping
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PATH = os.path.join(_ROOT, "data", "wealth_playbook.json")

RISK_ORDER = {"low": 0, "medium": 1, "high": 2}
CATEGORIES = {
    "craft_inventory", "targeted_craft", "arbitrage", "flipping", "investment"
}


def load_playbook(path: str | None = None) -> dict:
    """Load and minimally validate the authored playbook."""
    with open(path or DEFAULT_PATH, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("wealth playbook must be a JSON object")
    if not isinstance(data.get("stages"), list) or not data["stages"]:
        raise ValueError("wealth playbook needs at least one stage")
    if not isinstance(data.get("strategies"), list):
        raise ValueError("wealth playbook strategies must be a list")
    return data


def stage_for_day(day: int, playbook: dict) -> dict:
    """Return the inclusive stage containing ``day``."""
    if day < 0:
        raise ValueError("league day cannot be negative")
    for stage in playbook["stages"]:
        if int(stage["from_day"]) <= day <= int(stage["to_day"]):
            return stage
    raise ValueError(f"no wealth stage covers league day {day}")


def capital_tier(bankroll_c: float) -> str:
    """Human-readable bankroll band used in output, not for filtering."""
    if bankroll_c < 100:
        return "bootstrap"
    if bankroll_c < 500:
        return "small"
    if bankroll_c < 2000:
        return "working"
    return "scaled"


def _defer_reason(strategy: dict, stage_id: str, bankroll_c: float,
                  risk: str) -> str | None:
    if stage_id not in strategy["windows"]:
        return "outside the current league-stage window"
    needed = float(strategy["min_capital_c"])
    if needed > bankroll_c:
        return f"needs about {needed:g}c bankroll"
    if RISK_ORDER[strategy["risk"]] > RISK_ORDER[risk]:
        return f"{strategy['risk']} risk exceeds the {risk} profile"
    return None


def build_plan(day: int, bankroll_c: float, risk: str = "medium",
               categories: list[str] | None = None, limit: int = 8,
               playbook: dict | None = None) -> dict:
    """Build a deterministic, ranked wealth plan.

    ``priority`` is authored in the playbook (lower is better). Strategies
    are then ordered by risk and minimum capital so the plan favors simple,
    survivable plays at equal priority.
    """
    if bankroll_c < 0:
        raise ValueError("bankroll cannot be negative")
    if risk not in RISK_ORDER:
        raise ValueError(f"risk must be one of: {', '.join(RISK_ORDER)}")
    if limit < 1:
        raise ValueError("limit must be at least 1")
    selected_categories = set(categories or [])
    unknown = selected_categories - CATEGORIES
    if unknown:
        raise ValueError("unknown categories: " + ", ".join(sorted(unknown)))

    data = playbook or load_playbook()
    stage = stage_for_day(day, data)
    eligible, deferred = [], []
    for strategy in data["strategies"]:
        if selected_categories and strategy["category"] not in \
                selected_categories:
            continue
        reason = _defer_reason(strategy, stage["id"], bankroll_c, risk)
        row = dict(strategy)
        if reason:
            row["defer_reason"] = reason
            deferred.append(row)
        else:
            eligible.append(row)

    eligible.sort(key=lambda s: (
        int(s["priority"]),
        RISK_ORDER[s["risk"]],
        float(s["min_capital_c"]),
        s["name"].lower(),
    ))
    deferred.sort(key=lambda s: (
        0 if "needs about" in s["defer_reason"] else 1,
        float(s["min_capital_c"]),
        s["name"].lower(),
    ))
    return {
        "league_day": day,
        "stage": {
            "id": stage["id"],
            "goal": stage["goal"],
        },
        "bankroll_c": bankroll_c,
        "capital_tier": capital_tier(bankroll_c),
        "risk_profile": risk,
        "categories": sorted(selected_categories) if selected_categories
        else sorted(CATEGORIES),
        "guardrails": list(data.get("guardrails", [])),
        "recommendations": eligible[:limit],
        "deferred": deferred,
        "source": data.get("source", ""),
    }


def render_markdown(plan: dict) -> str:
    """Render a compact plan suitable for terminal output or a notes file."""
    lines = [
        f"# League-day {plan['league_day']} wealth plan",
        "",
        f"Stage: **{plan['stage']['id']}** · bankroll: "
        f"**{plan['bankroll_c']:g}c** ({plan['capital_tier']}) · risk: "
        f"**{plan['risk_profile']}**",
        "",
        plan["stage"]["goal"],
        "",
        "## Guardrails",
        "",
    ]
    lines.extend(f"- {rule}" for rule in plan["guardrails"])
    lines.extend(["", "## Recommended now", ""])
    if not plan["recommendations"]:
        lines.append("- No strategy fits these filters; keep progressing and "
                     "preserve liquid currency.")
    for row in plan["recommendations"]:
        lines.extend([
            f"### {row['name']}",
            "",
            f"{row['action']}",
            "",
            f"- Category: `{row['category']}` · risk: `{row['risk']}` · "
            f"effort: `{row['effort']}` · minimum bankroll: "
            f"`{row['min_capital_c']:g}c`",
            f"- Verify first: {row['verify']}",
            f"- Stop-loss: {row['stop_loss']}",
            "",
        ])
    if plan["deferred"]:
        lines.extend(["## Deferred", ""])
        for row in plan["deferred"][:5]:
            lines.append(f"- **{row['name']}** — {row['defer_reason']}.")
        lines.append("")
    lines.extend([
        f"_Curated from: {plan['source']}._",
        "_Advisory only; every market and crafting action remains manual._",
    ])
    return "\n".join(lines).rstrip() + "\n"


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="market.wealth",
        description="Stage craft, arbitrage, flip, and investment plays by "
                    "league day, bankroll, and risk (advisory only).")
    p.add_argument("--day", type=int, required=True, help="league day (0+)")
    p.add_argument("--bankroll-c", type=float, required=True,
                   help="available bankroll in chaos orbs")
    p.add_argument("--risk", choices=tuple(RISK_ORDER), default="medium")
    p.add_argument("--category", action="append", choices=sorted(CATEGORIES),
                   help="repeat to include only selected categories")
    p.add_argument("--limit", type=int, default=8,
                   help="maximum recommendations to show")
    p.add_argument("--playbook", default=DEFAULT_PATH,
                   help="wealth playbook JSON")
    p.add_argument("--json", action="store_true",
                   help="emit structured JSON instead of markdown")
    p.add_argument("--out", default=None,
                   help="write output to this file instead of stdout")
    return p


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        data = load_playbook(args.playbook)
        plan = build_plan(args.day, args.bankroll_c, args.risk,
                          args.category, args.limit, data)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"wealth plan error: {exc}", file=sys.stderr)
        return 1
    output = (json.dumps(plan, indent=2) + "\n"
              if args.json else render_markdown(plan))
    if args.out:
        try:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(output)
        except OSError as exc:
            print(f"wealth plan error: {exc}", file=sys.stderr)
            return 1
        print(f"wrote wealth plan to {args.out}")
    else:
        print(output, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
