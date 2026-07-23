"""Load and select level-aware build notes without depending on Qt."""

import re


_ADAPTER_SOURCE_RE = re.compile(r"^adapter:(.+)$")


def group_notes(rows):
    """JSON rows -> ``{act: [rows...]}``, tolerating malformed extras."""
    out = {}
    for row in rows if isinstance(rows, list) else []:
        try:
            act = int(row["act"])
        except (KeyError, TypeError, ValueError):
            continue
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        clean = dict(row)
        clean["act"] = act
        clean["text"] = text
        out.setdefault(act, []).append(clean)
    for entries in out.values():
        entries.sort(key=lambda x: _level(x, -1))
    return out


def adapter_id(rows):
    """Return the one adapter id encoded in note sources, if unambiguous."""
    found = set()
    for row in rows if isinstance(rows, list) else []:
        match = _ADAPTER_SOURCE_RE.match(str(row.get("source", "")))
        if match:
            found.add(match.group(1))
    return next(iter(found)) if len(found) == 1 else None


def _level(row, default=None):
    try:
        return int(row.get("level"))
    except (AttributeError, TypeError, ValueError):
        return default


def select_note(value, character_level):
    """Pick the latest reached milestone, or the first upcoming one.

    Plain strings remain supported for old callers/configs.  If several
    notes share the selected level, they are joined rather than lost.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        value = [value]
    entries = [row for row in (value or [])
               if isinstance(row, dict) and row.get("text")]
    if not entries:
        return ""

    levelled = [(lvl, row) for row in entries
                if (lvl := _level(row)) is not None]
    reached = [(lvl, row) for lvl, row in levelled
               if lvl <= character_level]
    if reached:
        chosen = max(lvl for lvl, _ in reached)
        selected = [row["text"] for lvl, row in reached if lvl == chosen]
    elif levelled:
        chosen = min(lvl for lvl, _ in levelled)
        selected = [row["text"] for lvl, row in levelled if lvl == chosen]
    else:
        selected = [row["text"] for row in entries]
    return " • ".join(selected)
