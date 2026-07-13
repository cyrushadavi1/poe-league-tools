#!/usr/bin/env python3
"""Cross-checks routes/act*.json against vendored Exile-UI data.

Two hard checks (exit 1 on failure -- these break the overlay):
  names   every route step's zone name must resolve in the Exile-UI
          area table for its act (a typo here silently kills
          auto-advance: the log line will never match the step)
  levels  every step's arealvl must equal the table's monster level
          (a wrong arealvl mis-fires the XP-penalty warning)

One soft check (informational only -- printed with --coverage):
  coverage  zones the community leveling guide routes through that our
            route never visits, and vice versa. Differences are often
            deliberate (optional zones, party-play skips), so this is
            a review aid, not a gate.

Pure stdlib, no network; data comes from data/exileui/ (see the README
there). Used by tests/test_layouts.py for the hard checks.
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EXILEUI = os.path.join(ROOT, "data", "exileui")
ROUTES = os.path.join(ROOT, "routes")

_AREAID_RE = re.compile(r"areaid([0-9A-Za-z_]+)")

# Real Client.txt zone names absent from the Exile-UI table (it skips
# boss arenas): the two Malachai fight zones at the end of act 4.
# https://www.poewiki.net/wiki/The_Eternal_Nightmare
KNOWN_EXTRA_ZONES = {"black core", "black heart"}


def norm(name):
    """Zone names comparable across sources: Exile-UI display names
    sometimes drop the leading 'The' ('Twilight Strand') and write
    multi-level zones as 'Chamber of Sins (1)' where Client.txt says
    'The Chamber of Sins Level 1'."""
    name = name.strip().lower()
    if name.startswith("the "):
        name = name[4:]
    return re.sub(r" level (\d+)$", r" (\1)", name)


def load_areas(path=os.path.join(EXILEUI, "areas.json")):
    """-> (per_act, by_id): per_act[act][norm_name] = entry (acts 1..10,
    epilogue ignored), by_id[area_id] = (act, entry)."""
    with open(path, encoding="utf-8") as f:
        acts = json.load(f)
    per_act, by_id = {}, {}
    for i, entries in enumerate(acts, start=1):   # 11 = epilogue
        per_act[i] = {norm(e["name"]): e for e in entries}
        for e in entries:
            by_id[e["id"]] = (i, e)
    return per_act, by_id


def load_routes(routes_dir=ROUTES):
    """-> {act: [step, ...]} from routes/act1..10.json."""
    routes = {}
    for n in range(1, 11):
        with open(os.path.join(routes_dir, f"act{n}.json"),
                  encoding="utf-8") as f:
            routes[n] = json.load(f)["steps"]
    return routes


def guide_area_ids(path=os.path.join(EXILEUI, "default_guide.json")):
    """-> {act: [area_id, ...]} in visit order, from the community
    guide's 'areaid<ID>' markup tokens (conditional blocks included --
    coverage is a superset of any single playthrough)."""
    with open(path, encoding="utf-8") as f:
        acts = json.load(f)
    out = {}
    for i, steps in enumerate(acts[:10], start=1):
        ids, seen = [], set()
        for step in steps:
            lines = step.get("lines", []) if isinstance(step, dict) else step
            for line in lines:
                for aid in _AREAID_RE.findall(line):
                    if aid not in seen:
                        seen.add(aid)
                        ids.append(aid)
        out[i] = ids
    return out


def check_names(routes, per_act):
    """Route zones that resolve nowhere -> [(act, step_idx, zone)].

    A zone may legitimately live in another act's table (cross-act
    steps like the act-4 Aqueduct arrival), so fall back to a global
    lookup before flagging.
    """
    all_names = set(KNOWN_EXTRA_ZONES)
    for table in per_act.values():
        all_names.update(table)
    return [(act, k, s["zone"])
            for act, steps in routes.items()
            for k, s in enumerate(steps)
            if norm(s["zone"]) not in per_act[act]
            and norm(s["zone"]) not in all_names]


def check_levels(routes, per_act):
    """arealvl mismatches -> [(act, zone, ours, theirs)]. Steps whose
    zone doesn't resolve in their own act's table are name-check
    territory, not level-check territory."""
    bad = []
    for act, steps in routes.items():
        for s in steps:
            entry = per_act[act].get(norm(s["zone"]))
            if entry and "arealvl" in s and "lvl" in entry \
                    and s["arealvl"] != entry["lvl"]:
                bad.append((act, s["zone"], s["arealvl"], entry["lvl"]))
    return bad


def check_coverage(routes, per_act, by_id, guide_ids):
    """-> {act: (guide_only_names, route_only_names)} (both sorted)."""
    out = {}
    for act in routes:
        route_zones = {norm(s["zone"]) for s in routes[act]}
        guide_zones = {}
        for aid in guide_ids.get(act, []):
            hit = by_id.get(aid)
            if hit:
                guide_zones[norm(hit[1]["name"])] = hit[1]["name"]
        guide_only = sorted(v for k, v in guide_zones.items()
                            if k not in route_zones)
        route_only = sorted({s["zone"] for s in routes[act]
                             if norm(s["zone"]) not in guide_zones
                             and norm(s["zone"]) in per_act[act]})
        out[act] = (guide_only, route_only)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--coverage", action="store_true",
                    help="also print the per-act guide/route zone diff")
    a = ap.parse_args()

    per_act, by_id = load_areas()
    routes = load_routes()

    failed = False
    bad_names = check_names(routes, per_act)
    if bad_names:
        failed = True
        print("ZONE NAMES that resolve nowhere (typo? auto-advance will "
              "never fire):")
        for act, k, zone in bad_names:
            print(f"  act{act} step {k}: {zone!r}")
    else:
        print("zone names: all route steps resolve  ✓")

    bad_lvls = check_levels(routes, per_act)
    if bad_lvls:
        failed = True
        print("AREALVL mismatches vs the Exile-UI table:")
        for act, zone, ours, theirs in bad_lvls:
            print(f"  act{act} {zone}: route says {ours}, table says {theirs}")
    else:
        print("area levels: all arealvl values match  ✓")

    if a.coverage:
        cov = check_coverage(routes, per_act, by_id, guide_area_ids())
        for act, (guide_only, route_only) in cov.items():
            if guide_only or route_only:
                print(f"act {act}:")
                if guide_only:
                    print("  community guide visits, we skip: "
                          + ", ".join(guide_only))
                if route_only:
                    print("  we visit, guide skips: " + ", ".join(route_only))

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
