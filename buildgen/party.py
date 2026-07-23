#!/usr/bin/env python3
"""Party build manager: turn 2-6 PoB codes into per-player leveling kits.

Usage:
  python party.py --init [party.json]         # interactive setup (start here)
  python party.py [party.json] [--out-dir builds]

--init asks for each member's name and PoB -- paste a pobb.in /
pastebin / poe.ninja / maxroll link or the raw code; each paste is
downloaded and decoded on the spot so typos surface immediately -- then
writes party.json and generates everything in one go.

Hand-editing party.json still works ("pob" takes a code, a build link,
or a path to a file holding either); mark your own character with
"me": true:

  {
    "members": [
      {"player": "CyrusChar", "pob": "<code, link, or file>", "me": true},
      {"player": "FriendChar", "pob": "<code, link, or file>"}
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
import sources

MAX_PARTY = 6
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

    notes_by_act = {}
    for note in notes:
        level = f"@{note['level']} " if "level" in note else ""
        notes_by_act.setdefault(note["act"], []).append(level + note["text"])

    return {
        "player": player,
        "role": member.get("role") or "",
        "pob": member.get("source") or member.get("pob") or "",
        "me": bool(member.get("me")),
        "class": info["class"],
        "ascendancy": info["ascendancy"],
        "notes": {act: "<br>".join(rows)
                  for act, rows in notes_by_act.items()},
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
            "role": m.get("role") or "",
            "pob": m.get("pob") or "",
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


def wizard(path, ask=input, say=print, fetch=None):
    """Interactive party.json builder: name + PoB link/code per member,
    every paste resolved and decoded on the spot so a dead link or
    mangled code is caught while the person who sent it is still on
    Discord. Writes `path` and returns the manifest dict.

    party.json stores the RESOLVED code (plus the original link as
    "source" for provenance), so re-running generation later needs no
    network and survives an expired paste.
    """
    if os.path.exists(path):
        if ask(f"{path} exists — overwrite? [y/N] ").strip().lower() != "y":
            raise SystemExit("keeping existing file; run without --init "
                             "to generate from it")
    say("Party setup — for each member paste a build link (pobb.in, "
        "pastebin, poe.ninja, maxroll ...) or the PoB code itself.")
    members = []
    while True:
        player = ask(f"\nPlayer {len(members) + 1} character name"
                     f"{' (blank = done)' if members else ''}: ").strip()
        if not player:
            if members:
                break
            say("  need at least one member")
            continue
        while True:
            pasted = ask(f"  {player}'s PoB link or code: ").strip()
            if not pasted:
                say("  nothing pasted — try again")
                continue
            if sources.is_url(pasted):
                say("  fetching (can take ~30s if the paste site is "
                    "having a moment) ...")
            try:
                code = sources.resolve(pasted, fetch=fetch)
                info = pob.build_info(pob.decode(code))
            except (sources.SourceError, *pob.DECODE_ERRORS) as e:
                say(f"  !! {e}")
                continue
            break
        asc = f" ({info['ascendancy']})" if info["ascendancy"] else ""
        say(f"  ok: {info['class']}{asc}, level {info['level']}")
        member = {"player": player, "pob": code}
        if pasted != code:
            member["source"] = pasted
        members.append(member)

    me = ask(f"\nWhich one is you? [{members[0]['player']}] ").strip().lower()
    idx = next((i for i, m in enumerate(members)
                if m["player"].lower() == me), 0)
    members[idx]["me"] = True

    manifest = {"members": members}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
    say(f"\nwrote {path}")
    return manifest


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("manifest", nargs="?", default="party.json",
                    help="party.json (default: ./party.json)")
    ap.add_argument("--init", action="store_true",
                    help="build the manifest interactively, then generate")
    ap.add_argument("--out-dir", default="builds")
    a = ap.parse_args()

    if a.init:
        manifest = wizard(a.manifest)
    else:
        try:
            with open(a.manifest, encoding="utf-8") as f:
                manifest = json.load(f)
        except FileNotFoundError:
            raise SystemExit(f"{a.manifest} not found — run "
                             f"`python {ap.prog} --init` to create it "
                             "interactively")
    members_cfg = manifest["members"]
    if not members_cfg:
        raise SystemExit("party.json has no members")
    if len(members_cfg) > MAX_PARTY:
        print(f"!! {len(members_cfg)} members; tooling is tuned for 2-{MAX_PARTY}")

    os.makedirs(a.out_dir, exist_ok=True)
    members = []
    for m in members_cfg:
        try:
            members.append(build_member(m, a.out_dir))
        except sources.SourceError as e:
            raise SystemExit(f"{m.get('player', '?')}: {e}")
        except pob.DECODE_ERRORS as e:
            raise SystemExit(f"{m.get('player', '?')}: bad PoB code/link "
                             f"({e}) — fix their entry in {a.manifest}")

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
