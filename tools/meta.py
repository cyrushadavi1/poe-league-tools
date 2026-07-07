"""poe.ninja build-meta ranker — top ascendancies and main skills by ladder share.

Usage:
    python tools/meta.py [--league <name-or-slug>] [--top 25] [--no-delta]

Prints a ranked table of the league's ladder meta from poe.ninja's builds
aggregation: top "classes" (ascendancies; unascended base classes appear
under their base name) and top main skills, each with character count and
% of ladder share, plus week-over-week share deltas (percentage points)
when poe.ninja has a 'week-1' time-machine snapshot for the league.

Endpoints — live-verified 2026-07-07 against league "Ancestors" (3.28):

1.  GET https://poe.ninja/poe1/api/data/index-state                (JSON)
    -> {"snapshotVersions": [{"url": "<league-slug>", "type": "exp",
        "name": "Ancestors", "version": "2002-20260707-55341",
        "snapshotName": "ancestors", "timeMachineLabels": ["week-1", ...]},
        ...], "economyLeagues": [...], "buildLeagues": [...]}
2.  GET https://poe.ninja/poe1/api/builds/{version}/search
        ?overview=<snapshotName>&type=exp[&timemachine=week-1]
    -> application/x-protobuf, NinjaSearchResult (schema below)
3.  GET https://poe.ninja/poe1/api/builds/dictionary/{hash}
    -> application/x-protobuf, SearchResultDictionary

The legacy JSON endpoint (/api/data/x/getbuildoverview?overview=...&type=exp
&language=en) and /api/data/getindexstate return 404 as of 2026-07-07: the
builds API moved under /poe1/api/ and switched to protobuf. The response
does not honor `Accept: application/json` (verified live).

Protobuf message schema, recovered from poe.ninja's own JS bundle
(assets.poe.ninja/_astro/a.CGfO4jcE.mjs) and verified by decoding live
responses (only the fields this tool reads are listed):

    NinjaSearchResult              { 1: result (SearchResult) }
    SearchResult                   { 1: total (int32)
                                     2: dimensions (repeated SearchResultDimension)
                                     6: dictionaries (repeated SearchResultDictionaryReference) }
    SearchResultDimension          { 1: id (string) e.g. "class", "skills"
                                     2: dictionary_id (string) e.g. "class", "gem"
                                     3: counts (repeated SearchResultDimensionCount) }
    SearchResultDimensionCount     { 1: key (int32, index into dictionary values)
                                     2: count (int32) }
    SearchResultDictionaryReference{ 1: id (string), 2: hash (string) }
    SearchResultDictionary         { 1: id (string), 2: values (repeated string) }

VERIFY: the /poe1/api/builds API is undocumented and versioned per snapshot
(the {version} path segment changes as poe.ninja re-snapshots); re-check the
endpoints and schema after the 3.29 launch (July 24 2026) before relying on
day-1 output.

Exit codes: 0 ok, 1 network/API/league failure (clear message on stderr),
2 bad CLI arguments (argparse).

Stdlib only. No LLM use. Read-only HTTP GETs to poe.ninja under the repo
rate rules (User-Agent, 1 request / 2 s floor, Retry-After honored).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

BASE = "https://poe.ninja"
INDEX_STATE_URL = BASE + "/poe1/api/data/index-state"
SEARCH_URL = BASE + "/poe1/api/builds/{version}/search?overview={overview}&type={type}"
DICTIONARY_URL = BASE + "/poe1/api/builds/dictionary/{hash}"
USER_AGENT = "poe-league-tools/1.0 (contact: cyrus@hadavi.net)"
MIN_INTERVAL_S = 2.0          # hard request floor (INTERFACES.md invariant 3)
MAX_ATTEMPTS = 3
WEEK_LABEL = "week-1"

_last_request = [0.0]         # module-level throttle state


class MetaError(RuntimeError):
    """Any failure fetching or parsing poe.ninja build data."""


# --------------------------------------------------------------- HTTP layer

def _retry_after_seconds(value) -> float:
    """Parse a Retry-After header: seconds or HTTP-date; floor on garbage.

    Never caps the server-requested wait (invariant 3: Retry-After honored).
    Mirrors market/sources.py::_retry_after_seconds.
    """
    if value is None or str(value).strip() == "":
        return MIN_INTERVAL_S
    try:
        return max(float(value), 0.0)
    except (TypeError, ValueError):
        pass
    try:
        dt = parsedate_to_datetime(str(value))
        return max((dt - datetime.now(timezone.utc)).total_seconds(), 0.0)
    except (TypeError, ValueError):
        return MIN_INTERVAL_S


def _get(url: str, timeout: float = 30.0) -> bytes:
    """Rate-limited GET honoring 429/Retry-After. Raises MetaError."""
    last_err: Exception | None = None
    for _ in range(MAX_ATTEMPTS):
        wait = _last_request[0] + MIN_INTERVAL_S - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request[0] = time.monotonic()
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 429:
                # Sleep the full server-requested wait, uncapped.
                delay = max(_retry_after_seconds(e.headers.get("Retry-After")),
                            MIN_INTERVAL_S)
                time.sleep(delay)
                continue
            raise MetaError(f"HTTP {e.code} from {url}") from e
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            last_err = e
            raise MetaError(f"network error fetching {url}: {e}") from e
    raise MetaError(f"still rate-limited after {MAX_ATTEMPTS} attempts: {url}"
                    ) from last_err


# ------------------------------------------------- protobuf wire-format read

def _read_varint(buf: bytes, i: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        if i >= len(buf):
            raise MetaError("malformed protobuf: truncated varint")
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7
        if shift > 63:
            raise MetaError("malformed protobuf: varint too long")


def decode_fields(buf: bytes) -> list[tuple[int, int, object]]:
    """Decode one message level into [(field_no, wire_type, value)].

    wire 0 -> int, wire 2 -> bytes, wires 1/5 -> raw fixed bytes (skipped by
    callers). Raises MetaError on anything malformed.
    """
    i, out = 0, []
    while i < len(buf):
        tag, i = _read_varint(buf, i)
        fno, wire = tag >> 3, tag & 7
        if fno == 0:
            raise MetaError("malformed protobuf: field number 0")
        if wire == 0:
            val, i = _read_varint(buf, i)
        elif wire == 2:
            length, i = _read_varint(buf, i)
            if i + length > len(buf):
                raise MetaError("malformed protobuf: truncated field")
            val = buf[i:i + length]
            i += length
        elif wire == 1:
            val, i = buf[i:i + 8], i + 8
        elif wire == 5:
            val, i = buf[i:i + 4], i + 4
        else:
            raise MetaError(f"malformed protobuf: unsupported wire type {wire}")
        if i > len(buf):
            raise MetaError("malformed protobuf: truncated field")
        out.append((fno, wire, val))
    return out


def _str(v: object) -> str:
    if not isinstance(v, bytes):
        raise MetaError("malformed protobuf: expected string field")
    try:
        return v.decode("utf-8")
    except UnicodeDecodeError as e:
        raise MetaError("malformed protobuf: bad utf-8") from e


def _first(fields: list, fno: int, default=None):
    for f, _, v in fields:
        if f == fno:
            return v
    return default


def parse_search(blob: bytes) -> dict:
    """Parse a NinjaSearchResult blob.

    Returns {"total": int, "dimensions": {id: {"dictionary_id": str,
    "counts": [(key, count), ...]}}, "dictionary_hashes": {id: hash}}.
    """
    top = decode_fields(blob)
    result = _first(top, 1)
    if not isinstance(result, bytes):
        raise MetaError("malformed response: no SearchResult in envelope")
    sr = decode_fields(result)
    total = _first(sr, 1)
    if not isinstance(total, int):
        raise MetaError("malformed response: missing total")
    dims: dict[str, dict] = {}
    hashes: dict[str, str] = {}
    for fno, wire, val in sr:
        if fno == 2 and wire == 2:
            dm = decode_fields(val)
            dim_id = _str(_first(dm, 1, b""))
            dict_id = _str(_first(dm, 2, b""))
            counts = []
            for a, w, x in dm:
                if a == 3 and w == 2:
                    cf = decode_fields(x)
                    key = _first(cf, 1, 0)
                    cnt = _first(cf, 2, 0)
                    if not isinstance(key, int) or not isinstance(cnt, int):
                        raise MetaError("malformed response: bad count entry")
                    counts.append((key, cnt))
            dims[dim_id] = {"dictionary_id": dict_id, "counts": counts}
        elif fno == 6 and wire == 2:
            dr = decode_fields(val)
            hashes[_str(_first(dr, 1, b""))] = _str(_first(dr, 2, b""))
    return {"total": total, "dimensions": dims, "dictionary_hashes": hashes}


def parse_dictionary(blob: bytes) -> dict:
    """Parse a SearchResultDictionary blob -> {"id": str, "values": [str]}."""
    fields = decode_fields(blob)
    dict_id = _first(fields, 1)
    if not isinstance(dict_id, bytes):
        raise MetaError("malformed dictionary: missing id")
    values = [_str(v) for f, w, v in fields if f == 2 and w == 2]
    if not values:
        raise MetaError("malformed dictionary: no values")
    return {"id": _str(dict_id), "values": values}


# ------------------------------------------------------------------ ranking

def shares(dim: dict, values: list[str], total: int) -> dict[str, int]:
    """Resolve a dimension's counts to {name: count} via its dictionary."""
    out: dict[str, int] = {}
    for key, count in dim["counts"]:
        if not 0 <= key < len(values):
            raise MetaError(f"malformed response: dictionary key {key} out of "
                            f"range (dictionary has {len(values)} values)")
        out[values[key]] = out.get(values[key], 0) + count
    return out


