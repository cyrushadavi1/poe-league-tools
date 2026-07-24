"""Turn PoB tree snapshots into level-by-level allocation instructions.

PoB exports cumulative node sets, not the order in which the author clicked
the nodes.  This module preserves the author's snapshot order and derives a
deterministic connected route inside each snapshot using Path of Building's
passive-tree graph.  Derived rows retain node ids and stage URLs so the
instruction is auditable instead of pretending the missing click order was
authored.
"""
from __future__ import annotations

from collections import deque
import json
import os
import re


DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

# Typical character levels for the 24 campaign points when killing all
# bandits. Exact levels vary with routing/XP; the quest name is the source of
# truth and the overlay keeps an unspent point visible at the next level.
QUEST_POINT_EVENTS = (
    (5, "The Dweller of the Deep"),
    (8, "The Marooned Mariner"),
    (10, "The Way Forward"),
    (18, "Through Sacred Ground"),
    (21, "Deal with the Bandits (kill all)"),
    (28, "Victario's Secrets"),
    (31, "Piety's Pets"),
    (38, "An Indomitable Spirit"),
    (43, "In Service to Science"),
    (45, "Kitava's Torments"),
    (48, "The Father of War"),
    (50, "The Puppet Mistress"),
    (52, "The Cloven One"),
    (55, "The Master of a Million Faces"),
    (57, "Queen of Despair"),
    (59, "Kishara's Star"),
    (61, "Love is Dead"),
    (62, "Reflection of Terror"),
    (63, "The Gemling Legion"),
    (65, "Queen of the Sands"),
    (66, "The Ruler of Highgate"),
    (67, "Vilenta's Vengeance"),
    (68, "An End to Hunger"),
    (68, "An End to Hunger"),
)
LAB_LEVELS = (33, 55, 68, 80)

_RANGE_RE = re.compile(
    r"\b(?:levels?|leveling|lvl)\s*(\d+)\s*(?:[-–]\s*(\d+)|(\+))",
    re.I,
)
_SKIP_STAGE = re.compile(r"\b(?:backup|rarity|aspirational)\b", re.I)


def load_catalog(version: str) -> dict:
    version = (version or "3_29").replace(".", "_")
    path = os.path.join(DATA_DIR, f"passive_tree_{version}.json")
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}


def _stage_level(title: str, index: int, total: int) -> int:
    matches = list(_RANGE_RE.finditer(title or ""))
    if matches:
        match = matches[-1]
        return int(match.group(2) or match.group(1))
    low = (title or "").lower()
    for token, level in (
        ("aurabot swap", 67),
        ("early maps", 75),
        ("late maps", 90),
        ("post maps", 92),
        ("early game", 90),
        ("midgame", 96),
        ("endgame", 100),
    ):
        if token in low:
            return level
    return max(2, round(2 + (98 * (index + 1) / max(1, total))))


def _stage_start(title: str, index: int, total: int) -> int:
    matches = list(_RANGE_RE.finditer(title or ""))
    if matches:
        return max(2, int(matches[-1].group(1)))
    low = (title or "").lower()
    for token, level in (
        ("aurabot swap", 67),
        ("early maps", 68),
        ("late maps", 80),
        ("post maps", 90),
        ("early game", 68),
        ("midgame", 90),
        ("endgame", 96),
    ):
        if token in low:
            return level
    return max(2, round(2 + (98 * index / max(1, total))))


def _selected_specs(specs: list[dict]) -> list[dict]:
    chosen = []
    for spec in specs:
        if _SKIP_STAGE.search(spec.get("title", "")):
            continue
        chosen.append(spec)
        if re.search(r"\bendgame\b", spec.get("title", ""), re.I):
            break
    return chosen


def _flags(node: dict) -> set[str]:
    return set(node.get("flags") or ())


def _free(node: dict) -> bool:
    flags = _flags(node)
    return "ascendancy_start" in flags or "class_start" in node


def _is_ascendancy(node: dict) -> bool:
    return bool(node.get("ascendancy"))


