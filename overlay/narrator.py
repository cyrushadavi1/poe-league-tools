"""Spoken narration for the overlay -- reads route steps aloud.

For party members who can't comfortably read the card mid-fight: when
the route advances (Client.txt zone line -> RouteEngine), the current
step's instructions are spoken through the OS text-to-speech voice.
No screenshots, no game hooks -- the same Client.txt-only data the
card already renders, just out loud.

Zero new dependencies (INTERFACES invariant 2):
  - Windows: a persistent PowerShell child running
    System.Speech.Synthesis.SpeechSynthesizer (ships with Windows).
  - macOS (dev machines): /usr/bin/say per utterance.
  - Anywhere else: silent no-op backend.

Never blocks the poll path (invariant 5): Narrator.say() only swaps a
slot under a lock and notifies a daemon worker thread; all speaking
happens on the worker. The slot is latest-wins -- if you zone twice
before the voice finishes, the stale step is dropped, and by default
the in-flight utterance is cancelled so the voice never lags the game.

Import-safe: no side effects at import time (the worker thread starts
in the constructor). Pure helpers (step_text, clean) are unit-tested
headless in tests/test_narrator.py with an injected fake backend.
"""
import re
import subprocess
import sys
import threading

# Spoken replacements for glyphs/shorthand that read fine on the card
# but confuse or get mangled by a TTS voice.
_SPOKEN = [
    ("→", ", then "),      # ->
    ("☠", ""),             # skull (death flash)
    ("⚠", "warning: "),    # warning sign
    ("×", " times "),      # multiplication sign
]


def clean(text):
    """Card text -> speakable text. Pure."""
    for sym, spoken in _SPOKEN:
        text = text.replace(sym, spoken)
    text = re.sub(r"\bWP\b", "waypoint", text)
    return re.sub(r"\s+", " ", text).strip()


def step_text(step, tips=True, layout=True):
    """Route step dict -> one spoken paragraph. Pure; '' for no step.

    Zone name first (confirms the recognizer matched the area), then
    the 'do' lines, then layout hint and tip when enabled.
    """
    if not step:
        return ""
    bits = [step.get("zone", "")]
    bits += list(step.get("do") or [])
    if layout and step.get("layout"):
        bits.append(step["layout"])
    if tips and step.get("tip"):
        bits.append(step["tip"])
    joined = ". ".join(b.strip().rstrip(".") for b in bits if b and b.strip())
    return clean(joined) + "." if joined else ""


# ------------------------------------------------------------------ backends
# Backend contract: speak(text) blocks until done (or cancelled),
# cancel() aborts the in-flight utterance ASAP, close() releases OS
# resources. All three must never raise out (worker wraps them anyway).


class NullBackend:
    """Silent stand-in for platforms without a wired voice."""

    def speak(self, text):
        pass

    def cancel(self):
        pass

    def close(self):
        pass


class SayBackend:
    """macOS /usr/bin/say -- one child per utterance (dev machines)."""

    def __init__(self, rate=0):
        self._rate = int(rate)
        self._proc = None

    def speak(self, text):
        cmd = ["say"]
        if self._rate:
            # say takes words-per-minute; map the -10..10 config scale
            # around the ~175 wpm default.
            cmd += ["-r", str(175 + self._rate * 15)]
        self._proc = subprocess.Popen(cmd + [text])
        self._proc.wait()

    def cancel(self):
        p = self._proc
        if p is not None and p.poll() is None:
            try:
                p.terminate()
            except OSError:
                pass

    def close(self):
        self.cancel()


