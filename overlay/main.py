#!/usr/bin/env python3
"""PoE campaign overlay -- entry point.

Reads Client.txt (GGG-sanctioned), auto-advances a step-by-step route,
renders an always-on-top card, tracks your party's levels/deaths, times
your run per act (with XP-penalty warnings), and evaluates items you
Ctrl+C in game (parse + rules only -- nothing leaves your machine).
Run PoE in *Windowed Fullscreen* -- nothing can draw over exclusive
fullscreen.

Developing on a machine without the game (e.g. macOS)? Point the
overlay at a fake log and drive it with the simulator:

    python tools/simulate_client.py repl --out /tmp/fake_client.txt
    python overlay/main.py --client /tmp/fake_client.txt

Import-safe: importing this module has no side effects and pulls in no
Qt -- PyQt6 and overlay_window are imported inside main(). The pure
helpers (dispatch_events, evaluate_clipboard_text, tracker_status,
save_run) are unit-tested headless in tests/test_integration.py.
"""
import argparse
import json
import os
import sys

from client_watcher import ClientWatcher, last_known_level, recent_zones
from party_state import PartyState
from route_engine import RouteEngine
from run_tracker import RunTracker, xp_warning
import find_client
import item_rules
import itemtext

HERE = os.path.dirname(os.path.abspath(__file__))

# Clipboard payloads bigger than this are never parsed (a real Ctrl+C
# item export is well under 8 KB; anything larger isn't an item).
MAX_CLIP_CHARS = 8192


def _resolve(path):
    return path if os.path.isabs(path) else os.path.join(HERE, path)


def _find_client(cli_arg, cfg):
    if cli_arg:
        return cli_arg
    configured = cfg.get("client_txt") or ""
    found, how = find_client.discover(configured)
    if found:
        if how != "config":
            print(f"[client] auto-detected log ({how}): {found}\n"
                  "         (run setup_pc.bat once to save it to the config)")
        return found
    return configured


def _load_config(path):
    """config.json -> dict, or exit with a message a non-dev can act on.

    Friends hand-edit this file (or a copy of someone else's); a raw
    json traceback in a console window that closes is where onboarding
    dies, so both failure modes name the fix.
    """
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"!! No config at {path}\n"
              "   Run setup_pc.bat (repo root) once -- it writes this "
              "file for you.")
    except json.JSONDecodeError as e:
        print(f"!! {path} is not valid JSON: line {e.lineno}, col {e.colno}: "
              f"{e.msg}\n"
              "   Common causes: a missing/extra comma, or single \\ in a "
              "Windows path (use \\\\ or /).\n"
              "   Easiest fix: re-run setup_pc.bat -- the wizard rewrites "
              "the config cleanly.")
    sys.exit(2)


# ---------------------------------------------------------------- pure logic
# These helpers hold everything main()'s Qt callbacks decide, so the
# wiring is testable without a display (tests/test_integration.py).

def dispatch_events(events, engine, party, tracker=None):
    """Turn ClientWatcher events into UI operations. Pure logic.

    Feeds the route engine, party state and (optionally) the run tracker
    exactly the way the overlay's poll tick does, and returns the UI ops
    to apply, in order:

        ("refresh",)     re-render the current step card
        ("level", int)   my level changed (update header, then refresh)
        ("flash", str)   urgent transient message (a death)
        ("party", str)   new party status line
    """
    ops = []
    for kind, val in events:
        if kind == "zone":
            advanced = engine.on_zone(val)
            if tracker is not None:
                cur = engine.current() or {}
                tracker.on_zone(val, cur.get("act", 1))
            if advanced:
                ops.append(("refresh",))
            continue
        res = party.on_event(kind, val)
        if not res:
            continue
        what, data = res
        if what == "me_level":
            if tracker is not None:
                tracker.on_level(data)
            ops.append(("level", data))
            ops.append(("refresh",))
        elif what == "death":
            me = party.is_me(data)
            if me and tracker is not None:
                tracker.on_death(data)
            ops.append(("flash", f"☠ {'YOU' if me else data} died"))
        ops.append(("party", party.status_line()))
    return ops