def rank(counts_by_name: dict[str, int], total: int, top: int,
         prev: dict[str, int] | None = None,
         prev_total: int = 0) -> list[dict]:
    """Rank by count desc (name asc tiebreak). pct = share of `total`.

    With `prev` (last week's {name: count} and prev_total), adds `delta_pp`:
    percentage-point change of share, or None if the name wasn't in prev.
    """
    rows = sorted(counts_by_name.items(), key=lambda kv: (-kv[1], kv[0]))[:top]
    out = []
    for name, count in rows:
        pct = round(100.0 * count / total, 2) if total else 0.0
        entry = {"name": name, "count": count, "pct": pct}
        if prev is not None:
            if name in prev and prev_total:
                prev_pct = 100.0 * prev[name] / prev_total
                entry["delta_pp"] = round(100.0 * count / total - prev_pct, 2) \
                    if total else None
            else:
                entry["delta_pp"] = None
        out.append(entry)
    return out


# ----------------------------------------------------------------- pipeline

def pick_snapshot(index_state: dict, league: str | None) -> dict:
    """Pick the type=='exp' snapshot for `league` (slug or name, case-
    insensitive). league=None -> poe.ninja's first exp snapshot (the current
    challenge league). Raises MetaError listing what's available."""
    snaps = [s for s in index_state.get("snapshotVersions", [])
             if s.get("type") == "exp"]
    if not snaps:
        raise MetaError("malformed index-state: no exp snapshots")
    if league is None:
        return snaps[0]
    want = league.casefold()
    for s in snaps:
        if want in (str(s.get("url", "")).casefold(),
                    str(s.get("name", "")).casefold(),
                    str(s.get("snapshotName", "")).casefold()):
            return s
    known = ", ".join(f"{s.get('name')} ({s.get('url')})" for s in snaps)
    raise MetaError(f"league {league!r} not found on poe.ninja; "
                    f"available: {known}")