class PowerShellBackend:
    """Windows System.Speech via one persistent PowerShell child.

    Spawning powershell per utterance costs ~1 s of startup; instead one
    child loops on stdin lines and echoes a marker after each Speak so
    speak() knows when the utterance finished. cancel() kills the child
    mid-word (System.Speech has no clean async abort over stdin); the
    next speak() respawns it lazily.
    """

    _MARK = "##DONE##"
    _CODE = (
        "[Console]::InputEncoding = [System.Text.Encoding]::UTF8;"
        "Add-Type -AssemblyName System.Speech;"
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        "$s.Rate = %d; $s.Volume = %d;"
        "while ($true) {"
        " $l = [Console]::In.ReadLine();"
        " if ($l -eq $null) { break };"
        " $s.Speak($l);"
        " [Console]::Out.WriteLine('" + "##DONE##" + "');"
        " [Console]::Out.Flush()"
        " }"
    )

    def __init__(self, rate=0, volume=100):
        self._rate = max(-10, min(10, int(rate)))
        self._volume = max(0, min(100, int(volume)))
        self._proc = None
        self._lock = threading.Lock()   # spawn/kill vs. speak

    def _ensure(self):
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return self._proc
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self._proc = subprocess.Popen(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-Command", self._CODE % (self._rate, self._volume)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, creationflags=flags,
                text=True, encoding="utf-8", errors="replace")
            return self._proc

    def speak(self, text):
        proc = self._ensure()
        try:
            proc.stdin.write(text.replace("\n", " ") + "\n")
            proc.stdin.flush()
        except OSError:
            return                       # child died; next speak respawns
        while True:                      # wait for the done marker or EOF
            line = proc.stdout.readline()
            if not line or line.strip() == self._MARK:
                return

    def cancel(self):
        with self._lock:
            p, self._proc = self._proc, None
        if p is not None and p.poll() is None:
            try:
                p.kill()                 # unblocks speak() via stdout EOF
            except OSError:
                pass

    def close(self):
        self.cancel()


def default_backend(rate=0, volume=100):
    if sys.platform == "win32":
        return PowerShellBackend(rate=rate, volume=volume)
    if sys.platform == "darwin":
        return SayBackend(rate=rate)
    print("[narration] no TTS voice wired for this platform -- silent")
    return NullBackend()


# ------------------------------------------------------------------ narrator


class Narrator:
    """Latest-wins speech queue on a daemon worker thread.

    say() never blocks (safe on the Qt poll path). Only the newest
    pending text survives -- narration must track the game, so a step
    you already left is worthless out loud. interrupt=True (default)
    also cancels the utterance currently being spoken.
    """

    def __init__(self, backend=None, enabled=True, rate=0, volume=100):
        self.enabled = enabled
        self._backend = backend or default_backend(rate, volume)
        self._slot = None
        self._stop = False
        self._cv = threading.Condition()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="narrator")
        self._thread.start()

    def say(self, text, interrupt=True):
        if not self.enabled or not text:
            return
        self._post(text, interrupt)

    def toggle(self):
        """Flip mute and announce the new state (the announcement itself
        bypasses the mute so 'narration off' is the last thing heard)."""
        self.enabled = not self.enabled
        self._post("Narration on." if self.enabled else "Narration off.",
                   interrupt=True)
        return self.enabled

    def shutdown(self):
        """Stop the worker and kill any OS child. Safe to call twice."""
        with self._cv:
            self._stop = True
            self._slot = None
            self._cv.notify()
        try:
            self._backend.cancel()
            self._backend.close()
        except Exception:  # noqa: BLE001 -- exit path must never raise
            pass

    # ---------------------------------------------------------- internal

    def _post(self, text, interrupt):
        with self._cv:
            self._slot = text
            self._cv.notify()
        if interrupt:
            try:
                self._backend.cancel()
            except Exception:  # noqa: BLE001 -- cancel is best-effort
                pass

    def _loop(self):
        while True:
            with self._cv:
                while self._slot is None and not self._stop:
                    self._cv.wait()
                if self._stop:
                    return
                text, self._slot = self._slot, None
            try:
                self._backend.speak(text)
            except Exception:  # noqa: BLE001 -- a TTS hiccup must never
                pass           # take the worker (or the overlay) down
