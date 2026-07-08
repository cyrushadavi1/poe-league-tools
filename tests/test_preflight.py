"""Headless tests: the doctor (tools/preflight.py). No network, no Qt.

Builds a throwaway repo skeleton (overlay/config.json + fake Client.txt
+ notes + bundle) and asserts each failure mode surfaces as the right
OK/INFO/WARN/FAIL row -- the whole point of the doctor is that nothing
stays silently wrong.
"""
import json
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "overlay")]

from tools import preflight  # noqa: E402

OK, INFO, WARN, FAIL = (preflight.OK, preflight.INFO, preflight.WARN,
                        preflight.FAIL)
REAL_ROUTES = os.path.join(ROOT, "routes")
LOG_PRE = "2026/07/24 20:11:03 1234 ac9 [INFO Client 5]"


def rows_for(rows, name):
    return [r for r in rows if r[1] == name]


def has(rows, level, name, substr=""):
    return any(r[0] == level and r[1] == name and substr in r[2]
               for r in rows)


tmp = tempfile.mkdtemp(prefix="poe_preflight_test_")
try:
    overlay_dir = os.path.join(tmp, "overlay")
    builds_dir = os.path.join(tmp, "builds")
    runs_dir = os.path.join(tmp, "runs")
    for d in (overlay_dir, builds_dir, runs_dir):
        os.makedirs(d)

    client = os.path.join(tmp, "Client.txt")
    with open(client, "w", encoding="utf-8") as f:
        f.write(f"{LOG_PRE} : You have entered The Coast.\n"
                f"{LOG_PRE} : Exile59 (Witch) is now level 7\n")

    notes = os.path.join(tmp, "notes.json")
    with open(notes, "w", encoding="utf-8") as f:
        json.dump([{"act": 1, "text": "Fireball"},
                   {"act": 2, "text": "Flame Wall"}], f)

    with open(os.path.join(builds_dir, "party_bundle.json"), "w",
              encoding="utf-8") as f:
        json.dump({"league": "3.29", "members": [
            {"player": "CyrusChar", "me": True},
            {"player": "FriendA"}]}, f)

    config = os.path.join(overlay_dir, "config.json")

    def write_config(**over):
        cfg = {
            "client_txt": client, "routes_dir": REAL_ROUTES,
            "build_notes": "../notes.json", "timer": True,
            "runs_dir": "../runs",
            "party": {"me": "CyrusChar", "members": ["FriendA"],
                      "gap_warn": 3},
            "hotkeys": {"prev": "F2", "next": "F3", "toggle": "F4",
                        "clickthrough": "F6"},
        }
        cfg.update(over)
        with open(config, "w", encoding="utf-8") as f:
            json.dump(cfg, f)

    # ------------------------------------------------ happy path: no FAILs
    write_config()
    rows = preflight.run_all(config)
    assert not [r for r in rows if r[0] == FAIL], rows
    assert has(rows, OK, "Client.txt", client)
    assert has(rows, OK, "log activity")
    assert has(rows, OK, "log parse", "2 recognizable")
    assert has(rows, OK, "routes", "187 steps")
    assert has(rows, OK, "gem notes", "1, 2")
    assert has(rows, OK, "party", "me=CyrusChar")
    assert has(rows, OK, "run timer")
    out = []
    assert preflight.render(rows, say=out.append) == 0
    assert any("Windowed Fullscreen" in line for line in out)

    # ------------------------------------------------ config failure modes
    with open(config, "w", encoding="utf-8") as f:
        f.write('{"client_txt": "x",}')          # trailing comma
    rows = preflight.run_all(config)
    assert has(rows, FAIL, "config", "invalid JSON")
    assert preflight.render(rows, say=lambda s: None) == 1
    # ...the rest of the report still ran (crash-proof driver)
    assert rows_for(rows, "python") and rows_for(rows, "routes")

    write_config(clien_txt="oops")               # typo'd key
    rows = preflight.run_all(config)
    assert has(rows, WARN, "config keys", "clien_txt")

    write_config(hotkeys={"prev": "F2", "next": "F2", "toggle": "F4"})
    rows = preflight.run_all(config)
    assert has(rows, FAIL, "hotkeys", "F2")

    # ------------------------------------------------ party failure modes
    write_config(party={"me": "CyrusChar", "members": ["CyrusChar"]})
    rows = preflight.run_all(config)
    assert has(rows, FAIL, "party", "both me and a member")

    write_config(party={"me": "", "members": ["FriendA"]})
    rows = preflight.run_all(config)
    assert has(rows, FAIL, "party", "party.me is empty")

    write_config(party={"me": "CyrusChar ", "members": []})
    rows = preflight.run_all(config)
    assert has(rows, FAIL, "party", "spaces")

    write_config(party={"me": "Typo", "members": ["FriendA"]})
    rows = preflight.run_all(config)   # not in the bundle next door
    assert has(rows, WARN, "party", "party_bundle.json")

    write_config(party={"me": "", "members": []})
    rows = preflight.run_all(config)
    assert has(rows, INFO, "party", "solo")

    # ------------------------------------------------ notes failure modes
    write_config(build_notes="../gone.json")
    rows = preflight.run_all(config)
    assert has(rows, FAIL, "gem notes", "missing")

    bad_notes = os.path.join(tmp, "bad.json")
    with open(bad_notes, "w", encoding="utf-8") as f:
        f.write('{"not": "a list"}')
    write_config(build_notes="../bad.json")
    rows = preflight.run_all(config)
    assert has(rows, FAIL, "gem notes", "not a notes file")

    write_config(build_notes=None)
    rows = preflight.run_all(config)
    assert has(rows, INFO, "gem notes", "not set")

    # ------------------------------------------------ client log states
    write_config()
    old = time.time() - 8 * 86400                # stale log
    os.utime(client, (old, old))
    rows = preflight.run_all(config)
    assert has(rows, WARN, "log activity", "right install?")

    with open(client, "w", encoding="utf-8") as f:  # localized/garbage log
        f.write("2026/07/24 [INFO] Sie haben die Zone betreten.\n" * 5)
    rows = preflight.run_all(config)
    assert has(rows, WARN, "log parse", "non-English")

    open(client, "w").close()                    # empty (fresh) log
    rows = preflight.run_all(config)
    assert has(rows, INFO, "log activity", "empty")

    # client not found anywhere: force discovery to come up dry so the
    # suite also passes on a PC that has the real game installed
    import find_client
    real_discover = find_client.discover
    find_client.discover = lambda *a, **k: (None, "")
    try:
        write_config(client_txt=os.path.join(tmp, "nope.txt"))
        rows = preflight.run_all(config)
        assert has(rows, FAIL, "Client.txt", "not found")
    finally:
        find_client.discover = real_discover

    # ------------------------------------------------ routes failure mode
    write_config(routes_dir=os.path.join(tmp, "no_routes"))
    rows = preflight.run_all(config)
    assert has(rows, FAIL, "routes")

    # ------------------------------------------------ missing config file
    rows = preflight.run_all(os.path.join(tmp, "absent", "config.json"))
    assert has(rows, FAIL, "config", "missing")
    assert has(rows, FAIL, "config", "setup_pc.bat")

    # ------------------------------------------------ CLI smoke
    write_config()
    with open(client, "w", encoding="utf-8") as f:
        f.write(f"{LOG_PRE} : You have entered The Coast.\n")
    assert preflight.main(["--config", config]) == 0
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("ALL TESTS PASSED")
