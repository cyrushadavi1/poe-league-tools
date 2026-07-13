"""Indexes the Exile-UI zone-layout image pack (see tools/fetch_layouts.py).

Pure stdlib -- no Qt imports -- so it can be unit tested headless.

Pack files are named '<areaID> <token>.jpg' where areaID matches the
'Generating level N area "<areaID>"' Client.txt line. Tokens follow
Exile-UI's convention:

    '1', '2', ... '9'   layout variants of the zone
    '1_1', '1_2', ...   continuation images of variant 1 (deeper floors,
                        the part of the layout past a transition, ...)
    'x'                 an extra image shared by every variant
    'y', 'y_1', ...     generic layout notes for the whole zone

A "variant head" is everything before the first '_' in the token; its
continuation images always share the head's fate (pinning variant 2
shows '2' plus every '2_*').
"""
import os
import re

_FILE_RE = re.compile(r"^(?P<area>\S+) (?P<token>\S+)\.(?:jpe?g|png)$",
                      re.IGNORECASE)


def _natural(token):
    """'1_10' sorts after '1_2': compare digit runs numerically.
    Parts map to uniform tuples so digit and letter parts ('x', 'y')
    stay mutually comparable."""
    return [(0, int(p), "") if p.isdigit() else (1, 0, p)
            for p in token.split("_")]


def _head(token):
    return token.split("_", 1)[0]


def _head_order(head):
    """Numbered variants first (numerically), then 'x', then 'y'."""
    return (0, int(head), "") if head.isdigit() else (1, 0, head)


class LayoutIndex:
    """area_id -> layout variants, built from one directory scan."""

    def __init__(self, root):
        self.root = root
        self.zones = {}                    # area_id -> {token: abspath}
        zones_dir = os.path.join(root, "zones")
        try:
            names = os.listdir(zones_dir)
        except OSError:
            names = []
        for name in names:
            m = _FILE_RE.match(name)
            if m:
                self.zones.setdefault(m.group("area"), {})[
                    m.group("token")] = os.path.join(zones_dir, name)
        self.count = sum(len(v) for v in self.zones.values())

    def has(self, area_id):
        return area_id in self.zones

    def variants(self, area_id):
        """[(head, [image paths])] for the zone, or [] if it has none.

        Paths per head start with the head image itself, then its
        continuations in natural order.
        """
        tokens = self.zones.get(area_id)
        if not tokens:
            return []
        heads = {}
        for token in sorted(tokens, key=_natural):
            heads.setdefault(_head(token), []).append(tokens[token])
        return [(h, heads[h]) for h in sorted(heads, key=_head_order)]
