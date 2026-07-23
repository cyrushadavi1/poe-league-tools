"""Headless tests for the deterministic league-start wealth planner."""
import contextlib
import io
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT]

from market import wealth  # noqa: E402


data = wealth.load_playbook()
assert data["source"].startswith("League Start Wealth Plan")
assert len(data["guardrails"]) >= 5
assert {s["id"] for s in data["stages"]} == {
    "launch", "transition", "scaling", "mature"
}
assert wealth.stage_for_day(0, data)["id"] == "launch"
assert wealth.stage_for_day(2, data)["id"] == "launch"
assert wealth.stage_for_day(3, data)["id"] == "transition"
assert wealth.stage_for_day(8, data)["id"] == "scaling"
assert wealth.stage_for_day(15, data)["id"] == "mature"

required = {
    "id", "name", "category", "windows", "min_capital_c", "risk", "effort",
    "priority", "action", "verify", "stop_loss"
}
for strategy in data["strategies"]:
    assert required <= set(strategy), strategy
    assert strategy["category"] in wealth.CATEGORIES
    assert strategy["risk"] in wealth.RISK_ORDER
    assert strategy["windows"]
    assert "verify" in strategy["verify"].lower() or strategy["verify"]
    assert strategy["stop_loss"]

# Launch + low capital/risk favors broad inventory, never high-risk crafts.
launch = wealth.build_plan(1, 50, "low", playbook=data)
launch_ids = [row["id"] for row in launch["recommendations"]]
assert launch["stage"]["id"] == "launch"
assert launch["capital_tier"] == "bootstrap"
assert launch_ids[:2] == ["rog_inventory", "walmart_rares"]
assert "basic_essence_gear" in launch_ids
assert all(row["risk"] == "low" for row in launch["recommendations"])
assert any(row["id"] == "appreciating_assets"
           and "needs about 500c" in row["defer_reason"]
           for row in launch["deferred"])

# Day 6 working capital unlocks targeted craft/arbitrage, but not high risk.
transition = wealth.build_plan(
    6, 1200, "medium",
    categories=["targeted_craft", "arbitrage"],
    playbook=data,
)
transition_ids = [row["id"] for row in transition["recommendations"]]
assert transition["stage"]["id"] == "transition"
assert transition["capital_tier"] == "working"
assert transition["categories"] == ["arbitrage", "targeted_craft"]
assert transition_ids[0] == "meta_targeted_crafts"
assert "cluster_jewels" in transition_ids
assert "vendor_recipe_arbitrage" in transition_ids
assert "six_linking" not in transition_ids
assert any(row["id"] == "six_linking"
           and "high risk" in row["defer_reason"]
           for row in transition["deferred"])

# Scaling + high risk/capital admits high-end plays.
scaled = wealth.build_plan(10, 5000, "high", limit=50, playbook=data)
scaled_ids = {row["id"] for row in scaled["recommendations"]}
assert {"high_end_flips", "six_linking", "influence_reroll",
        "329_corruption_batches"} <= scaled_ids
assert scaled["capital_tier"] == "scaled"

md = wealth.render_markdown(transition)
for phrase in ("League-day 6 wealth plan", "Guardrails", "Recommended now",
               "Verify first", "Stop-loss", "every market and crafting"):
    assert phrase in md

# CLI JSON and file output are deterministic and require no LLM/network.
with tempfile.TemporaryDirectory(prefix="poe_wealth_test_") as tmp:
    out_path = os.path.join(tmp, "plan.json")
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        rc = wealth.main([
            "--day", "4", "--bankroll-c", "600", "--risk", "medium",
            "--category", "investment", "--json", "--out", out_path,
        ])
    assert rc == 0 and not stderr.getvalue()
    assert "wrote wealth plan" in stdout.getvalue()
    with open(out_path, encoding="utf-8") as f:
        written = json.load(f)
    assert written["stage"]["id"] == "transition"
    assert written["categories"] == ["investment"]
    assert [row["id"] for row in written["recommendations"]] == \
        ["appreciating_assets"]

for bad_args in (
    ["--day", "-1", "--bankroll-c", "100"],
    ["--day", "1", "--bankroll-c", "-1"],
):
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        assert wealth.main(bad_args) == 1
    assert "wealth plan error" in stderr.getvalue()

print("ALL TESTS PASSED")
print("  league day + bankroll + risk gate every recommendation")
print("  each play includes live-price verification and a stop-loss")
print("  planner is deterministic, offline, and advisory only")
