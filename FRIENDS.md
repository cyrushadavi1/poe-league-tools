# Joining the party — PC setup (~5 minutes)

> Not comfortable with this kind of thing, or new to the game?
> Read **`START_HERE_EASY.md`** instead — same setup, explained
> one click at a time.

You got the Windows installer from whoever runs the builds. It gives you an
in-game-overlay leveling guide for the whole 10-act campaign, a party
row that tracks the others' levels/deaths, split timers, and instant
TAKE/SKIP verdicts on any item you Ctrl+C.

**Is this allowed?** Yes. It only ever *reads* `Client.txt` — a plain
text log the game itself writes, which GGG explicitly sanctions (it's
how every leveling tracker works). No memory reading, no injected
input, no automation; nothing leaves your machine.

## Setup

1. **Run `PoE-League-Tools-Setup.exe`.** No Python or admin rights are
   needed. Leave **Launch PoE League Tools** checked on the final screen.
2. A setup window opens:
   - Pick your build: **Carry, Aurabot, Banner, or Drugger**.
   - Enter your exact in-game character name.
   - *Client.txt* is usually found automatically; press **Use this build**.
3. **In the game:** Options → Graphics → Window Mode →
   **Windowed Fullscreen** (the overlay can't draw over exclusive
   fullscreen). English client only.
4. Start **PoE League Tools** from the Windows Start menu whenever you
   play. Drag the card wherever you like. It advances by itself as you
   zone, and if you're already partway through the campaign it
   fast-forwards to where you are (F2/F3 nudges it if it lands a step off).

Hotkeys: **F2/F3** step back/forward · **F4** hide/show ·
**F6** click-through (so the card never eats a click) ·
**F7** zone-layouts panel hide/show · **F10** change your selected PoB.

Card too big / in the way? **Mouse-wheel** over it to resize,
**double-click** to shrink it to one line, F6 to click straight
through it. When you enter a zone, a second panel shows every shape
the zone can roll — click the one matching your minimap and follow the
green line to the exit (right-click to see all of them again).

**Updating later:** run the newer installer. It keeps all answers,
settings, and run history.

Your printable leveling sheet is
`builds\allflame\<YourRole>_plan.md`, and
`builds\allflame\party_summary.md` shows everyone's gem links per act.
New to the game entirely? `BEGINNER_LEVELING.md` is the from-zero
companion: game basics plus an act-by-act walkthrough that matches
this route.

## If something looks wrong

Re-run the installer first. If the app still cannot start, send
`%LOCALAPPDATA%\PoE League Tools\logs\last-crash.log` to the group chat.

| Symptom | Usual cause |
|---|---|
| Overlay invisible in game | Game is in exclusive Fullscreen → set Windowed Fullscreen |
| Steps don't advance | Open **PoE League Tools - Setup or Change Character** from Start and choose Client.txt again (English client only) |
| Party row empty / levels stuck at `?` | Names don't match the real character names → press F10 |
| No gem reminders on steps | Press F10 and reselect the build; reinstall if its data is missing |
| No verdict when I Ctrl+C an item | That's item-eval — make sure you copied while hovering an item in game |
| Hotkeys dead | Another app grabbed F2–F10 → edit `%LOCALAPPDATA%\PoE League Tools\config.json` |
| No zone-layout pictures | Reinstall; the image pack is included |

Reinstalling is safe and keeps your UI and hotkey tweaks. To change only
the build, press F10.