def _dim_names(search: dict, dim_id: str, dictionaries: dict[str, list[str]]
               ) -> dict[str, int]:
    dim = search["dimensions"].get(dim_id)
    if dim is None:
        raise MetaError(f"malformed response: no {dim_id!r} dimension")
    values = dictionaries.get(dim["dictionary_id"])
    if values is None:
        raise MetaError(f"malformed response: no dictionary "
                        f"{dim['dictionary_id']!r} for dimension {dim_id!r}")
    return shares(dim, values, search["total"])


def _fetch_snapshot_counts(version: str, overview: str, snap_type: str,
                           timemachine: str | None, get, timeout: float
                           ) -> tuple[dict, dict[str, int], dict[str, int]]:
    """Fetch one search snapshot + the dictionaries for class/skills.

    Returns (parsed_search, class_counts_by_name, skill_counts_by_name).
    """
    url = SEARCH_URL.format(version=version, overview=overview,
                            type=snap_type)
    if timemachine:
        url += f"&timemachine={timemachine}"
    search = parse_search(get(url, timeout))
    dictionaries: dict[str, list[str]] = {}
    needed = set()
    for dim_id in ("class", "skills"):
        dim = search["dimensions"].get(dim_id)
        if dim is None:
            raise MetaError(f"malformed response: no {dim_id!r} dimension")
        needed.add(dim["dictionary_id"])
    for dict_id in sorted(needed):
        h = search["dictionary_hashes"].get(dict_id)
        if not h:
            raise MetaError(f"malformed response: no hash for dictionary "
                            f"{dict_id!r}")
        d = parse_dictionary(get(DICTIONARY_URL.format(hash=h), timeout))
        dictionaries[dict_id] = d["values"]
    return (search,
            _dim_names(search, "class", dictionaries),
            _dim_names(search, "skills", dictionaries))


