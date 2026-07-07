# PoE League Tools

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
   `<name>_plan.md` + `<name>_notes.json`, and `party_summary.md` with
   everyone's gem links per act and a uniques wishlist.
3. Paste the printed `build_notes` + `party` block into `overlay/config.json`
   (each member runs their own overlay with their own name as `me`).

Solo works too: `python buildgen/pob.py plan <code>`, empty party config.

## Overlay quickstart (gaming PC)

Windows, Python 3.10+, game in **Windowed Fullscreen**, English client.

```
setup_pc.bat                 (once — creates .venv, installs PyQt6)
overlay\run_overlay.bat      (or: .venv\Scripts\python.exe overlay\main.py)
```

`Client.txt` is auto-detected at these common Steam/standalone paths; edit
`client_txt` in `overlay/config.json` otherwise:

```
C:\Program Files (x86)\Grinding Gear Games\Path of Exile\logs\Client.txt
C:\Program Files (x86)\Steam\steamapps\common\Path of Exile\logs\Client.txt
C:\Program Files\Grinding Gear Games\Path of Exile\logs\Client.txt
D:\SteamLibrary\steamapps\common\Path of Exile\logs\Client.txt
```

Hotkeys (global on
Windows): **F2/F3** prev/next step · **F4** hide · **F6** click-through
(F6 is Windows-only — the non-Windows fallback shortcuts could not undo it).

What the card shows: current step checklist + layout/tips + gem notes;
`● Name lvl` party row (● in your area, ⚠ level-gap ≥ `gap_warn`, ☠ deaths,
red flash when someone dies); `⏱ A3 41:22 (-2:10 PB) ⚠ XP -38%` timer/XP
row; and a 6-second color-coded verdict when you Ctrl+C an item in game
(pure local parsing — nothing leaves your machine). Feature flags in
config: `timer`, `item_eval`, `links_best`, `runs_dir`.

Runs are saved to `runs/` on exit; `python tools/retro.py runs/<file>.json`
prints splits vs PB, level curve, deaths — plus three concrete improvements
if the LLM extra is enabled.

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
Deploy to the PC: copy the folder (or git clone), run `setup_pc.bat`.

## Route data

`routes/act1.json` … `act10.json` — full guided density (every quest, skill
point, trial, bandit choice, logout-warp tech), `arealvl` per step for XP
warnings. Format in `routes/schema.md`; `tests/test_routes_all.py` walks
all 187 steps end-to-end through the engine.

## Status / roadmap

- [x] Overlay engine + UI, Client.txt watcher, hotkeys, party row
- [x] PoB decode → plans + gem notes; multi-PoB party builder + wishlist
- [x] Acts 1–10 routes (wiki-verified, walked end-to-end in CI)
- [x] Split timers + XP-penalty warnings; run files + retro tool
- [x] Clipboard item evaluator (parser + rules)
- [x] LLM infra + advisor pipeline (awaiting July 16 patch notes)
- [x] Market stack: sources → store → scanner → console → PnL; briefs
- [x] Meta ranker, tradeq, LLM route verifier, Client.txt simulator
- [ ] **Before Jul 20:** Mirage rehearsal — 24 h daemon run, tune the
      price-fixing filter, capture real clipboard fixtures (see DECISIONS.md)
- [ ] **Jul 16:** feed patch notes to the advisor; generate the watchlist
- [ ] **Jul 24:** set the 3.29 league in `market/config.json`; re-verify
      poe.ninja endpoints + meta.py after launch
