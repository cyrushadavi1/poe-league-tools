#!/usr/bin/env python3
"""Fetch RePoE game data and compile the crafting dataset (addendum 5G).

Usage:
    python tools/refresh_repoe.py                    # fetch + compile
    python tools/refresh_repoe.py --raw-dir DIR      # fetch into DIR, keep raw
    python tools/refresh_repoe.py --from-dir DIR     # offline: compile only
    python tools/refresh_repoe.py --out PATH         # default data/repoe_craft.json

Source: https://repoe-fork.github.io/ (community-maintained RePoE fork —
game data extracted from client files, served as static JSON; the same
substrate Craft of Exile builds on). Static files, no auth, no game-client
interaction. Fetches honor INTERFACES.md invariant 3 anyway: repo
User-Agent, 1 request / 2 s floor, Retry-After honored.

The compiled artifact (data/repoe_craft.json, ~2 MB) is what craft/pool.py
loads. It trims 22 MB of mods.min.json down to what the crafting copilot
needs:

  bases     name -> {cls, tags, lvl, dom}   released item/flask bases only
  mods      key  -> {n, gen, type, grp, lvl, sw, stats, t, ess, dom}
            every item/flask prefix/suffix mod — including zero-weight ones
            (delve/incursion/essence mods still appear ON items, so the
            matcher must know them; pool filtering by weight happens at
            runtime against the base's tags)
  essences  [{name, tier, max_ilvl, mods: {class: text}}]
            max_ilvl = RePoE item_level_restriction (low-tier essences
            cannot be applied to items above it; null = unrestricted)
  bench     [{master, tier, cost, classes, kind, t, gen, grp}]
            kind "mod" rows resolve add_explicit_mod through the full mods
            dict (domain "crafted"); other actions kept as kind "action"

mods.min.json already carries a rendered English `text` template per mod
("+(3-9) to maximum Life"), so stat_translations is not needed.

Stdlib only. Import-safe: network/IO only under main().
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

BASE_URL = "https://repoe-fork.github.io/"
USER_AGENT = "poe-league-tools/1.0 (contact: cyrus@hadavi.net)"
FILES = ("mods.min.json", "base_items.min.json", "essences.min.json",
         "crafting_bench_options.min.json")
MIN_INTERVAL_S = 2.0  # invariant 3: hard floor 1 request / 2 s

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = os.path.join(_ROOT, "data", "repoe_craft.json")

# What the copilot can reason about: gear + flask affixes.
_DOMAINS = ("item", "flask")
_GENERATIONS = ("prefix", "suffix")


# -------------------------------------------------------------------- fetch

_last_request = [0.0]


def _get(url, timeout=120):
    """One rate-limited GET. Returns bytes; honors Retry-After on 429/503."""
    wait = _last_request[0] + MIN_INTERVAL_S - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code in (429, 503):
            retry = exc.headers.get("Retry-After", "")
            delay = int(retry) if retry.isdigit() else 30
            time.sleep(delay)
            _last_request[0] = time.monotonic()
            return _get(url, timeout=timeout)
        raise
    _last_request[0] = time.monotonic()
    return data


def fetch_raw(raw_dir):
    """Download the RePoE files into raw_dir. Returns the detected game
    version string (from the data-formats page), or 'unknown'."""
    os.makedirs(raw_dir, exist_ok=True)
    for name in FILES:
        print(f"fetching {name} ...", flush=True)
        data = _get(BASE_URL + name)
        with open(os.path.join(raw_dir, name), "wb") as f:
            f.write(data)
    try:
        page = _get(BASE_URL + "data-formats/").decode("utf-8", "ignore")
        m = re.search(r"PoE version ([0-9][0-9.]*)", page)
        return m.group(1) if m else "unknown"
    except (urllib.error.URLError, OSError):
        return "unknown"


def load_raw(raw_dir):
    """Load the four raw JSON files from raw_dir into a dict by short name."""
    raw = {}
    for name in FILES:
        with open(os.path.join(raw_dir, name), encoding="utf-8") as f:
            raw[name.split(".")[0]] = json.load(f)
    return raw


# ------------------------------------------------------------------ compile


def _compile_bases(base_items):
    """Released item/flask bases by display name. On duplicate names keep
    the highest drop_level (legacy metadata ids shadow current bases)."""
    out = {}
    for b in base_items.values():
        if b.get("release_state") != "released":
            continue
        if b.get("domain") not in _DOMAINS:
            continue
        name = b.get("name") or ""
        cand = {"cls": b.get("item_class", ""),
                "tags": list(b.get("tags", [])),
                "lvl": int(b.get("drop_level", 1) or 1),
                # mods only roll in their own domain; without this, flask
                # mods would "spawn" on weapons via their 'default' tag
                "dom": b.get("domain", "item")}
        cur = out.get(name)
        if cur is None or cand["lvl"] > cur["lvl"]:
            out[name] = cand
    return out


def _compile_mods(mods):
    out = {}
    for key, m in mods.items():
        if m.get("domain") not in _DOMAINS:
            continue
        if m.get("generation_type") not in _GENERATIONS:
            continue
        stats = [[s.get("id", ""), s.get("min"), s.get("max")]
                 for s in m.get("stats", [])]
        out[key] = {
            "n": m.get("name", ""),
            "gen": m.get("generation_type"),
            "type": m.get("type", ""),
            "grp": list(m.get("groups", [])),
            "lvl": int(m.get("required_level", 1) or 1),
            "sw": [[w.get("tag", ""), int(w.get("weight", 0) or 0)]
                   for w in m.get("spawn_weights", [])],
            "stats": stats,
            # 3 of ~4600 mods lack text; fall back to their stat ids
            "t": m.get("text") or " / ".join(s[0] for s in stats),
            "ess": bool(m.get("is_essence_only")),
            "dom": m.get("domain"),
        }
    return out


def _compile_essences(essences, mods):
    out = []
    for e in essences.values():
        mods_text = {}
        for cls, key in e.get("mods", {}).items():
            m = mods.get(key)
            if m:
                mods_text[cls] = m.get("text") or key
        out.append({"name": e.get("name", ""),
                    "tier": int(e.get("level", 0) or 0),
                    "max_ilvl": e.get("item_level_restriction"),
                    "mods": mods_text})
    out.sort(key=lambda r: (r["name"], r["tier"]))
    return out


def _compile_bench(bench, mods, base_items):
    currency_names = {mid: (b.get("name") or mid.rsplit("/", 1)[-1])
                      for mid, b in base_items.items()}
    out = []
    for e in bench:
        actions = e.get("actions", {})
        cost = ", ".join(
            f"{n}x {currency_names.get(mid, mid.rsplit('/', 1)[-1])}"
            for mid, n in e.get("cost", {}).items())
        row = {"master": e.get("master", ""),
               "tier": int(e.get("bench_tier", 0) or 0),
               "cost": cost,
               "classes": list(e.get("item_classes", []))}
        key = actions.get("add_explicit_mod")
        if key:
            m = mods.get(key, {})
            row.update(kind="mod", t=m.get("text") or key,
                       gen=m.get("generation_type", ""),
                       grp=list(m.get("groups", [])))
        else:
            row.update(kind="action", gen="", grp=[],
                       t="; ".join(f"{k}={v}" for k, v in actions.items()))
        out.append(row)
    return out


def compile_data(raw, game_version="unknown"):
    """Pure: raw dict (load_raw shape) -> compiled artifact dict."""
    mods = raw["mods"]
    return {
        "meta": {
            "source": BASE_URL,
            "game_version": game_version,
            "generated": datetime.now(timezone.utc).isoformat(
                timespec="seconds"),
        },
        "bases": _compile_bases(raw["base_items"]),
        "mods": _compile_mods(mods),
        "essences": _compile_essences(raw["essences"], mods),
        "bench": _compile_bench(raw["crafting_bench_options"],
                                mods, raw["base_items"]),
    }


# --------------------------------------------------------------------- main


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--raw-dir", default=None,
                    help="download raw files here and keep them")
    ap.add_argument("--from-dir", default=None,
                    help="compile from an existing raw dir; no network")
    ap.add_argument("--game-version", default=None,
                    help="version stamp override (used with --from-dir)")
    args = ap.parse_args(argv)

    if args.from_dir:
        raw_dir, version = args.from_dir, args.game_version or "unknown"
    else:
        raw_dir = args.raw_dir or tempfile.mkdtemp(prefix="repoe_raw_")
        version = fetch_raw(raw_dir)
        if args.game_version:
            version = args.game_version

    compiled = compile_data(load_raw(raw_dir), game_version=version)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(compiled, f, separators=(",", ":"), ensure_ascii=False)
    size_mb = os.path.getsize(args.out) / 1e6
    print(f"wrote {args.out} ({size_mb:.1f} MB) — game version {version}: "
          f"{len(compiled['bases'])} bases, {len(compiled['mods'])} mods, "
          f"{len(compiled['essences'])} essences, "
          f"{len(compiled['bench'])} bench options")
    return 0


if __name__ == "__main__":
    sys.exit(main())
