"""tradeq — natural language -> official Path of Exile trade-site query.

    python tools/tradeq.py "boots 30 movespeed, life, cold res, max 5c"

Pipeline:
  1. Load the official trade API stat catalog from data/trade_stats.json.
     That file is a TRIMMED snapshot of the live catalog: the full response
     from https://www.pathofexile.com/api/trade/data/stats is ~1.9 MB with
     ~15k entries across 13 groups; we keep only the ~44 most common
     leveling-relevant stats with their exact official ids (life/mana/ES,
     resists incl. pseudo totals, movement/attack/cast speed, added damage,
     attributes, accuracy, regen/leech, minion and gem levels).
     Endpoint path + entry shape ({"id","text","type"}) live-verified
     2026-07-07 with the mandatory User-Agent.
  2. LLM (standard tier, json_schema-constrained) emits official trade-API
     search JSON; the code then validates every stat id against the catalog
     and reprompts ONCE with the offending ids before giving up.
  3. Print the validated JSON plus the trade-site URL for the league and
     paste instructions. Optionally (--post) POST the search — a read-only
     operation — and print the result count and a direct results URL.
     Any error on the POST path degrades back to print-only.

ToS: this tool only builds and (optionally) POSTs a *search*. It never
sends whispers or messages, never opens trades, never touches the game
client — the human does everything after the search is printed.

Degrade (per INTERFACES.md invariant 4): when the LLM is unavailable
(LLMDisabled, missing llm/client.py, or LLMError) the tool prints a
manual-query template plus the stat catalog so the query can be
hand-assembled on the trade site.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG_PATH = os.path.join(ROOT, "data", "trade_stats.json")
MARKET_CONFIG_PATH = os.path.join(ROOT, "market", "config.json")

USER_AGENT = "poe-league-tools/1.0 (contact: cyrus@hadavi.net)"
TRADE_SITE = "https://www.pathofexile.com/trade/search/{league}"
SEARCH_API = "https://www.pathofexile.com/api/trade/search/{league}"
MIN_REQUEST_INTERVAL_S = 2.0  # hard floor, INTERFACES.md invariant 3

# Structure constraint handed to the LLM (llm.client validates against it);
# stat *ids* are additionally validated in code against the catalog because
# a schema cannot stop a well-formed hallucinated id.
QUERY_SCHEMA = {
    "type": "object",
    "required": ["query", "sort"],
    "properties": {
        "query": {
            "type": "object",
            "required": ["status", "stats"],
            "properties": {
                "status": {
                    "type": "object",
                    "required": ["option"],
                    "properties": {
                        "option": {"type": "string",
                                   "enum": ["online", "onlineleague", "any"]},
                    },
                },
                "name": {"type": "string"},
                "type": {"type": "string"},
                "term": {"type": "string"},
                "stats": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["type", "filters"],
                        "properties": {
                            "type": {"type": "string",
                                     "enum": ["and", "not", "count", "weight"]},
                            "filters": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "required": ["id"],
                                    "properties": {
                                        "id": {"type": "string"},
                                        "value": {
                                            "type": "object",
                                            "properties": {
                                                "min": {"type": "number"},
                                                "max": {"type": "number"},
                                            },
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
                "filters": {"type": "object"},
            },
        },
        "sort": {"type": "object"},
    },
}

SYSTEM_PROMPT = """\
You translate a natural-language Path of Exile item request into the JSON
body of the official trade-site search API (POST /api/trade/search/<league>).

Emit ONLY the JSON object, shaped like:
{
  "query": {
    "status": {"option": "online"},
    "stats": [{"type": "and", "filters": [
        {"id": "<stat id from the catalog below>", "value": {"min": 30}}
    ]}],
    "filters": {
      "type_filters":  {"filters": {"category": {"option": "armour.boots"}}},
      "trade_filters": {"filters": {"price": {"option": "chaos", "max": 5}}}
    }
  },
  "sort": {"price": "asc"}
}

Rules:
- Every stat filter id MUST be copied verbatim from the stat catalog below.
  Never invent ids. If the request names a stat not in the catalog, omit it.
- A stat mentioned without a number gets no "value" (presence-only filter).
- "max Nc" / "under N chaos" -> trade_filters price {"option": "chaos",
  "max": N}; divines use "option": "divine".
- Item kind -> type_filters category option, e.g. armour.boots,
  armour.gloves, armour.helmet, armour.chest, weapon.wand, weapon.onesword,
  weapon.bow, accessory.ring, accessory.amulet, accessory.belt.
  Omit type_filters when no kind is given.