def fetch_meta(league: str | None = None, top: int = 25,
               want_delta: bool = True, get=None, timeout: float = 30.0
               ) -> dict:
    """Full pipeline. `get` is injectable for tests (defaults to _get)."""
    get = get or _get
    try:
        index_state = json.loads(get(INDEX_STATE_URL, timeout))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise MetaError(f"malformed index-state JSON: {e}") from e
    if not isinstance(index_state, dict):
        raise MetaError("malformed index-state: not a JSON object")
    snap = pick_snapshot(index_state, league)
    version = str(snap.get("version", ""))
    overview = str(snap.get("snapshotName", ""))
    if not version or not overview:
        raise MetaError("malformed index-state: snapshot missing "
                        "version/snapshotName")

    search, class_counts, skill_counts = _fetch_snapshot_counts(
        version, overview, "exp", None, get, timeout)

    prev_class = prev_skill = None
    prev_total = 0
    has_week = WEEK_LABEL in (snap.get("timeMachineLabels") or [])
    if want_delta and has_week:
        prev_search, prev_class, prev_skill = _fetch_snapshot_counts(
            version, overview, "exp", WEEK_LABEL, get, timeout)
        prev_total = prev_search["total"]

    return {
        "league": snap.get("name"),
        "slug": snap.get("url"),
        "version": version,
        "total": search["total"],
        "week1_total": prev_total if prev_class is not None else None,
        "ascendancies": rank(class_counts, search["total"], top,
                             prev_class, prev_total),
        "skills": rank(skill_counts, search["total"], top,
                       prev_skill, prev_total),
    }


# --------------------------------------------------------------- formatting

def _fmt_delta(row: dict) -> str:
    if "delta_pp" not in row:
        return ""
    d = row["delta_pp"]
    if d is None:
        return "    new"
    return f"{d:+7.2f}"


def format_table(title: str, rows: list[dict], with_delta: bool) -> str:
    namew = max([len(r["name"]) for r in rows] + [len(title) - 4]) + 1
    head = f"{title:<{namew + 4}} {'count':>7} {'share':>7}"
    if with_delta:
        head += f" {'Δpp/wk':>7}"
    lines = [head, "-" * len(head)]
    for i, r in enumerate(rows, 1):
        line = f"{i:>2}. {r['name']:<{namew}} {r['count']:>7,}" \
               f" {r['pct']:>6.2f}%"
        if with_delta:
            line += f" {_fmt_delta(r)}"
        lines.append(line)
    return "\n".join(lines)


def format_report(meta: dict) -> str:
    with_delta = meta["week1_total"] is not None
    out = [f"poe.ninja build meta — {meta['league']} "
           f"({meta['total']:,} ladder characters, "
           f"snapshot {meta['version']})"]
    if with_delta:
        out.append(f"Δpp/wk = share change in percentage points vs the "
                   f"'{WEEK_LABEL}' snapshot ({meta['week1_total']:,} chars); "
                   f"'new' = not present then.")
    else:
        out.append("week-over-week deltas: not available for this league.")
    out.append("")
    out.append(format_table("Top ascendancies", meta["ascendancies"],
                            with_delta))
    out.append("")
    out.append(format_table("Top main skills", meta["skills"], with_delta))
    return "\n".join(out)


# ---------------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    # Windows: redirected/piped stdout defaults to the ANSI codepage, which
    # cannot encode the report's 'Δ'; force UTF-8 (replace on odd consoles).
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass
    p = argparse.ArgumentParser(
        description="Rank the current poe.ninja build meta: top ascendancies "
                    "and main skills by ladder share.")
    p.add_argument("--league", default=None,
                   help="league name or poe.ninja slug (default: the current "
                        "challenge league)")
    p.add_argument("--top", type=int, default=25,
                   help="rows per table (default 25)")
    p.add_argument("--no-delta", action="store_true",
                   help="skip the week-over-week delta fetch (fewer requests)")
    p.add_argument("--timeout", type=float, default=30.0,
                   help="per-request timeout in seconds")
    args = p.parse_args(argv)
    if args.top < 1:
        p.error("--top must be >= 1")
    try:
        meta = fetch_meta(league=args.league, top=args.top,
                          want_delta=not args.no_delta, timeout=args.timeout)
    except MetaError as e:
        print(f"meta: could not fetch poe.ninja build data: {e}",
              file=sys.stderr)
        return 1
    print(format_report(meta))
    return 0


if __name__ == "__main__":
    sys.exit(main())