def evaluate_clipboard_text(text, level, act, links_best=3):
    """Clipboard text -> (verdict, item_name, reason), or None to ignore.

    Fast pure parse + rules only -- no LLM, no IO, safe on the Qt main
    thread (INTERFACES invariant 5). Non-item text, empty text and
    oversized payloads all return None.
    """
    if not text or len(text) > MAX_CLIP_CHARS:
        return None
    parsed = itemtext.parse(text)
    if parsed is None:
        return None
    ctx = {"level": int(level), "act": int(act),
           "links_best": int(links_best), "build": None}
    verdict, reason = item_rules.evaluate(parsed, ctx)
    name = parsed.get("name") or parsed.get("base") or "item"
    return verdict, name, reason


def tracker_status(tracker, step, level):
    """Meta-row status text: act split timer + XP-penalty warning.

    E.g. 'A3 41:22 (-2:10 PB)  ⚠ XP -38%'. Empty string when the
    tracker has no active run and the current step carries no warning.
    """
    step = step or {}
    bits = []
    line = tracker.status_line(step.get("act", 1)) if tracker else ""
    if line:
        bits.append(line)
    arealvl = step.get("arealvl")
    if arealvl:
        warn = xp_warning(int(level), int(arealvl))
        if warn:
            bits.append(f"⚠ {warn}")
    return "  ".join(bits)


def save_run(tracker):
    """finish() + save the run file; tracker IO must never crash the
    overlay (called from aboutToQuit). Returns the saved path or None."""
    if tracker is None or not getattr(tracker, "run", None):
        return None
    try:
        return tracker.finish()
    except Exception as e:  # noqa: BLE001 -- IO failure on exit is non-fatal
        print(f"[tracker] could not save run: {e}")
        return None


