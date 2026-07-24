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


def group_passives(rows):
    """Return valid passive instructions sorted into allocation order."""
    out = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        if row.get("kind") not in {
                "passive", "passive-lab", "passive-ascendancy",
                "passive-respec"}:
            continue
        level = _level(row)
        text = str(row.get("text") or "").strip()
        if level is None or not text:
            continue
        clean = dict(row)
        clean["level"] = level
        clean["text"] = text
        out.append(clean)
    out.sort(key=lambda row: (
        row["level"],
        _level({"level": row.get("stage_index")}, 999),
        {"passive-respec": 0, "passive": 1, "passive-lab": 2,
         "passive-ascendancy": 2}.get(
            row.get("kind"), 9),
        _level({"level": row.get("point")}, 0),
    ))
    return out


def select_passives(rows, character_level):
    """Format this level's passive clicks, or the next upcoming clicks."""
    entries = [row for row in (rows or [])
               if isinstance(row, dict) and _level(row) is not None]
    if not entries:
        return ""
    exact = [row for row in entries if _level(row) == character_level]
    upcoming = False
    if not exact:
        later = [_level(row) for row in entries
                 if _level(row) > character_level]
        if not later:
            return ""
        chosen = min(later)
        exact = [row for row in entries if _level(row) == chosen]
        upcoming = True

    formatted = []
    for row in exact:
        kind = row.get("kind")
        if kind == "passive-respec":
            label = "RESPEC"
            stage = row.get("stage") or "new tree checkpoint"
            refunds = len(row.get("remove") or ())
            allocations = len(row.get("allocate") or ())
            text = (
                f"{stage} — refund {refunds}, allocate {allocations}; "
                "use the plan/PoB checklist"
            )
        elif kind == "passive-lab":
            label = "Lab"
            text = row["text"]
        elif kind == "passive-ascendancy":
            label = "Secondary ascendancy"
            text = row["text"]
        else:
            label = {
                "level-up": "Level",
                "quest": (
                    f"Quest ({row.get('source_name')})"
                    if row.get("source_name") else "Quest"
                ),
                "ascendancy-granted": "Ascendant",
                "remaining": "Final",
            }.get(row.get("source_type"), "Passive")
            text = row["text"]
        formatted.append(f"{label}: {text}")
    prefix = f"Next @{exact[0]['level']}: " if upcoming else ""
    return prefix + " • ".join(formatted)


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
