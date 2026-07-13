"""Tails Path of Exile's Client.txt and yields game events.

Pure stdlib -- no Qt imports -- so it can be unit tested headless.
Reading Client.txt is the GGG-sanctioned way to track game state;
this module never touches game memory and never sends input.

Assumes an English game client (log strings are localized).
"""
import os
import re

# System lines have nothing between the ']' and ':' -- player chat does
# ("] #Troll: ...", "] @From Bob: ..."). The prefix is matched from the
# START of the line (re.match) and requires ': ' immediately after the
# '[INFO Client N]' bracket, so a '] : ' planted inside a chat/whisper
# payload can never be the match point (chat always has 'Name:' between
# the bracket and the message).
_SYSTEM_RE = re.compile(
    r"^\S+ \S+ \S+ \S+ \[INFO Client \d+\] : (?P<msg>.*)$")

# Area generation is a DEBUG line with no ': ' separator:
#   ... [DEBUG Client 5] Generating level 2 area "1_1_2" with seed 1094830447
# It precedes the matching "You have entered X." INFO line and carries the
# internal area ID (keys the zone-layout images), the monster level and the
# instance seed. Anchored from line start like _SYSTEM_RE, so the pattern
# can't be spoofed from chat (chat is always '[INFO ...] : Name: ...').
AREA_RE = re.compile(
    r'^\S+ \S+ \S+ \S+ \[DEBUG Client \d+\] '
    r'Generating level (?P<lvl>\d+) area "(?P<id>[^"]+)" '
    r'with seed (?P<seed>\d+)\s*$')

# Event patterns run against the extracted system message, fully anchored.
# Guilded characters are prefixed with their guild tag ('<TAG> Bob has
# joined the area.'), hence the optional tag group on name-bearing events.
_TAG = r"(?:<[^>]*> )?"
ZONE_RE = re.compile(r"^You have entered (?P<zone>.+)\.\s*$")
LEVEL_RE = re.compile(
    r"^" + _TAG +
    r"(?P<name>\S+) \((?P<cls>[^)]+)\) is now level (?P<level>\d+)\s*$")
JOIN_RE = re.compile(r"^" + _TAG + r"(?P<name>\S+) has joined the area\.\s*$")
LEAVE_RE = re.compile(r"^" + _TAG + r"(?P<name>\S+) has left the area\.\s*$")
SLAIN_RE = re.compile(r"^" + _TAG + r"(?P<name>\S+) has been slain\.\s*$")


def parse_line(line):
    """Parse one log line into an event tuple, or None.

    ('zone', zone_name)               -- you entered a zone
    ('area', (area_id, lvl, seed))    -- an instance was generated for you
    ('level', (name, class, int))     -- any player (you or party) leveled
    ('join', name) / ('leave', name)  -- a player entered/left your area
    ('slain', name)                   -- a player died in your area
    """
    sm = _SYSTEM_RE.match(line)
    if not sm:
        m = AREA_RE.match(line)
        if m:
            return ("area", (m.group("id"), int(m.group("lvl")),
                             int(m.group("seed"))))
        return None
    msg = sm.group("msg")
    m = ZONE_RE.match(msg)
    if m:
        return ("zone", m.group("zone"))
    m = LEVEL_RE.match(msg)
    if m:
        return ("level", (m.group("name"), m.group("cls"),
                          int(m.group("level"))))
    m = JOIN_RE.match(msg)
    if m:
        return ("join", m.group("name"))
    m = LEAVE_RE.match(msg)
    if m:
        return ("leave", m.group("name"))
    m = SLAIN_RE.match(msg)
    if m:
        return ("slain", m.group("name"))
    return None


def last_known_level(path, is_me, tail_bytes=262144):
    """Most recent 'is now level N' in the file's tail for a character
    `is_me(name)` accepts, or None.

    The watcher primes at EOF and never replays history, so a mid-run
    overlay restart would otherwise report level 1 (spurious XP warnings)
    until the next real level-up.
    """
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - tail_bytes))
            text = f.read().decode("utf-8", errors="ignore")
    except OSError:
        return None
    level = None
    for line in text.splitlines():
        ev = parse_line(line)
        if ev and ev[0] == "level" and is_me(ev[1][0]):
            level = ev[1][2]
    return level


def last_area(path, tail_bytes=262144):
    """Most recent ('area', ...) payload in the file's tail, or None.

    Same restart problem as last_known_level: the watcher primes at EOF,
    so without this the layouts panel stays blank until the next zone
    transition after an overlay restart.
    """
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - tail_bytes))
            text = f.read().decode("utf-8", errors="ignore")
    except OSError:
        return None
    area = None
    for line in text.splitlines():
        ev = parse_line(line)
        if ev and ev[0] == "area":
            area = ev[1]
    return area


def recent_zones(path, tail_bytes=4194304):
    """Zone names from 'You have entered' lines in the log tail, oldest
    first (a few MB ≈ hours of play). Powers the startup route
    fast-forward; chat spoofing is already rejected by parse_line."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - tail_bytes))
            text = f.read().decode("utf-8", errors="ignore")
    except OSError:
        return []
    return [ev[1] for ev in map(parse_line, text.splitlines())
            if ev and ev[0] == "zone"]


class ClientWatcher:
    """Incremental log reader. Call poll() regularly; returns new events."""

    def __init__(self, path):
        self.path = path
        self._pos = 0
        self._prime()

    def _prime(self):
        """Start at the end of the file so history isn't replayed."""
        try:
            self._pos = os.path.getsize(self.path)
        except OSError:
            self._pos = 0

    def poll(self):
        events = []
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return events
        if size < self._pos:          # file truncated/replaced -> restart
            self._pos = 0
        if size == self._pos:
            return events
        with open(self.path, "rb") as f:
            f.seek(self._pos)
            chunk = f.read()
        # Only consume complete lines. The game's buffered writer can
        # flush mid-line; parsing the fragment now (its completion never
        # re-read) would silently drop the event -- or parse a truncated
        # '...is now level 4' as the wrong level. The partial tail stays
        # unconsumed until its newline arrives.
        end = chunk.rfind(b"\n")
        if end < 0:
            return events
        self._pos += end + 1
        text = chunk[:end + 1].decode("utf-8", errors="ignore")
        for line in text.splitlines():
            ev = parse_line(line)
            if ev:
                events.append(ev)
        return events