def _shortest_special_path(
        seeds: set[str],
        remaining: set[str],
        nodes: dict[str, dict],
) -> list[str]:
    allowed = seeds | remaining
    distances = {}
    parent = {}
    queue = deque()
    for node_id in sorted(seeds, key=lambda value: int(value)):
        distances[node_id] = 0
        queue.append(node_id)
    while queue:
        current = queue.popleft()
        for neighbour in nodes.get(current, {}).get("connections", ()):
            if neighbour not in allowed or neighbour in distances:
                continue
            distances[neighbour] = distances[current] + 1
            parent[neighbour] = current
            queue.append(neighbour)

    specials = []
    for node_id in remaining:
        if node_id not in distances:
            continue
        flags = _flags(nodes.get(node_id, {}))
        if flags & {"notable", "mastery", "jewel"}:
            priority = (0 if "notable" in flags else
                        1 if "mastery" in flags else 2)
            specials.append((
                distances[node_id],
                priority,
                nodes[node_id].get("name", ""),
                int(node_id),
                node_id,
            ))
    if not specials:
        return []
    target = min(specials)[-1]
    path = [target]
    while path[-1] not in seeds:
        previous = parent.get(path[-1])
        if previous is None:
            return []
        path.append(previous)
    path.reverse()
    return path[1:]


def _node_text(
        node_id: str,
        node: dict,
        mastery_effects: dict[str, str],
        toward: str | None = None,
) -> str:
    name = node.get("name") or f"Passive node #{node_id}"
    if "mastery" in _flags(node):
        effect_id = mastery_effects.get(node_id)
        effect = (node.get("masteries") or {}).get(str(effect_id))
        if effect:
            return f"{name}: {effect}"
    if toward and toward != name and not (_flags(node) & {"notable", "mastery"}):
        return f"{name} (toward {toward})"
    return name


def _order_nodes(
        target: set[str],
        seeds: set[str],
        nodes: dict[str, dict],
        mastery_effects: dict[str, str],
) -> list[dict]:
    """Connected, deterministic ordering for one author snapshot delta."""
    remaining = set(target) - set(seeds)
    allocated = set(seeds)
    ordered = []
    while remaining:
        path = _shortest_special_path(allocated, remaining, nodes)
        if path:
            destination = nodes.get(path[-1], {}).get("name")
            for node_id in path:
                if node_id not in remaining:
                    continue
                node = nodes.get(node_id, {})
                ordered.append({
                    "node_id": node_id,
                    "text": _node_text(
                        node_id, node, mastery_effects, destination),
                })
                allocated.add(node_id)
                remaining.remove(node_id)
            continue

        connected = [
            node_id for node_id in remaining
            if any(neighbour in allocated for neighbour in
                   nodes.get(node_id, {}).get("connections", ()))
        ]
        if connected:
            node_id = min(
                connected,
                key=lambda value: (
                    nodes.get(value, {}).get("name", ""),
                    int(value),
                ),
            )
        else:
            # Cluster-jewel virtual nodes are not part of the static tree
            # graph. Keep their exact ids and author stage rather than
            # silently dropping them.
            node_id = min(remaining, key=int)
        node = nodes.get(node_id, {})
        ordered.append({
            "node_id": node_id,
            "text": _node_text(node_id, node, mastery_effects),
            "virtual": node_id not in nodes,
        })
        allocated.add(node_id)
        remaining.remove(node_id)
    return ordered


