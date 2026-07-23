"""Headless tests for high-ticket craft guides and premium-base filters."""
import contextlib
import io
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
sys.path[:0] = [ROOT, TOOLS]

from craft import guides  # noqa: E402
from market import base_filters  # noqa: E402
import tradeq  # noqa: E402


catalog = guides.load_catalog()
ids = {guide["id"] for guide in catalog["guides"]}
assert ids == {
    "focused_plus4_amulet",
    "helical_attribute_ring",
    "fractured_35_large_cluster",
    "plus2_arrow_spine_bow",
    "t1_suppress_necrotic",
}
assert len(catalog["rules"]) >= 5
for guide in catalog["guides"]:
    text = guides.render_guide(guide, catalog["rules"])
    assert guide["name"] in text
    assert "ROI thesis" in text and "Stop-loss" in text
    assert "Matching base searches" in text
    assert guide["base_searches"]
    assert all(step["checkpoint"] for step in guide["steps"])

bundle_md = guides.render_bundle(catalog)
assert "High-ticket crafting playbook" in bundle_md
assert all(guide["name"] in bundle_md for guide in catalog["guides"])

filter_catalog = base_filters.load_templates()
filter_ids = {row["id"] for row in filter_catalog["templates"]}
guide_search_ids = {
    sid for guide in catalog["guides"] for sid in guide["base_searches"]
}
assert guide_search_ids == filter_ids, \
    "every guide search id must resolve to exactly one template"

bundle = base_filters.build_bundle(100, filter_catalog)
assert bundle["bankroll_div"] == 100
assert len(bundle["queries"]) == len(filter_catalog["templates"])
assert not bundle["skipped"]

stats = tradeq.load_catalog()
queries = {row["id"]: row for row in bundle["queries"]}
for row in bundle["queries"]:
    assert tradeq.validate_query(row["query"], stats) == []
    q = row["query"]["query"]
    assert q["status"]["option"] == "online"
    assert q["filters"]["misc_filters"]["filters"]["corrupted"]["option"] \
        == "false"
    assert q["filters"]["trade_filters"]["filters"]["price"]["max"] == \
        row["price_cap_div"]
    assert row["price_cap_div"] <= 30

bow_stats = queries["spine_bow_i86_fractured_plus2"]["query"]["query"][
    "stats"][0]["filters"]
assert bow_stats == [{
    "id": "fractured.stat_3885405204", "value": {"min": 2}
}]
suppress_stats = queries[
    "necrotic_i86_fractured_t1_suppress"]["query"]["query"]["stats"][0][
        "filters"]
assert suppress_stats[0]["id"] == "fractured.stat_3680664274"
assert suppress_stats[0]["value"]["min"] == 22

spell = queries["cluster_spell_12_fractured_35"]["query"]["query"]
spell_stats = {row["id"]: row.get("value") for row in
               spell["stats"][0]["filters"]}
assert spell_stats["enchant.stat_3086156145"] == {"min": 12, "max": 12}
assert spell_stats["enchant.stat_3948993189"] == {"option": 10}
assert spell_stats["fractured.stat_2618549697"] == {"min": 35}
assert spell["filters"]["misc_filters"]["filters"]["ilvl"]["min"] == 84

# Bankroll gating prevents a small account from arming expensive searches.
small = base_filters.build_bundle(18, filter_catalog)
assert {row["id"] for row in small["queries"]} == {
    "helical_ring_i84"
}
assert len(small["skipped"]) == len(filter_catalog["templates"]) - 1

with tempfile.TemporaryDirectory(prefix="poe_high_end_test_") as tmp:
    paths = base_filters.write_bundle(bundle, tmp)
    assert len(paths) == len(bundle["queries"])
    assert os.path.exists(os.path.join(tmp, "README.md"))
    for path in paths:
        with open(path, encoding="utf-8") as f:
            query = json.load(f)
        assert tradeq.validate_query(query, stats) == []
    with open(os.path.join(tmp, "README.md"), encoding="utf-8") as f:
        readme = f.read()
    assert "tools/snipe.py" in readme
    assert "--query" in readme
    assert "manual action" in readme

    guide_out = os.path.join(tmp, "guides.md")
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        rc = guides.main(["bundle", "--out", guide_out])
    assert rc == 0 and not stderr.getvalue()
    assert os.path.exists(guide_out)

print("ALL TESTS PASSED")
print("  five high-ticket craft guides include checkpoints and stop-losses")
print("  eight premium-base searches use exact official fractured/enchant ids")
print("  bankroll caps gate searches before they can over-size a position")
