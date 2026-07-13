#!/usr/bin/env python3
"""Writes fake Client.txt lines so the overlay can be developed and
demoed on a machine without the game (e.g. building on macOS, playing
on a Windows PC).

--out defaults to <system temp>/fake_client.txt and REFUSES paths that
look like a real game log (e.g. anything ending in logs/Client.txt):
the live Client.txt is strictly read-only in this repo.

Point the overlay at the fake log:
    python overlay/main.py --client /tmp/fake_client.txt

Interactive driver (type events, see the overlay react live):
    python tools/simulate_client.py repl --out /tmp/fake_client.txt
      z The Coast          -> you enter a zone
      a 1_1_2 [lvl] [seed] -> instance generated (drives the layouts panel)
      l Exile59 Witch 7    -> a player hits a level
      j FriendChar         -> player joins your area
      p FriendChar         -> player leaves your area
      d FriendChar         -> player dies
      raw <anything>       -> arbitrary system line

Auto-walk a whole route (with an optional simulated party):
    python tools/simulate_client.py walk --out /tmp/fake_client.txt \\
        --route routes/act1.json --interval 2 \\
        --me Exile59:Witch --party FriendA:Duelist,FriendB:Ranger
"""
import argparse
import datetime
import json
import os
import re
import tempfile
import time

STAMP_TAIL = "1234 ac9 [INFO Client 5]"
DEBUG_STAMP_TAIL = "1234 ac9 [DEBUG Client 5]"   # 'Generating ...' lines

HERE = os.path.dirname(os.path.abspath(__file__))
AREAS_JSON = os.path.join(HERE, "..", "data", "exileui", "areas.json")

# Cross-platform default (a bare /tmp does not exist on Windows).
DEFAULT_OUT = os.path.join(tempfile.gettempdir(), "fake_client.txt")


def looks_like_real_client(path):
    """True when `path` looks like a LIVE game log — the one file this
    repo must never write (INTERFACES.md invariant 1: Client.txt is
    read-only). Matches the overlay's COMMON_CLIENT_PATHS shapes and any
    '.../logs/Client.txt' inside a PoE-ish install dir."""
    p = os.path.abspath(path).replace("\\", "/").lower()
    return (p.endswith("/logs/client.txt")
            or "path of exile" in p
            or "grinding gear" in p)


def guard_out_path(path, override=False):
    """Refuse simulator writes into anything resembling the real log."""
    if override or not looks_like_real_client(path):
        return
    raise SystemExit(
        f"refusing to write to {path!r}: it looks like a REAL Path of "
        "Exile Client.txt, and the game log is strictly read-only "
        "(docs/INTERFACES.md invariant 1). Point --out at a scratch file "
        f"(default: {DEFAULT_OUT}) — or pass --i-know-what-im-doing if "
        "this truly is not the live game log.")


def ensure_parent(out):
    parent = os.path.dirname(os.path.abspath(out))
    if parent:
        os.makedirs(parent, exist_ok=True)


def sysline(msg):
    ts = datetime.datetime.now().strftime("%Y/%m/%d %H:%M:%S")
    return f"{ts} {STAMP_TAIL} : {msg}"