def _stage_rows(specs: list[dict], nodes: dict[str, dict]):
    sequence = []
    transitions = []
    ascendancy_order = []
    current_main: set[str] = set()
    current_asc: set[str] = set()

    class_starts = {
        node_id for node_id, node in nodes.items() if "class_start" in node
    }
    ascendancy_starts = {
        node_id for node_id, node in nodes.items()
        if "ascendancy_start" in _flags(node)
    }

    for index, spec in enumerate(specs):
        title = spec.get("title") or f"Tree stage {index + 1}"
        url = spec.get("url") or ""
        stage_level = _stage_level(title, index, len(specs))
        stage_start = _stage_start(title, index, len(specs))
        mastery_effects = spec.get("mastery_effects") or {}
        all_target = set(spec.get("nodes") or ())
        paid = {
            node_id for node_id in all_target
            if not _free(nodes.get(node_id, {}))
        }
        target_asc = {
            node_id for node_id in paid
            if _is_ascendancy(nodes.get(node_id, {}))
        }
        target_main = paid - target_asc

        removed = current_main - target_main
        main_seeds = (
            (current_main & target_main)
            | (all_target & class_starts)
        )
        additions = target_main - current_main
        ordered_additions = _order_nodes(
            target_main, main_seeds, nodes, mastery_effects)
        ordered_additions = [
            row for row in ordered_additions if row["node_id"] in additions
        ]

        respec_count = min(len(removed), len(ordered_additions))
        respec_additions = ordered_additions[:respec_count]
        if removed:
            remove_names = [
                f"{_node_text(node_id, nodes.get(node_id, {}), {})} "
                f"[{node_id}]"
                for node_id in sorted(removed, key=int)
            ]
            allocate_names = [
                f"{row['text']} [{row['node_id']}]"
                for row in respec_additions
            ]
            transitions.append({
                "kind": "passive-respec",
                "level": stage_level,
                "stage_start": stage_start,
                "stage_index": index,
                "stage": title,
                "tree_url": url,
                "remove": remove_names,
                "allocate": allocate_names,
                "text": (
                    f"RESPEC for {title}: refund "
                    f"{', '.join(remove_names)}; allocate "
                    f"{', '.join(allocate_names) or 'nothing yet'}"
                ),
                "source": "passive-tree:derived",
            })
        for row in ordered_additions[respec_count:]:
            row.update({
                "stage": title,
                "stage_level": stage_level,
                "stage_start": stage_start,
                "stage_index": index,
                "tree_url": url,
            })
            sequence.append(row)
        current_main = target_main

        asc_removed = current_asc - target_asc
        asc_seeds = (
            (current_asc & target_asc)
            | (all_target & ascendancy_starts)
        )
        asc_additions = target_asc - current_asc
        asc_ordered = _order_nodes(
            target_asc, asc_seeds, nodes, mastery_effects)
        asc_ordered = [
            row for row in asc_ordered if row["node_id"] in asc_additions
        ]
        for row in asc_ordered:
            row.update({
                "stage": title,
                "stage_level": stage_level,
                "stage_start": stage_start,
                "stage_index": index,
                "tree_url": url,
            })
        asc_respec_count = min(len(asc_removed), len(asc_ordered))
        if asc_removed:
            remove_names = [
                f"{_node_text(node_id, nodes.get(node_id, {}), {})} "
                f"[{node_id}]"
                for node_id in sorted(asc_removed, key=int)
            ]
            allocate_names = [
                f"{row['text']} [{row['node_id']}]"
                for row in asc_ordered[:asc_respec_count]
            ]
            transitions.append({
                "kind": "passive-respec",
                "level": stage_level,
                "stage_start": stage_start,
                "stage_index": index,
                "stage": title,
                "tree_url": url,
                "remove": remove_names,
                "allocate": allocate_names,
                "text": (
                    f"ASCENDANCY RESPEC for {title}: refund "
                    f"{', '.join(remove_names)}; allocate "
                    f"{', '.join(allocate_names) or 'nothing yet'}"
                ),
                "source": "passive-tree:derived",
            })
        ascendancy_order.extend(asc_ordered[asc_respec_count:])
        current_asc = target_asc
    return sequence, ascendancy_order, transitions