# ----------------------------------------------------------------------- app

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(HERE, "config.json"))
    ap.add_argument("--client",
                    help="override the Client.txt path (e.g. a simulator "
                         "file when developing without the game)")
    args = ap.parse_args()
    cfg = _load_config(args.config)

    # Qt only from here on -- importing this module never pulls it in.
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication
    from overlay_window import OverlayWindow
    import hotkeys

    client = _find_client(args.client, cfg)
    client_missing = not (client and os.path.exists(client))
    if client_missing:
        print(f"!! Client.txt not found (configured: {client or '<unset>'});"
              " also probed the common\n"
              "   install paths, every Steam library and all drives.\n"
              "   Steps will NOT auto-advance until this is fixed:\n"
              "   run setup_pc.bat (or doctor.bat) in the repo root, or set\n"
              "   'client_txt' in overlay/config.json to your install's "
              "logs\\Client.txt.\n"
              "   (No game on this machine? See tools/simulate_client.py)")
        client = client or os.path.join(HERE, "Client.txt")  # watcher no-ops

    engine = RouteEngine(_resolve(cfg.get("routes_dir", "../routes")),
                         lookahead=cfg.get("lookahead", 4))
    watcher = ClientWatcher(client)

    pc = cfg.get("party") or {}
    party = PartyState(me=pc.get("me", ""),
                       members=pc.get("members", []),
                       gap_warn=pc.get("gap_warn", 3))

    tracker = None
    if cfg.get("timer", True):
        try:
            tracker = RunTracker(
                runs_dir=_resolve(cfg.get("runs_dir", "../runs")))
            tracker.start(character=pc.get("me", "") or "unknown",
                          cls="", league=cfg.get("league", "3.29"))
        except Exception as e:  # noqa: BLE001 -- timer is optional
            print(f"[tracker] disabled: {e}")
            tracker = None

    # Restarting mid-run? The watcher primes at EOF and never replays, so
    # recover the last-known level from the log tail — otherwise the
    # overlay reports level 1 (spurious XP warnings, wrong item context)
    # until the next real level-up.
    try:
        known = last_known_level(client, party.is_me)
    except Exception:  # noqa: BLE001 -- priming is best-effort
        known = None
    if known:
        party.my_level = known
        if tracker is not None:
            tracker.level = known
        print(f"[client] resumed at level {known} (from the log tail)")

    # Fast-forward the guide to where the log says the player already
    # is — a mid-campaign start otherwise opens on step 1 and means
    # F3-ing through half the route by hand.
    if cfg.get("resume_route", True):
        try:
            skipped = engine.fast_forward(recent_zones(client),
                                          party.my_level)
        except Exception:  # noqa: BLE001 -- resume is best-effort
            skipped = 0
        if skipped:
            cur = engine.current() or {}
            print(f"[route] fast-forwarded {skipped} steps to act "
                  f"{cur.get('act')}: {cur.get('zone')}  (F2/F3 to adjust;"
                  " resume_route:false in config to disable)")

    app = QApplication(sys.argv)
    win = OverlayWindow(cfg)
    win.set_level(party.my_level)

    bn = cfg.get("build_notes")
    if bn and os.path.exists(_resolve(bn)):
        with open(_resolve(bn), encoding="utf-8") as f:
            win.set_notes({int(x["act"]): x["text"] for x in json.load(f)})
    elif bn:
        # Configured but missing must be loud: "my gem reminders never
        # showed up" is otherwise indistinguishable from working-as-set-up.
        print(f"[notes] build_notes not found: {_resolve(bn)}\n"
              "        gem reminders are OFF -- re-run setup_pc.bat or fix "
              "'build_notes' in overlay/config.json")

    def refresh():
        win.show_step(engine.current(), engine.progress(), engine.peek())

    def on_next():
        engine.next()
        refresh()

    def on_prev():
        engine.prev()
        refresh()

    def tick():
        for op in dispatch_events(watcher.poll(), engine, party, tracker):
            if op[0] == "refresh":
                refresh()
            elif op[0] == "level":
                win.set_level(op[1])
            elif op[0] == "flash":
                win.flash(op[1])
            elif op[0] == "party":
                win.set_party(op[1])

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(cfg.get("poll_ms", 300))

    # -- run tracker: 1 s status refresh + autosave + save on exit ----------
    status_timer = None
    if tracker is not None:
        AUTOSAVE_EVERY_TICKS = 30          # 1 s ticks -> autosave every 30 s

        def update_status(_tick=[0]):
            try:
                win.set_status(
                    tracker_status(tracker, engine.current(), party.my_level))
                # Autosave the in-progress run (ended stays null): an
                # abnormal exit (taskkill, power cut, console X, an
                # aborting slot exception) never fires aboutToQuit, and
                # hours of splits must not die with the process.
                _tick[0] += 1
                if _tick[0] % AUTOSAVE_EVERY_TICKS == 0:
                    tracker.save()
            except Exception:  # noqa: BLE001 -- never crash the overlay
                pass

        status_timer = QTimer()
        status_timer.timeout.connect(update_status)
        status_timer.start(1000)
        update_status()
        app.aboutToQuit.connect(lambda: save_run(tracker))

    # SIGINT/SIGTERM/SIGBREAK -> clean Qt quit (saves the run via
    # aboutToQuit when a tracker exists). Installed regardless of the
    # tracker: without a handler, Ctrl+C raises KeyboardInterrupt inside
    # the next tick() slot and PyQt6 aborts the process (SIGABRT).
    # (Python-level handlers fire on the next poll tick.)
    try:
        import signal
        sigs = [signal.SIGINT, signal.SIGTERM]
        if hasattr(signal, "SIGBREAK"):    # Windows Ctrl+Break / console close
            sigs.append(signal.SIGBREAK)
        for sig in sigs:
            signal.signal(sig, lambda *_: app.quit())
    except (ImportError, ValueError, OSError):
        pass

    # -- clipboard item evaluator (parse + rules only; never blocks) --------
    if cfg.get("item_eval", True):
        clip = app.clipboard()

        def on_clipboard():
            try:
                res = evaluate_clipboard_text(
                    clip.text(), party.my_level,
                    (engine.current() or {}).get("act", 1),
                    cfg.get("links_best", 3))
                if res:
                    win.show_item(*res)
            except Exception:  # noqa: BLE001 -- weird clipboards must not crash
                pass

        clip.dataChanged.connect(on_clipboard)

    hk = cfg.get("hotkeys", {})
    bindings = {
        hk.get("prev", "F2"): on_prev,
        hk.get("next", "F3"): on_next,
        hk.get("toggle", "F4"): win.toggle_visible,
    }
    if sys.platform == "win32":
        # Clickthrough only with GLOBAL hotkeys: the non-Windows fallback
        # uses window-local shortcuts, and an input-transparent window can
        # never receive the keypress to toggle back — a one-way trap.
        bindings[hk.get("clickthrough", "F6")] = win.toggle_clickthrough
    else:
        print("[hotkeys] clickthrough disabled on this platform (window-"
              "local shortcuts could not undo it)")
    hotkeys.install(app, win, bindings)

    refresh()
    win.set_party(party.status_line())
    if client_missing:
        # A double-click launch may never read the console; park the
        # problem on the card itself. Party events would repaint this
        # row, but none can arrive without a log -- so it stays up
        # exactly as long as it is true.
        win.set_party("⚠ Client.txt not found — steps won't auto-advance "
                      "(run doctor.bat)")
    win.move(40, 140)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