def append(out, msg):
    line = sysline(msg)
    ensure_parent(out)
    with open(out, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(f"  >> {line}")


def zone(out, name):
    append(out, f"You have entered {name}.")


def area(out, area_id, lvl=1, seed=1):
    """The DEBUG 'Generating ...' line the game writes before 'You have
    entered' -- drives the overlay's zone-layouts panel."""
    ts = datetime.datetime.now().strftime("%Y/%m/%d %H:%M:%S")
    line = (f"{ts} {DEBUG_STAMP_TAIL} Generating level {lvl} area "
            f'"{area_id}" with seed {seed}')
    ensure_parent(out)
    with open(out, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(f"  >> {line}")


def norm_zone(name):
    """Match tools/crosscheck_routes.norm: drop a leading 'The', write
    'X Level N' as 'X (N)' (the Exile-UI table's spelling)."""
    name = name.strip().lower()
    if name.startswith("the "):
        name = name[4:]
    return re.sub(r" level (\d+)$", r" (\1)", name)


def area_lookup(act):
    """normalized zone-name -> (area_id, lvl) for one act, from the
    vendored Exile-UI table; {} when the data file isn't there."""
    try:
        with open(AREAS_JSON, encoding="utf-8") as f:
            acts = json.load(f)
        entries = acts[act - 1]
    except (OSError, ValueError, IndexError):
        return {}
    return {norm_zone(e.get("name", "")): (e["id"], e.get("lvl", 1))
            for e in entries}


def level(out, name, cls, lvl):
    append(out, f"{name} ({cls}) is now level {lvl}")


def parse_char(spec):
    """'Name:Class' -> (name, class); class defaults to Witch."""
    name, _, cls = spec.partition(":")
    return name, cls or "Witch"


# ------------------------------------------------------------------ repl
def repl(a):
    ensure_parent(a.out)
    open(a.out, "a", encoding="utf-8").close()   # ensure file exists
    print(f"writing to {a.out} -- commands: z / l / j / p / d / raw / quit")
    while True:
        try:
            line = input("sim> ").strip()
        except EOFError:
            break
        if not line or line in ("q", "quit", "exit"):
            break
        cmd, _, rest = line.partition(" ")
        if cmd == "z" and rest:
            zone(a.out, rest)
        elif cmd == "a" and rest:
            parts = rest.split()
            try:
                area(a.out, parts[0],
                     int(parts[1]) if len(parts) > 1 else 1,
                     int(parts[2]) if len(parts) > 2 else 1)
            except ValueError:
                print("  usage: a <area_id> [lvl] [seed]")
        elif cmd == "l":
            try:
                name, cls, lvl = rest.split()
                level(a.out, name, cls, int(lvl))
            except ValueError:
                print("  usage: l <name> <class> <level>")
        elif cmd == "j" and rest:
            append(a.out, f"{rest} has joined the area.")
        elif cmd == "p" and rest:
            append(a.out, f"{rest} has left the area.")
        elif cmd == "d" and rest:
            append(a.out, f"{rest} has been slain.")
        elif cmd == "raw" and rest:
            append(a.out, rest)
        else:
            print("  ? commands: z <zone> | a <area_id> [lvl] [seed] | "
                  "l <name> <class> <lvl> | j/p/d <name> | raw <text> | quit")


# ------------------------------------------------------------------ walk
def walk(a):
    with open(a.route, encoding="utf-8") as f:
        route = json.load(f)
    steps = route["steps"]
    me_name, me_cls = parse_char(a.me)
    party = [parse_char(s) for s in a.party.split(",")] if a.party else []
    areas = area_lookup(route.get("act", 1))   # zone name -> (id, lvl)

    ensure_parent(a.out)
    open(a.out, "a", encoding="utf-8").close()
    print(f"walking {len(steps)} steps every {a.interval}s -> {a.out}")
    lvl = 1
    for i, step in enumerate(steps):
        time.sleep(a.interval)
        hit = areas.get(norm_zone(step["zone"]))
        if hit:                    # the real log Generates before it enters
            area(a.out, hit[0], step.get("arealvl", hit[1]), seed=1000 + i)
        zone(a.out, step["zone"])
        if i == 1:                        # party trickles in at first town
            for name, _ in party:
                append(a.out, f"{name} has joined the area.")
        if i % 2 == 1:                    # everyone levels as you go
            lvl += 1
            level(a.out, me_name, me_cls, lvl)
            for j, (name, cls) in enumerate(party):
                level(a.out, name, cls, max(1, lvl - 1 - j))
        if party and i == len(steps) // 2:    # mid-act drama
            append(a.out, f"{party[0][0]} has been slain.")
    print("route complete")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=DEFAULT_OUT,
                    help=f"fake Client.txt path (default {DEFAULT_OUT})")
    ap.add_argument("--i-know-what-im-doing", action="store_true",
                    dest="override_guard",
                    help="override the refusal to write paths that look "
                         "like a real PoE Client.txt")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("repl", help="interactive event driver")

    w = sub.add_parser("walk", help="auto-walk a route file")
    w.add_argument("--route", default="routes/act1.json")
    w.add_argument("--interval", type=float, default=2.0)
    w.add_argument("--me", default="Exile59:Witch", help="Name:Class")
    w.add_argument("--party", default="",
                   help="comma-separated Name:Class list (optional)")

    a = ap.parse_args()
    guard_out_path(a.out, override=a.override_guard)
    if a.cmd == "repl":
        repl(a)
    else:
        walk(a)


if __name__ == "__main__":
    main()