- Prefer pseudo.* ids for resistances/life totals, explicit.* otherwise.
- Default status "online", sort {"price": "asc"}.
"""

MANUAL_TEMPLATE = """\
{
  "query": {
    "status": {"option": "online"},
    "stats": [{"type": "and", "filters": [
        {"id": "<paste a stat id from the catalog below>", "value": {"min": 0}}
    ]}],
    "filters": {
      "trade_filters": {"filters": {"price": {"option": "chaos", "max": 5}}}
    }
  },
  "sort": {"price": "asc"}
}"""


# --------------------------------------------------------------- league

def default_league(config_path: str = MARKET_CONFIG_PATH) -> str:
    """League when --league is not given: POE_LEAGUE env var, then the
    market/config.json 'league' (kept live-correct by the market
    workstream), then 'Standard'."""
    env = os.environ.get("POE_LEAGUE")
    if env:
        return env
    try:
        with open(config_path, encoding="utf-8") as f:
            league = json.load(f).get("league")
        if league and isinstance(league, str):
            return league
    except (OSError, ValueError):
        pass
    return "Standard"


# --------------------------------------------------------------- catalog

def load_catalog(path: str = CATALOG_PATH) -> dict[str, str]:
    """data/trade_stats.json -> {stat_id: human_text} (trimmed catalog)."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    catalog = {e["id"]: e["text"] for e in data["stats"]}
    if not catalog:
        raise ValueError(f"empty stat catalog: {path}")
    return catalog


def validate_query(obj, catalog: dict[str, str]) -> list[str]:
    """Structural + stat-id validation. Returns a list of error strings
    (empty = valid). Every stat id must exist in the trimmed catalog —
    this is what catches hallucinated ids the schema cannot."""
    errors: list[str] = []
    if not isinstance(obj, dict) or not isinstance(obj.get("query"), dict):
        return ["top level must be an object with a 'query' object"]
    q = obj["query"]
    status = q.get("status")
    if status is not None and (
            not isinstance(status, dict)
            or status.get("option") not in ("online", "onlineleague", "any")):
        errors.append("query.status.option must be online|onlineleague|any")
    stats = q.get("stats", [])
    if not isinstance(stats, list):
        return errors + ["query.stats must be a list of stat groups"]
    for gi, group in enumerate(stats):
        if not isinstance(group, dict) or not isinstance(
                group.get("filters"), list):
            errors.append(f"stats[{gi}] must be an object with a filters list")
            continue
        if group.get("type") not in ("and", "not", "count", "weight"):
            errors.append(f"stats[{gi}].type must be and|not|count|weight")
        for fi, filt in enumerate(group["filters"]):
            if not isinstance(filt, dict) or not isinstance(
                    filt.get("id"), str):
                errors.append(f"stats[{gi}].filters[{fi}] needs a string 'id'")
                continue
            if filt["id"] not in catalog:
                errors.append(f"unknown stat id: {filt['id']}")
            value = filt.get("value")
            if value is not None and not isinstance(value, dict):
                errors.append(
                    f"stats[{gi}].filters[{fi}].value must be an object")
    return errors


# --------------------------------------------------------------- LLM step

def build_query(nl_request: str, catalog: dict[str, str], llm) -> dict:
    """NL -> validated trade query via the LLM; one reprompt on bad stat
    ids, then ValueError. `llm` is an llm.client.LLM (or a test double)."""
    system = (SYSTEM_PROMPT + "\nStat catalog (id | text):\n"
              + "\n".join(f"{sid} | {text}"
                          for sid, text in sorted(catalog.items())))
    messages = [{"role": "user", "content": nl_request}]
    obj = llm.complete(system=system, messages=messages, max_tokens=2048,
                       feature="tradeq", json_schema=QUERY_SCHEMA)
    errors = validate_query(obj, catalog)
    if not errors:
        return obj
    messages = messages + [
        {"role": "assistant", "content": json.dumps(obj)},
        {"role": "user", "content":
            "That query was rejected: " + "; ".join(errors)
            + ". Use only stat ids that appear verbatim in the catalog. "
              "Emit the corrected JSON object."},
    ]
    obj = llm.complete(system=system, messages=messages, max_tokens=2048,
                       feature="tradeq", json_schema=QUERY_SCHEMA)
    errors = validate_query(obj, catalog)
    if errors:
        raise ValueError("query still invalid after one reprompt: "
                         + "; ".join(errors))
    return obj


