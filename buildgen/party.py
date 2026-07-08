#!/usr/bin/env python3
"""Party build manager: turn 2-3 PoB codes into per-player leveling kits.

Usage:
  python party.py <party.json> [--out-dir builds]

party.json -- paste each member's PoB code (or a path to a file holding
one); mark your own character with "me": true:

  {
    "members": [
      {"player": "CyrusChar", "pob": "<code or file>", "me": true},
      {"player": "FriendChar", "pob": "<code or file>"}
    ]
  }

Outputs, per player: <player>_plan.md (printable leveling sheet) and
<player>_notes.json (overlay gem reminders -- each person points
`build_notes` in their own overlay config at their file). Plus one
party_summary.md: who plays what and everyone's gem links side by side
per act, for coordinating vendor/quest gem pickups. And one
party_bundle.json: the machine-readable manifest tools/join_party.py
uses on each gaming PC to write that player's overlay config (ship the
whole folder, builds/ included).

Also prints a ready-to-paste "party" config block for hand-editors.
"""
import argparse
import json
import os
import re

import pob

MAX_PARTY = 3
OVERLAY_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "overlay")


def safe_name(player):
    return re.sub(r"[^\w-]", "_", player)


def build_member(member, out_dir):
    """Decode one member's PoB; write their plan + notes; return summary."""
    root = pob.decode(pob.read_code(member["pob"]))
    info = pob.build_info(root)
    md, notes = pob.make_plan(root)

    player = member["player"]
    plan_path = os.path.join(out_dir, f"{safe_name(player)}_plan.md")
    notes_path = os.path.join(out_dir, f"{safe_name(player)}_notes.json")
    with open(plan_path, "w", encoding="utf-8") as f:
        f.write(f"*Player: {player}*\n\n" + md)
    with open(notes_path, "w", encoding="utf-8") as f:
        json.dump(notes, f, indent=2)

    return {
        "player": player,
        "me": bool(member.get("me")),
        "class": info["class"],
        "ascendancy": info["ascendancy"],
        "notes": {n["act"]: n["text"] for n in notes},
        "uniques": [it for it in pob.extract_items(root)
                    if (it.get("rarity") or "").upper() in ("UNIQUE", "RELIC")],
        "plan_path": plan_path,
        "notes_path": notes_path,
    }


def summary_md(members):
    lines = ["# Party summary", "", "| Player | Class | Ascendancy |",
             "|---|---|---|"]
    for m in members:
        star = " ★" if m["me"] else ""
        lines.append(f"| {m['player']}{star} | {m['class']} | "
                     f"{m['ascendancy'] or '—'} |")

    acts = sorted({a for m in members for a in m["notes"]})
    if acts:
        lines += ["", "## Gem links by act", "",
                  "| Act | " + " | ".join(m["player"] for m in members) + " |",
                  "|---|" + "---|" * len(members)]
        for act in acts:
            row = [m["notes"].get(act, "—") for m in members]
            lines.append(f"| {act} | " + " | ".join(row) + " |")
    else:
        lines += ["", "*(No act-tagged skill sets found in any PoB -- name "
                  "PoB skill sets \"Act 1 ...\" etc. to get per-act gem "
                  "reminders.)*"]

    # Uniques wishlist: each player's PoB-listed uniques so the party can
    # coordinate who keeps which drops. Section only appears when at least
    # one build actually lists uniques (keeps old output byte-identical).
    if any(m.get("uniques") for m in members):
        lines += ["", "## Uniques wishlist", "",
                  "*(uniques listed in each player's PoB -- call these "
                  "drops for each other)*", ""]
        for m in members:
            uniques = m.get("uniques") or []
            names = ", ".join(
                u["name"] + (f" ({u['base']})" if u.get("base") else "")
                for u in uniques) or "—"
            lines.append(f"- **{m['player']}**: {names}")
    lines.append("")
    return "\n".join(lines)


def write_bundle(members, path, league="3.29"):
    """builds/party_bundle.json: everything a friend's PC needs to set
    itself up (tools/join_party.py reads it and asks "who are you?").

    Notes/plan paths are stored as basenames -- the bundle travels with
    its directory when the folder is copied to each gaming PC, so paths
    from the generating machine would be wrong on every one of them.
    """
    bundle = {
        "league": league,
        "members": [{
            "player": m["player"],
            "me": m["me"],
            "class": m["class"],
            "ascendancy": m["ascendancy"],
            "notes": os.path.basename(m["notes_path"]),
            "plan": os.path.basename(m["plan_path"]),
        } for m in members],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(bundle, f, indent=2)
        f.write("\n")
    return bundle


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("manifest", help="party.json (see module docstring)")
    ap.add_argument("--out-dir", default="builds")
    a = ap.parse_args()

    with open(a.manifest, encoding="utf-8") as f:
        manifest = json.load(f)
    members_cfg = manifest["members"]
    if not members_cfg:
        raise SystemExit("party.json has no members")
    if len(members_cfg) > MAX_PARTY:
        print(f"!! {len(members_cfg)} members; tooling is tuned for 2-{MAX_PARTY}")

    os.makedirs(a.out_dir, exist_ok=True)
    members = [build_member(m, a.out_dir) for m in members_cfg]

    sum_path = os.path.join(a.out_dir, "party_summary.md")
    with open(sum_path, "w", encoding="utf-8") as f:
        f.write(summary_md(members))
    bundle_path = os.path.join(a.out_dir, "party_bundle.json")
    write_bundle(members, bundle_path, manifest.get("league", "3.29"))

    for m in members:
        print(f"{m['player']:<20} {m['class']}"
              f"{' (' + m['ascendancy'] + ')' if m['ascendancy'] else ''}"
              f"  -> {m['plan_path']}")
    print(f"party summary        -> {sum_path}")
    print(f"party bundle         -> {bundle_path}")

    print("\nShip the folder (with the builds dir -- it is gitignored, so "
          "zip beats clone)\nto each gaming PC; setup_pc.bat there reads "
          "the bundle and asks who you are.")

    # Manual fallback: an overlay-relative path (forward slashes) works
    # after the folder is copied to any machine; an absolute path from
    # THIS machine would not.
    me = next((m for m in members if m["me"]), members[0])
    others = [m["player"] for m in members if m is not me]
    rel_notes = os.path.relpath(os.path.abspath(me["notes_path"]),
                                OVERLAY_DIR).replace(os.sep, "/")
    print("\n(hand-editing instead? paste into overlay/config.json:)")
    print(json.dumps({
        "build_notes": rel_notes,
        "party": {"me": me["player"], "members": others, "gap_warn": 3},
    }, indent=2))


if __name__ == "__main__":
    main()
