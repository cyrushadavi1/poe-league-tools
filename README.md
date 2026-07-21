# PoE League Tools

A game assistant built entirely from the one file the developer allows you
to read: no memory access, no injection, no simulated input, just
`Client.txt`. **[Design writeup → kuros.io/work/poe-league-tools](https://kuros.io/work/poe-league-tools/)**

Toolkit for Path of Exile league starts (built for 3.29 *Curse of the
Allflame*, July 24 2026 — the campaign route itself is league-agnostic).
Designed for a **party of 2–3**: paste in everyone's PoB, get per-player
leveling kits, and the overlay guides you through all ten acts while
tracking your mates, your splits, and the loot on your clipboard.

Components:

1. **Campaign overlay** (`overlay/`) — always-on-top guide card, auto-advancing
   through a full 10-act route (187 steps) by watching `Client.txt`. Shows a
   party status row, per-act split timer vs. your PB, XP-penalty warnings,
   and instant TAKE/SKIP/CHECK verdicts on any item you Ctrl+C in game.
2. **Build tools** (`buildgen/`) — PoB codes → leveling sheets, overlay gem
   notes, and a party summary (gem links per act + uniques wishlist).
3. **Advisor** (`advisor/`) — LLM pipeline for the 3.29 patch notes:
   `summarize.py` (notes → structured deltas) → `advise.py` (build
   recommendations) → `exposure.py` (per-build nerf report).
4. **Market intelligence** (`market/`) — poe.ninja poller → SQLite →
   deterministic arbitrage scanner (cycles + spreads, anti-price-fixing
   filter) → terminal console with trade journal and PnL calibration.
5. **Tools** (`tools/`) — `simulate_client.py` (fake Client.txt for dev
   without the game), `tradeq.py` (English → trade-site query), `retro.py`
   (post-run analysis), `meta.py` (ninja ladder ranker), `verify_routes_llm.py`
   (wiki-grounded route checker), `check.py` (run every test suite).

## ToS safety (read this once)

This project only ever **reads the `Client.txt` text log**, which GGG
explicitly sanctions — it's how every legitimate leveling tracker works.
It never reads game memory, injects into the process, or sends input.
The market tools follow the same line: they fetch public data politely
(identified User-Agent, 1 request/2 s), **never** send whispers or execute
trades — the console's ceiling is copying whisper text to your clipboard
when you press a key. Party tracking, deaths, and levels are all lines the
game already writes to your own log. Nothing here should ever cross that
line.

## Party setup (start here)

1. Copy `buildgen/party.example.json` to `party.json`, paste each member's
   PoB code, mark yours with `"me": true`.
2. `python buildgen/party.py party.json --out-dir builds` → per-player
   `<name>_plan.md` + `<name>_notes.json`, `party_summary.md` with
   everyone's gem links per act and a uniques wishlist, and
   `party_bundle.json` — the manifest each PC sets itself up from.
   PoBs with act-tagged skill sets ("Act 1 …", "Act 3-5 …") get
   build-specific gem notes; a bare endgame-only PoB falls back to the
   generic per-class plan in `data/leveling_defaults.json` (edit it to
   taste) — labelled as generic in plan.md and by the doctor.
3. `python tools/make_portable.py` → `dist/poe-league-tools-pc.zip`
   (~92 MB): the whole toolkit **with a private Windows Python and PyQt6
   inside** — friends install nothing. Built from the Mac; needs network
   the first time (python.org + PyPI, cached in `dist/cache/`).
4. Ship that zip plus `FRIENDS.md` as the cover note
   (`START_HERE_EASY.md` is the zero-assumed-knowledge setup version,
   and `BEGINNER_LEVELING.md` the from-zero leveling companion, for
   the least experienced player). On each PC,
   `setup_pc.bat` finds Client.txt, asks "who are you?" and writes that
   machine's `overlay/config.json` — nobody installs or edits anything.
   (Skipped step 3? The zip-the-folder flow still works — `builds/` is
   gitignored so zip beats clone — friends then need Python 3.10+ and
   `setup_pc.bat` pip-installs the rest.)

Solo works too: `python buildgen/pob.py plan <code>`, empty party config.

## Overlay quickstart (gaming PC)

Windows, Python 3.10+, game in **Windowed Fullscreen**, English client.

```
setup_pc.bat                 (once — first-run wizard; installs deps only
                              when the folder has no bundled python\)
overlay\run_overlay.bat      (or: .venv\Scripts\python.exe overlay\main.py)
doctor.bat                   (health check — run when anything looks wrong)
```

The .bat files prefer, in order: the portable bundle's `python\`, the
repo `.venv`, a system Python. LLM extras (`anthropic`) need the venv
flavor — the embedded python ships without pip on purpose.

`Client.txt` is auto-detected (common installs, then every Steam library
via `libraryfolders.vdf`, then a per-drive scan — `overlay/find_client.py`);
the wizard persists what it finds, or set `client_txt` in
`overlay/config.json` by hand. Handing the folder to the other players?
Point them at `FRIENDS.md`.

Hotkeys (global on
Windows): **F2/F3** prev/next step · **F4** hide · **F6** click-through,
card and layouts panel together · **F7** layouts panel hide/show
(F6 is Windows-only — the non-Windows fallback shortcuts could not undo it).

Getting in the way of clicking? **Mouse-wheel** over the card (or the
layouts panel) resizes it, **double-click** collapses the card to just
its header line, **F6** makes everything click-through. Sizes and
positions persist across restarts (`overlay/ui_state.json`,
machine-written — not part of the shared config).

What the card shows: current step checklist + layout/tips + gem notes;
`● Name lvl` party row (● in your area, ⚠ level-gap ≥ `gap_warn`, ☠ deaths,
red flash when someone dies); `⏱ A3 41:22 (-2:10 PB) ⚠ XP -38%` timer/XP
row; and a 6-second color-coded verdict when you Ctrl+C an item in game
(pure local parsing — nothing leaves your machine). Feature flags in
config: `timer`, `item_eval`, `links_best`, `runs_dir`.

Starting the overlay mid-campaign (or restarting it)? It reads the log
tail and **fast-forwards to where you already are** — zone history plus
your level disambiguates repeated zone names like the act 1/6 towns
(`resume_route: false` to disable; F2/F3 to fine-tune the landing).

Runs are saved to `runs/` on exit; `python tools/retro.py runs/<file>.json`
prints splits vs PB, level curve, deaths — plus three concrete improvements
if the LLM extra is enabled.

## Zone layouts (the "act decoder")

Optional but great: ~470 hand-traced zone layout images from
[Exile-UI](https://github.com/Lailloken/Exile-UI) (MIT). Fetched by
`setup_pc.bat`, or by hand:

```
python tools/fetch_layouts.py
```

Entering a zone pops a panel with every layout the zone can roll
(white = zone outline, green = path to the exit, purple = waypoint).
Glance at your minimap, **left-click** the variant that matches — it
stays pinned (with any deeper-floor images) until the next zone;
**right-click** shows all variants again. The panel reads the area ID
from the same Client.txt line the game already writes, so ToS safety is
unchanged. `layouts.auto_show: false` in the config if you'd rather
summon it with F7 only.

## Updating

Your personal settings never get clobbered by an update: they live in
`overlay/config.json` and `overlay/ui_state.json`, which are
machine-written and untracked — pulling new code can't touch them.

**If you cloned with git** (needs [git](https://git-scm.com) installed):

```
git pull
setup_pc.bat
```

`setup_pc.bat` is safe to re-run every time: it upgrades dependencies,
keeps your existing answers in the setup wizard (just press Enter
through it), and updates the zone-layout image pack.

**If you got the portable zip** (no git, no Python install): download
the new `poe-league-tools-pc.zip`, unzip it next to your old folder,
copy your old `overlay\config.json` into the new folder (or skip that
and answer the setup wizard again), run `setup_pc.bat` once, then
delete the old folder.

**Zone-layout images only:** `python tools/fetch_layouts.py --check`
says whether the community pack has new images; re-run
`python tools/fetch_layouts.py` (or `setup_pc.bat`) to update it.

## LLM features (optional)

`pip install anthropic` + set `ANTHROPIC_API_KEY`. Everything degrades
gracefully without it; kill switch `POE_TOOLS_LLM=off`. Usage is metered to
`llm_usage.jsonl` (`python tools/llm_report.py` for spend by feature).

**July 16 (GGG Live) flow:** save the patch notes to a text file, then

```
python advisor/summarize.py notes.txt --out data/3.29/summary.json
python advisor/advise.py --summary data/3.29/summary.json --pob <code> [--pob <code>...]
python advisor/exposure.py <pob-code> --summary data/3.29/summary.json
python market/brief.py watchlist
```

Also available: `python tools/tradeq.py "boots 30 movespeed, life, cold res,
max 5c"` (validated trade-query JSON + site link), and the wiki-grounded
route audit (advisory only):

```
python tools/verify_routes_llm.py all
```

**Crafting copilot:** Ctrl+C an item in game, then

```
Get-Clipboard | python tools/craft_check.py - --level 34   # PC
pbpaste | python tools/craft_check.py - --no-llm           # Mac, digest only
```

prints what the mods are (tiers, prefix/suffix, open slots), what can
still roll on that base at its item level, usable essences, bench crafts
with costs, and — with the LLM — a grounded step-by-step plan. The odds
data is compiled from RePoE (`python tools/refresh_repoe.py`, rerun after
each patch); the model only selects and explains, it never invents
numbers. You execute every step by hand in game, as always.
League-start crafting principles + verified vendor recipes:
`docs/CRAFTING_GUIDELINES.md`.

## Market stack

```
python market/daemon.py            # poll poe.ninja → market/market.db
python market/console.py           # ranked opportunities; r/c/j/x/? commands
python market/brief.py daily       # one-page LLM brief (optional)
python tools/pnl.py                # realized vs expected — tune the haircut
python tools/meta.py               # ladder meta ranker (ascendancies/skills)
```

Scanner finds negative-cycle arbitrage and cross-source spreads over the
latest snapshots, filters price-fixed quotes, and sizes by liquidity and
bankroll (`market/config.json`; league is `Mirage` until 3.29 launches).
`c <row>` copies the next leg's whisper to your clipboard — sending it is
always your keypress in game.

## Developing on a Mac (playing on a PC)

Everything runs on macOS except global hotkeys (window-local fallback) and
the game itself — use the simulator:

```
python3.13 -m venv .venv && .venv/bin/pip install PyQt6      # once
.venv/bin/python tools/check.py                              # all 16 suites
.venv/bin/python overlay/main.py --client /tmp/fake_client.txt
.venv/bin/python tools/simulate_client.py --out /tmp/fake_client.txt \
    walk --route routes/act1.json --party FriendA:Duelist,FriendB:Ranger
```

(`repl` instead of `walk` to type zone/level/join/death events by hand.)
Deploy to the PC: copy the folder — `builds/` included — and run
`setup_pc.bat`; the wizard (`tools/join_party.py`) and the doctor
(`tools/preflight.py`) both run fine on macOS for testing.

## Route data

`routes/act1.json` … `act10.json` — full guided density (every quest, skill
point, trial, bandit choice, logout-warp tech), `arealvl` per step for XP
warnings. Format in `routes/schema.md`; `tests/test_routes_all.py` walks
all 187 steps end-to-end through the engine.

`tools/crosscheck_routes.py` validates every zone name and area level
against community data vendored from Exile-UI (`data/exileui/`) — it
runs in the test suite, so a typo that would break auto-advance can't
sneak in. `--coverage` also diffs our route against the community
leveling guide (differences are usually deliberate skips).

## Status / roadmap

- [x] Overlay engine + UI, Client.txt watcher, hotkeys, party row
- [x] PoB decode → plans + gem notes; multi-PoB party builder + wishlist
- [x] Acts 1–10 routes (wiki-verified, walked end-to-end in CI)
- [x] Split timers + XP-penalty warnings; run files + retro tool
- [x] Clipboard item evaluator (parser + rules)
- [x] LLM infra + advisor pipeline (awaiting July 16 patch notes)
- [x] Market stack: sources → store → scanner → console → PnL; briefs
- [x] Meta ranker, tradeq, LLM route verifier, Client.txt simulator
- [x] Friend onboarding: party bundle → `setup_pc.bat` wizard →
      `doctor.bat` health check (FRIENDS.md is the hand-out)
- [x] Zone-layout panel (Exile-UI image pack, F7) · resizable/compact
      overlay · routes cross-checked against community data
- [ ] **Before Jul 20:** Mirage rehearsal — 24 h daemon run, tune the
      price-fixing filter, capture real clipboard fixtures (see DECISIONS.md)
- [ ] **Jul 16:** feed patch notes to the advisor; generate the watchlist
- [ ] **Jul 24:** set the 3.29 league in `market/config.json`; re-verify
      poe.ninja endpoints + meta.py after launch

## Credits

Zone layout images and campaign reference data come from
[Exile-UI](https://github.com/Lailloken/Exile-UI) by Lailloken (MIT) —
the act-decoder image pack is fetched on demand by
`tools/fetch_layouts.py` (attribution ships alongside it in
`overlay/assets/layouts/ATTRIBUTION.md`), and `data/exileui/` vendors
two of its data tables for offline route validation.