# --------------------------------------------------------------- output

def site_url(league: str) -> str:
    return TRADE_SITE.format(league=urllib.parse.quote(league, safe=""))


def format_output(query_obj: dict, league: str) -> str:
    """Validated JSON + trade-site URL + paste instructions."""
    pretty = json.dumps(query_obj, indent=2, ensure_ascii=False)
    url = site_url(league)
    # VERIFY: the trade site loads a query from the ?q=<urlencoded json>
    # parameter (widely used by community tools); if that ever breaks,
    # use --post or rebuild the filters by hand from the JSON.
    deep_link = url + "?q=" + urllib.parse.quote(
        json.dumps(query_obj, separators=(",", ":")))
    return (
        f"Validated trade query:\n{pretty}\n\n"
        f"Trade site for league '{league}':\n  {url}\n"
        f"Direct link (opens the site with this query pre-filled):\n"
        f"  {deep_link}\n\n"
        "To use: open the direct link in your browser (or open the trade\n"
        "site and rebuild the filters from the JSON above), review the\n"
        "listings, and whisper sellers YOURSELF in game. Re-run with\n"
        "--post to submit the search now and get a result count.\n"
        "This tool never sends whispers or messages on your behalf."
    )


def manual_template(nl_request: str, catalog: dict[str, str],
                    league: str) -> str:
    """Degrade path: a hand-editable query template + the stat catalog."""
    stats = "\n".join(f"  {sid}  ({text})"
                      for sid, text in sorted(catalog.items()))
    return (
        f"LLM unavailable — manual query template for: {nl_request!r}\n\n"
        "Paste/adapt this JSON on the trade site (or into a --post body):\n"
        f"{MANUAL_TEMPLATE}\n\n"
        f"Stat ids you can use (from data/trade_stats.json):\n{stats}\n\n"
        f"Trade site for league '{league}':\n  {site_url(league)}\n"
        "Set each filter by hand in the site UI; whisper sellers yourself."
    )


# --------------------------------------------------------------- --post

_last_request_ts = 0.0             # in-process floor (monotonic)
_urlopen = urllib.request.urlopen  # monkeypatch point for tests

# Cross-invocation rate state: the 2 s floor and any 429/token-bucket
# deadline must survive process exit (each `tradeq ... --post` is a fresh
# process, so a module global alone lets a shell loop POST at spawn speed
# — the exact behavior market/sources.py disabled its trade-POST stub
# to avoid). Wall-clock (time.time) timestamps, JSON, best-effort IO.
STATE_PATH = os.path.join(ROOT, "data", "tradeq_state.json")


def _load_rate_state(path) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_rate_state(state: dict, path) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except OSError:
        pass          # persistence is best-effort; the in-process floor holds


def _retry_after_seconds(value) -> float:
    """Retry-After header -> seconds (numeric or HTTP-date), uncapped."""
    if value is None or str(value).strip() == "":
        return MIN_REQUEST_INTERVAL_S
    try:
        return max(float(value), 0.0)
    except (TypeError, ValueError):
        pass
    try:
        from email.utils import parsedate_to_datetime
        from datetime import datetime, timezone
        dt = parsedate_to_datetime(str(value))
        return max((dt - datetime.now(timezone.utc)).total_seconds(), 0.0)
    except (TypeError, ValueError):
        return MIN_REQUEST_INTERVAL_S


def _bucket_deadline(headers, now: float) -> float:
    """Earliest next-allowed time from X-Rate-Limit-* headers.

    Rules look like "8:10:60" (max hits : window s : penalty s) and state
    like "5:10:0" (hits in window : window s : active penalty s), comma-
    separated per bucket. An active penalty, or a bucket at/over its
    limit, exhausts the budget: back off for the penalty/window length.
    """
    deadline = now
    if headers is None:
        return deadline
    rules = [s.strip() for s in
             (headers.get("X-Rate-Limit-Rules") or "Ip").split(",")
             if s.strip()]
    for name in rules:
        rule = headers.get(f"X-Rate-Limit-{name}")
        state = headers.get(f"X-Rate-Limit-{name}-State")
        if not rule or not state:
            continue
        for r, s in zip(rule.split(","), state.split(",")):
            try:
                max_hits, window, _penalty = (int(x) for x in r.split(":"))
                hits, _, active_penalty = (int(x) for x in s.split(":"))
            except ValueError:
                continue
            if active_penalty > 0:
                deadline = max(deadline, now + active_penalty)
            elif hits >= max_hits:
                deadline = max(deadline, now + window)
    return deadline


