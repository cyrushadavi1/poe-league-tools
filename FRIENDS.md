# Joining the party — PC setup (~5 minutes)

You got this folder from whoever runs the builds. It gives you an
in-game-overlay leveling guide for the whole 10-act campaign, a party
row that tracks the others' levels/deaths, split timers, and instant
TAKE/SKIP verdicts on any item you Ctrl+C. Setup is two double-clicks.

**Is this allowed?** Yes. It only ever *reads* `Client.txt` — a plain
text log the game itself writes, which GGG explicitly sanctions (it's
how every leveling tracker works). No memory reading, no injected
input, no automation; nothing leaves your machine.

## Setup

1. **Is there a `python` folder inside this folder?** Then there's
   nothing to install — skip to step 2. (No `python` folder? Install
   Python 3.10+ from [python.org](https://python.org) first — click
   through the installer, keep the "py launcher" option checked.)
2. **Double-click `setup_pc.bat`** in this folder. It asks two things:
   - *Client.txt* — usually found automatically; just press Enter.
   - *Who are you?* — pick your character from the list.
3. **In the game:** Options → Graphics → Window Mode →
   **Windowed Fullscreen** (the overlay can't draw over exclusive
   fullscreen). English client only.
4. **Double-click `overlay\run_overlay.bat`.** Drag the card wherever
   you like. Done — it advances by itself as you zone, and if you're
   already partway through the campaign it fast-forwards to where you
   are (F2/F3 nudges it if it lands a step off).

Hotkeys: **F2/F3** step back/forward · **F4** hide/show ·
**F6** click-through (so the card never eats a click) ·
**F7** zone-layouts panel hide/show.

Card too big / in the way? **Mouse-wheel** over it to resize,
**double-click** to shrink it to one line, F6 to click straight
through it. When you enter a zone, a second panel shows every shape
the zone can roll — click the one matching your minimap and follow the
green line to the exit (right-click to see all of them again).

**Updating later:** re-download the folder (or `git pull` if you
cloned it), then double-click `setup_pc.bat` again — it keeps all your
answers and settings.

Your printable leveling sheet is `builds\<YourChar>_plan.md`, and
`builds\party_summary.md` shows everyone's gem links per act.

## If something looks wrong

**Double-click `doctor.bat`** and read the FAIL/WARN lines — each one
says how to fix itself. Screenshot it to the group chat if stuck.

| Symptom | Usual cause |
|---|---|
| Overlay invisible in game | Game is in exclusive Fullscreen → set Windowed Fullscreen |
| Steps don't advance | Wrong/missing Client.txt → re-run `setup_pc.bat` (or non-English client) |
| Party row empty / levels stuck at `?` | Names in config don't match the real character names → re-run `setup_pc.bat` |
| No gem reminders on steps | `builds\` folder missing or notes not wired → doctor.bat says which. (A build without act-tagged PoB sets gets a generic class plan automatically.) |
| No verdict when I Ctrl+C an item | That's item-eval — make sure you copied while hovering an item in game |
| Hotkeys dead | Another app grabbed F2–F7 → rebind in `overlay\config.json` under `hotkeys` |
| No zone-layout pictures | Image pack not fetched → re-run `setup_pc.bat` (or `python tools\fetch_layouts.py`) |

Re-running `setup_pc.bat` is always safe — it keeps your tweaks and
just re-asks the two questions.