def build_plan(info: dict, specs: list[dict]) -> tuple[list[dict], list[str]]:
    """Return JSON-ready passive rows and any generation warnings."""
    chosen = _selected_specs(specs)
    if not chosen:
        return [], []
    version = chosen[0].get("tree_version") or "3_29"
    catalog = load_catalog(version)
    nodes = catalog.get("nodes") or {}
    if not nodes:
        return [], [
            f"Passive tree data for {version} is not installed; "
            "level-by-level allocations could not be generated."
        ]

    sequence, ascendancy, transitions = _stage_rows(chosen, nodes)
    max_level = max(2, min(100, int(info.get("level") or 100)))
    queue = deque(sequence)
    rows = []
    point = 0
    quests_by_level = {}
    for level, quest in QUEST_POINT_EVENTS:
        quests_by_level.setdefault(level, []).append(quest)

    # Assign the first eight choices in pairs to the conventional four labs.
    # 3.29 PoBs can contain later Bloodline/secondary-ascendancy pathing; put
    # those at their authored stage without pretending they came from a Lab.
    lab_rows = []
    extra_main_by_level = {}
    for index, allocation in enumerate(ascendancy):
        if index < 8:
            level = LAB_LEVELS[index // 2]
            kind = "passive-lab"
            source_type = "ascendancy"
        else:
            level = int(
                allocation.get("stage_start")
                or allocation.get("stage_level")
                or max_level
            )
            kind = "passive-ascendancy"
            source_type = "ascendancy-extra"
        node = nodes.get(allocation["node_id"], {})
        lab_rows.append({
            "kind": kind,
            "level": level,
            "source_type": source_type,
            "node_id": allocation["node_id"],
            "text": allocation["text"],
            "stage": allocation.get("stage", ""),
            "stage_index": allocation.get("stage_index", 999),
            "tree_url": allocation.get("tree_url", ""),
            "source": "passive-tree:derived",
        })
        grants = int(node.get("grants") or 0)
        if grants:
            extra_main_by_level[level] = (
                extra_main_by_level.get(level, 0) + grants)

    def take(level: int, source_type: str, source_name: str = ""):
        nonlocal point
        if not queue:
            return
        allocation = queue.popleft()
        point += 1
        rows.append({
            "kind": "passive",
            "level": level,
            "point": point,
            "source_type": source_type,
            "source_name": source_name,
            "node_id": allocation["node_id"],
            "text": allocation["text"],
            "stage": allocation.get("stage", ""),
            "stage_index": allocation.get("stage_index", 999),
            "tree_url": allocation.get("tree_url", ""),
            "virtual": bool(allocation.get("virtual")),
            "source": "passive-tree:derived",
        })

    for level in range(2, max_level + 1):
        take(level, "level-up")
        for quest in quests_by_level.get(level, ()):
            take(level, "quest", quest)
        for _ in range(extra_main_by_level.get(level, 0)):
            take(level, "ascendancy-granted")

    # A level-100 tree can legitimately contain points acquired at the end
    # of the campaign or granted by Ascendant. Keep them visible at the final
    # level instead of dropping an author-selected node.
    while queue:
        take(max_level, "remaining")

    # A respec must be shown before the first new click it enables. Snapshot
    # titles often describe the *end* of a range ("Lvl 33-46"), while point
    # counts make the next authored stage begin slightly earlier or later.
    # Anchor the transition to the first actual allocation for that stage;
    # a pure one-for-one swap falls back to the stage's authored start.
    first_by_stage = {}
    for row in rows:
        stage = row.get("stage")
        if stage:
            first_by_stage.setdefault(stage, row["level"])
    stage_titles = [
        spec.get("title") or f"Tree stage {index + 1}"
        for index, spec in enumerate(chosen)
    ]
    for transition in transitions:
        stage = transition.get("stage")
        level = first_by_stage.get(stage)
        if level is None and stage in stage_titles:
            index = stage_titles.index(stage)
            later = [
                first_by_stage[title] for title in stage_titles[index + 1:]
                if title in first_by_stage
            ]
            if later:
                # Pure one-for-one swaps have no earned-point row of their
                # own. Put them immediately before the next stage rather
                # than allowing that stage to appear first.
                level = min(
                    transition.get("stage_start") or transition["level"],
                    min(later),
                )
        transition["level"] = (
            level or transition.get("stage_start") or transition["level"])

    rows.extend(lab_rows)
    rows.extend(transitions)
    rows.sort(key=lambda row: (
        int(row.get("level", 0)),
        int(row.get("stage_index", 999)),
        {"passive-respec": 0, "passive": 1, "passive-lab": 2,
         "passive-ascendancy": 2}.get(
            row.get("kind"), 9),
        int(row.get("point", 0)),
    ))
    warnings = []
    if len(ascendancy) > 8:
        warnings.append(
            f"This PoB contains {len(ascendancy)} paid ascendancy clicks. "
            "The first 8 are assigned to the four Labs; later clicks are "
            "labelled secondary/extra ascendancy points at their authored "
            "tree stage.")
    virtual = sum(
        1 for row in rows if row.get("kind") == "passive"
        and row.get("virtual"))
    virtual += sum(
        text.startswith("Passive node #")
        for row in transitions
        for text in (row.get("remove") or []) + (row.get("allocate") or [])
    )
    if virtual:
        warnings.append(
            f"{virtual} cluster-jewel virtual-node instructions have exact "
            "node ids but no static-tree name; open the linked PoB stage for "
            "those clicks.")
    return rows, warnings


def passive_label(row: dict) -> str:
    source = row.get("source_type")
    prefix = {
        "level-up": "Level-up point",
        "quest": (
            f"Quest point ({row.get('source_name')})"
            if row.get("source_name") else "Quest point"
        ),
        "ascendancy-granted": "Ascendancy-granted point",
        "remaining": "Remaining point",
        "ascendancy": "Lab point",
        "ascendancy-extra": "Secondary/extra ascendancy point",
    }.get(source, "Passive")
    return f"{prefix}: {row.get('text', '')}"