def post_search(query_obj: dict, league: str,
                state_path: str | None = None) -> tuple[int, str]:
    """Read-only search POST. Returns (result_count, results_url).
    Raises on any HTTP/parse problem — the caller degrades to print-only.
    Honors the 1-request-per-2s floor and any 429 Retry-After / exhausted
    X-Rate-Limit bucket ACROSS invocations (persisted under data/); a 429
    is reported, never retried automatically."""
    global _last_request_ts
    path = state_path or STATE_PATH
    state = _load_rate_state(path)
    now = time.time()
    until = float(state.get("blocked_until") or 0.0)
    if now < until:
        raise RuntimeError(
            f"trade API rate budget exhausted for another "
            f"{until - now:.0f}s (Retry-After honored across runs) — "
            "not POSTing")
    wait = max(
        MIN_REQUEST_INTERVAL_S - (time.monotonic() - _last_request_ts),
        MIN_REQUEST_INTERVAL_S - (now - float(state.get("last_post_ts")
                                              or 0.0)))
    if wait > 0:
        time.sleep(wait)
    _last_request_ts = time.monotonic()
    state["last_post_ts"] = time.time()
    _save_rate_state(state, path)      # persist the floor before the POST

    api = SEARCH_API.format(league=urllib.parse.quote(league, safe=""))
    req = urllib.request.Request(
        api,
        data=json.dumps(query_obj).encode("utf-8"),
        headers={"User-Agent": USER_AGENT,
                 "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with _urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            deadline = _bucket_deadline(getattr(resp, "headers", None),
                                        time.time())
    except urllib.error.HTTPError as e:
        if e.code == 429:
            retry_after = e.headers.get("Retry-After") if e.headers else None
            state["blocked_until"] = time.time() + _retry_after_seconds(
                retry_after)
            _save_rate_state(state, path)
            raise RuntimeError(
                f"rate limited (429), Retry-After: {retry_after or '?'}s — "
                "not retrying (deadline persisted)") from e
        raise RuntimeError(f"search POST failed: HTTP {e.code}") from e
    if deadline > time.time():         # bucket exhausted: persist the wait
        state["blocked_until"] = deadline
        _save_rate_state(state, path)
    # VERIFY: response shape {"id": "<hash>", "total": N, "result": [...]}
    # per the official trade API; not re-verified live for this build.
    search_id = body["id"]
    total = int(body.get("total", len(body.get("result", []))))
    return total, f"{site_url(league)}/{search_id}"


# --------------------------------------------------------------- CLI

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="tradeq",
        description="Natural language -> official trade-site search query. "
                    "Read-only; every whisper/trade is a human action.")
    parser.add_argument("request", help='e.g. "boots 30 movespeed, life, '
                                        'cold res, max 5c"')
    parser.add_argument("--league", default=None,
                        help="league name (default: $POE_LEAGUE, else the "
                             "market/config.json league, else Standard)")
    parser.add_argument("--post", action="store_true",
                        help="also POST the search (read-only) and print "
                             "the result count + results URL")
    parser.add_argument("--catalog", default=CATALOG_PATH,
                        help="path to the trimmed stat catalog JSON")
    args = parser.parse_args(argv)
    if not args.league:                    # flag > env > market config > Standard
        args.league = default_league()

    try:
        catalog = load_catalog(args.catalog)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as e:
        print(f"cannot load stat catalog {args.catalog}: {e}")
        return 2

    if ROOT not in sys.path:  # so `llm.client` resolves when run from tools/
        sys.path.insert(0, ROOT)
    try:
        from llm.client import LLM, LLMDisabled, LLMError
    except ImportError:
        print("llm/client.py not available.")
        print(manual_template(args.request, catalog, args.league))
        return 0

    try:
        llm = LLM("standard")
        query_obj = build_query(args.request, catalog, llm)
    except LLMDisabled:
        print(manual_template(args.request, catalog, args.league))
        return 0
    except (LLMError, ValueError) as e:
        print(f"LLM query generation failed: {e}")
        print(manual_template(args.request, catalog, args.league))
        return 1

    print(format_output(query_obj, args.league))

    if args.post:
        try:
            total, results_url = post_search(query_obj, args.league)
            print(f"\nSearch posted: {total} result(s)")
            print(f"Results: {results_url}")
        except Exception as e:  # any error -> degrade to print-only
            print(f"\nsearch POST failed ({e}); "
                  "use the printed JSON / direct link instead.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
