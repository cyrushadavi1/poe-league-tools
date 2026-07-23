"""Headless tests: narration text building + Narrator threading.

No Qt, no audio: the Narrator gets a fake backend that records what
would have been spoken. Timing-sensitive assertions use events, not
sleeps, except one bounded poll loop.
"""
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "overlay")]

from narrator import Narrator, clean, step_text     # noqa: E402

# ------------------------------------------------------------- clean()
assert clean("Turn in Enemy at the Gate → take your gem") == \
    "Turn in Enemy at the Gate , then take your gem"
assert clean("☠ YOU died") == "YOU died"
assert clean("⚠ XP -38%") == "warning: XP -38%"
assert clean("WP is roughly halfway") == "waypoint is roughly halfway"
# WP only replaced as a whole word
assert clean("Grab the WPvP flag") == "Grab the WPvP flag"
assert clean("a   b\n c") == "a b c"

# --------------------------------------------------------- step_text()
STEP = {
    "zone": "The Coast",
    "do": ["Tag the waypoint on the main path",
           "Take the seaward side exit to The Tidal Island"],
    "layout": "WP is roughly halfway along the path.",
    "tip": "Don't full-clear.",
}
full = step_text(STEP)
assert full.startswith("The Coast. "), full
assert "Tag the waypoint on the main path" in full
assert "waypoint is roughly halfway along the path" in full
assert full.endswith("Don't full-clear.")
# no doubled periods where card lines already end in one
assert ".." not in full, full

no_extras = step_text(STEP, tips=False, layout=False)
assert "halfway" not in no_extras and "full-clear" not in no_extras
assert no_extras.endswith("The Tidal Island.")

assert step_text(None) == ""
assert step_text({}) == ""
assert step_text({"zone": "Town"}) == "Town."

# ------------------------------------------------- Narrator threading


class FakeBackend:
    """Records utterances; can be made to block to simulate a slow voice."""

    def __init__(self):
        self.spoken = []
        self.cancels = 0
        self.block = None          # threading.Event -> speak waits on it
        self.started = threading.Event()

    def speak(self, text):
        self.started.set()
        if self.block is not None:
            self.block.wait(5)
        self.spoken.append(text)

    def cancel(self):
        self.cancels += 1

    def close(self):
        pass


def wait_for(cond, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


# basic: say() speaks through the backend, without blocking the caller
be = FakeBackend()
n = Narrator(backend=be)
n.say("hello exile")
assert wait_for(lambda: be.spoken == ["hello exile"]), be.spoken

# latest-wins: while the voice is busy, only the newest pending survives
be = FakeBackend()
be.block = threading.Event()
n = Narrator(backend=be)
n.say("step one", interrupt=False)
assert be.started.wait(5)                    # worker is inside speak()
n.say("step two", interrupt=False)           # queued...
n.say("step three", interrupt=False)         # ...and replaced
be.block.set()
assert wait_for(lambda: len(be.spoken) == 2), be.spoken
assert be.spoken == ["step one", "step three"], be.spoken

# interrupt=True (default) cancels the in-flight utterance
be = FakeBackend()
n = Narrator(backend=be)
n.say("zap me")
assert be.cancels >= 1

# muted narrator stays silent; toggle announces both flips out loud
be = FakeBackend()
n = Narrator(backend=be, enabled=False)
n.say("should not be spoken")
assert n.toggle() is True
assert wait_for(lambda: "Narration on." in be.spoken), be.spoken
assert n.toggle() is False
assert wait_for(lambda: "Narration off." in be.spoken), be.spoken
n.say("also not spoken")
time.sleep(0.05)
assert not [t for t in be.spoken if "spoken" in t], be.spoken

# empty text is a no-op (a step with nothing to say must not clear-throat)
be = FakeBackend()
n = Narrator(backend=be)
n.say("")
n.say(None)
time.sleep(0.05)
assert be.spoken == []

# shutdown stops the worker and is idempotent
n.shutdown()
n.shutdown()
assert wait_for(lambda: not n._thread.is_alive())

print("ALL TESTS PASSED")
