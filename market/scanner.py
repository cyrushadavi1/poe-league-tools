"""Deterministic market opportunity scanner (addendum task 24, section 4.3).

Pure stdlib. No network, no LLM, no side effects at import time.

Input:  a list of snapshot rows (dicts matching the `snapshots` table in
docs/INTERFACES.md) plus a params dict (defaults below mirror
market/config.json).  Output: a list of opportunity dicts exactly per the
"Opportunity object" contract in docs/INTERFACES.md.

Snapshot-row conventions understood by this scanner
---------------------------------------------------
Two row shapes share the snapshots schema:

1. **Pair rows** — ``item`` contains ``"->"`` (e.g. ``"chaos->divine"``):
   a directed quote saying 1 unit of ``u`` converts into ``sell`` units of
   ``v``; ``sell_vol`` is the CHAOS-DENOMINATED tradable depth on that
   directed edge (falls back to ``buy_vol``; missing volume counts as 0
   and fails the liquidity gate).  Pair rows feed the cycle detector.

2. **Single-item rows** — ``item`` has no arrow: the item is priced in
   chaos.  ``buy`` = chaos paid per unit when buying (best ask),
   ``sell`` = chaos received per unit when selling, ``buy_vol`` /
   ``sell_vol`` the respective volumes AS PUBLISHED BY THE SOURCE:
   listing counts for currency/stash rows, chaos-denominated volume
   (``volumePrimaryValue`` present in ``raw``) for exchange rows.  The
   scanner normalizes both to chaos depth (counts x unit price) before
   any gating, sizing or ranking (this is exactly what
   market/sources.py currency snapshots emit).  Single-item rows feed
   the cross-source spread detector, AND are converted into implied
   chaos-pair rows by ``pair_rows_from_currency`` so the cycle detector
   sees them too (reconciled with market/sources.py 2026-07-07).  A
   2-leg cycle made purely of such derived edges from two *different*
   sources is suppressed — it is the same trade the spread detector
   already reports; same-source crossed books (sell > buy) do surface
   as 2-leg cycles because the spread detector deliberately skips
   same-source pairs.

``raw`` (JSON text or an already-parsed dict) may carry:

* ``"listings"``: a list of ask prices (units of the paying currency per
  1 unit bought).  When present, the mandatory anti-price-fixing filter
  (``filter_listings``) re-derives the buy quote from these instead of
  trusting the naive ``buy``/``sell`` fields.  On a pair row ``u->v`` a
  listing price is u-per-v, so the effective rate becomes
  ``1 / clean_quote``.
* ``"whisper"``: the whisper template from the trade API fetch response;
  passed through verbatim into the opportunity's action.

Leg kinds: a row whose ``source`` contains ``"exchange"`` produces
exchange legs (default 0.5 min/leg); everything else is a manual
whisper->trade leg (default 2 min/leg).

Ranking (addendum 4.3, all liquidity in chaos): ``size =
min(bottleneck_liquidity * 0.25, bankroll_c * 0.2)``;
``est_profit_c = margin * size``;
``est_profit_per_hour = est_profit_c / (sum(leg_minutes) / 60)``.
``liq_score = min(1, bottleneck / (5 * min_vol))`` (documented scale:
1.0 at 5x the liquidity gate).

ToS: this module only analyses already-fetched data.  The actions it
emits are drafts for a human to copy and send; nothing here touches the
game, the clipboard, or any website.
"""
from __future__ import annotations

import hashlib
import json
import math
from statistics import median as _stat_median

__all__ = [
    "DEFAULT_PARAMS",
    "scan",
    "filter_listings",
    "canonical_cycle",
    "cycle_key",
    "pair_rows_from_currency",
]

CHAOS = "chaos"          # base node implied single-item quotes hang off

_EPS = 1e-12

DEFAULT_PARAMS = {
    "league": None,             # optional: filter rows to this league
    "haircut": 0.04,            # per-leg slippage/staleness haircut
    "min_margin_pct": 5.0,      # keep opportunities with margin >= this
    "min_vol": 20.0,            # bottleneck liquidity gate
    "bankroll_c": 2000.0,       # bankroll in chaos for sizing
    "leg_minutes": 2.0,         # manual whisper->trade leg
    "leg_minutes_exchange": 0.5,  # in-game / bulk-exchange leg
    "max_legs": 4,              # cycle length cap
    # anti-price-fixing filter defaults (addendum 4.3)
    "fixer_n": 20,
    "fixer_k": 3,
    "fixer_x": 0.25,
    "fixer_m": 6,
}


# --------------------------------------------------------------------------
# anti-price-fixing filter (pure, mandatory before ranking listing quotes)
# --------------------------------------------------------------------------

def filter_listings(prices, n=20, k=3, x=0.25, m=6):
    """Filter listing-derived quotes against price-fixing lowballs.

    Take the cheapest ``n`` listings; if the cheapest ``k`` (at most) are
    more than ``x`` (fraction) below the band median, drop them and
    re-quote at the cheapest remaining listing.  Require at least ``m``
    listings inside the band (within +-x of the post-drop median), else
    confidence is "low" and the quote is flagged "price_fixing_suspect".
    If the post-drop quote itself still sits below the band (a fixer
    posted more than ``k`` lowballs), the quote is likewise degraded to
    "low" + "price_fixing_suspect" instead of being promoted as clean.

    Returns ``(clean_quote, confidence, flags)`` where clean_quote is None
    only when there are no usable prices.
    """
    flags = []
    ps = sorted(
        float(p) for p in (prices or [])
        if isinstance(p, (int, float)) and not isinstance(p, bool) and p > 0
    )[:n]
    if not ps:
        return None, "low", ["no_listings"]

    band_median = _stat_median(ps)
    cut = band_median * (1.0 - x)
    dropped = 0
    while dropped < k and ps and ps[0] < cut - _EPS:
        ps.pop(0)
        dropped += 1
    if dropped:
        flags.append("lowballs_dropped")
    if not ps:                       # pathological: everything was a lowball
        return None, "low", flags + ["price_fixing_suspect"]

    med = _stat_median(ps)
    lo, hi = med * (1.0 - x), med * (1.0 + x)
    in_band = sum(1 for p in ps if lo - _EPS <= p <= hi + _EPS)
    clean = ps[0]
    if in_band < m or clean < lo - _EPS:
        # clean < lo: more than k lowballs — the quote is still a bait price
        return clean, "low", flags + ["price_fixing_suspect"]
    return clean, "high", flags


# --------------------------------------------------------------------------
# cycle canonicalisation
# --------------------------------------------------------------------------

def canonical_cycle(nodes):
    """Rotate a node cycle so it starts at the lexicographically smallest
    node, preserving direction.  ``nodes`` lists each node once."""
    nodes = list(nodes)
    i = min(range(len(nodes)), key=lambda j: nodes[j])
    return tuple(nodes[i:] + nodes[:i])


def cycle_key(nodes):
    """Dedupe key: identical for all rotations of a cycle.  A 2-cycle also
    collapses with its reversal (the reversal is the same two directed
    edges); for 3+ legs the reversal trades entirely different directed
    edges — a distinct executable trade — and keeps its own key."""
    if len(nodes) <= 2:
        return min(canonical_cycle(nodes),
                   canonical_cycle(list(reversed(nodes))))
    return canonical_cycle(nodes)


# --------------------------------------------------------------------------
# row plumbing
# --------------------------------------------------------------------------

def _raw_dict(raw):
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            d = json.loads(raw)
        except ValueError:
            return {}
        return d if isinstance(d, dict) else {}
    return {}


def _pos(x):
    """Positive float or None."""
    if isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0:
        return float(x)
    return None


def _chaos_depth(vol, price, raw):
    """Normalize a single-item volume to chaos-denominated depth.

    poe.ninja exchange rows publish ``volumePrimaryValue`` (already
    chaos); currency/stash rows publish listing counts, which are
    multiplied by the unit price in chaos.  Sizing, the min_vol gate and
    liq_score all treat volumes as chaos, so the two source families
    must be normalized before ranking (they were not, which skewed
    est_profit_per_hour toward exchange-endpoint items).
    """
    if vol is None:
        return None
    if isinstance(raw, dict) and "volumePrimaryValue" in raw:
        return float(vol)                # already chaos-denominated
    if price is None or price <= 0:
        return float(vol)                # unpriced row: nothing to scale by
    return float(vol) * float(price)


def _latest(rows):
    """Keep only the latest row per (source, item); ties keep the later
    element of the input list."""
    best = {}
    for r in rows:
        key = (r.get("source"), r.get("item"))
        cur = best.get(key)
        if cur is None or (r.get("ts") or "") >= (cur.get("ts") or ""):
            best[key] = r
    return list(best.values())


def _leg_kind(source, params):
    src = (source or "").lower()
    if "exchange" in src:
        return "exchange", float(params["leg_minutes_exchange"])
    return "trade", float(params["leg_minutes"])


def _apply_listing_filter(raw, params):
    """Returns (clean_quote|None, confidence, flags); ("high", []) when the
    row carries no listings array."""
    listings = raw.get("listings")
    if not isinstance(listings, list):
        return None, "high", []
    return filter_listings(
        listings,
        n=params["fixer_n"], k=params["fixer_k"],
        x=params["fixer_x"], m=params["fixer_m"],
    )


# --------------------------------------------------------------------------
# single-item -> pair-row glue (market/sources.py currency snapshots)
# --------------------------------------------------------------------------

def pair_rows_from_currency(rows):
    """Convert single-item chaos-priced rows into directed pair rows.

    market/sources.py currency snapshots are single-item rows where
    ``buy`` = chaos paid to buy 1 unit and ``sell`` = chaos received
    selling 1 unit (e.g. Divine Orb buy 500 / sell 422).  For each such
    row this emits, in the pair-row convention (rate in ``sell``,
    volume in ``sell_vol``):

    * ``buy > 0``  -> ``"chaos-><item>"`` with rate ``1 / buy``
      (units per chaos spent), volume = ``buy_vol`` normalized to chaos;
    * ``sell > 0`` -> ``"<item>->chaos"`` with rate ``sell``
      (chaos per unit sold), volume = ``sell_vol`` normalized to chaos.

    Volumes are normalized to chaos depth via ``_chaos_depth`` (listing
    counts x unit price; exchange rows are already chaos), matching the
    pair-row convention that ``sell_vol`` is chaos-denominated depth.
    ``raw`` is carried onto the buy-side row only: a ``listings`` array
    there holds chaos-per-unit asks, which is exactly the u-per-v price
    the pair-row fixer filter expects.  Emitted rows are tagged
    ``_derived`` so the cycle detector can drop cross-source 2-cycles
    that merely restate a spread.  Pure function; input rows untouched.
    """
    out = []
    for r in rows or []:
        item = (r.get("item") or "").strip()
        if not item or "->" in item or item == CHAOS:
            continue
        raw = _raw_dict(r.get("raw"))
        common = {"ts": r.get("ts"), "source": r.get("source"),
                  "league": r.get("league"), "_derived": True,
                  "buy": None, "buy_vol": None}
        buy = _pos(r.get("buy"))
        if buy is not None:
            out.append(dict(common, item=f"{CHAOS}->{item}",
                            sell=1.0 / buy,
                            sell_vol=_chaos_depth(_pos(r.get("buy_vol")),
                                                  buy, raw),
                            raw=r.get("raw")))
        sell = _pos(r.get("sell"))
        if sell is not None:
            out.append(dict(common, item=f"{item}->{CHAOS}",
                            sell=sell,
                            sell_vol=_chaos_depth(_pos(r.get("sell_vol")),
                                                  sell, raw),
                            raw=None))
    return out


# --------------------------------------------------------------------------
# (a) cycle arbitrage — Bellman-Ford negative cycles on -ln(rate) weights
# --------------------------------------------------------------------------

def _build_pair_edges(rows, params):
    """Directed edges from pair rows.  Multiple quotes for the same edge
    keep the best (highest) effective rate."""
    haircut = float(params["haircut"])
    edges = {}
    for r in rows:
        item = r.get("item") or ""
        if "->" not in item:
            continue
        u, _, v = item.partition("->")
        u, v = u.strip(), v.strip()
        if not u or not v or u == v:
            continue
        raw = _raw_dict(r.get("raw"))
        clean, conf, flags = _apply_listing_filter(raw, params)
        rate = _pos(r.get("sell"))
        if clean is not None:
            rate = 1.0 / clean       # listing price = u paid per 1 v bought
        if rate is None:
            continue
        eff = rate * (1.0 - haircut)
        if eff <= 0:
            continue
        vol = _pos(r.get("sell_vol"))
        if vol is None:
            vol = _pos(r.get("buy_vol")) or 0.0
        leg, minutes = _leg_kind(r.get("source"), params)
        edge = {
            "u": u, "v": v,
            "rate": rate, "eff": eff, "w": -math.log(eff),
            "vol": vol, "source": r.get("source") or "",
            "leg": leg, "minutes": minutes,
            "confidence": conf, "flags": list(flags),
            "whisper": raw.get("whisper"),
            "derived": bool(r.get("_derived")),
        }
        old = edges.get((u, v))
        if old is None or edge["eff"] > old["eff"]:
            edges[(u, v)] = edge
    return edges


def _components(edges):
    """Weakly-connected components over the directed edge set."""
    adj = {}
    for (u, v) in edges:
        adj.setdefault(u, set()).add(v)
        adj.setdefault(v, set()).add(u)
    seen, comps = set(), []
    for start in sorted(adj):
        if start in seen:
            continue
        comp, stack = set(), [start]
        seen.add(start)
        while stack:
            node = stack.pop()
            comp.add(node)
            for nb in adj[node]:
                if nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        comps.append(comp)
    return comps


def _find_negative_cycle(comp, edges):
    """Bellman-Ford with a virtual source (all distances start at 0) and
    predecessor tracing.  Returns a simple cycle as a node list in forward
    edge order, or None when the component has no negative cycle."""
    edge_list = sorted(k for k in edges if k[0] in comp and k[1] in comp)
    dist = {n: 0.0 for n in comp}
    pred = {n: None for n in comp}
    last = None
    for _ in range(len(comp)):          # V passes; quiet pass => converged
        last = None
        for (u, v) in edge_list:
            w = edges[(u, v)]["w"]
            if dist[u] + w < dist[v] - _EPS:
                dist[v] = dist[u] + w
                pred[v] = u
                last = v
        if last is None:
            return None
    # a relaxation in pass V proves a negative cycle; walk back V steps to
    # land inside it, then trace the predecessor loop.
    y = last
    for _ in range(len(comp)):
        y = pred[y]
        if y is None:
            return None
    seq = [y]
    cur = pred[y]
    for _ in range(len(comp) + 1):
        if cur is None:
            return None
        if cur == y:
            break
        seq.append(cur)
        cur = pred[cur]
    else:
        return None
    seq.reverse()                       # pred points backwards along edges
    return seq


def _detect_cycles(edges, params):
    """Extract distinct positive-margin cycles (<= max_legs) per component.

    Explores a small tree of working graphs: after Bellman-Ford surfaces
    a negative cycle, one branch is queued per cycle edge (that edge
    removed), so an unreportable or overlapping cycle (a suppressed
    derived 2-cycle, an over-cap cycle, a sub-margin one) can never
    consume an edge that a distinct reportable cycle needs — the old
    greedy worst-edge removal silently masked such cycles.  Work is
    bounded per component by a Bellman-Ford run budget; states dedupe on
    their removed-edge set.  Rotations (and 2-cycle reversals) dedupe
    via cycle_key, keeping the higher margin.
    """
    max_legs = int(params["max_legs"])
    found = {}
    for comp in _components(edges):
        comp_edges = {k: e for k, e in edges.items()
                      if k[0] in comp and k[1] in comp}
        budget = max(200, 2 * len(comp_edges))   # BF runs per component
        seen_states = set()
        queue = [frozenset()]
        while queue and budget > 0:
            removed = queue.pop(0)
            if removed in seen_states:
                continue
            seen_states.add(removed)
            work = {k: e for k, e in comp_edges.items() if k not in removed}
            if not work:
                continue
            budget -= 1
            cyc = _find_negative_cycle(comp, work)
            if cyc is None:
                continue
            cyc_edges = []
            for i, u in enumerate(cyc):
                e = work.get((u, cyc[(i + 1) % len(cyc)]))
                if e is None:
                    cyc_edges = None
                    break
                cyc_edges.append(e)
            if cyc_edges is None:
                continue                 # defensive: broken pred chain
            margin = math.exp(-sum(e["w"] for e in cyc_edges)) - 1.0
            if margin > _EPS and len(cyc) <= max_legs:
                key = cycle_key(cyc)
                prev = found.get(key)
                if prev is None or margin > prev[0]:
                    start = cyc.index(canonical_cycle(cyc)[0])
                    nodes = cyc[start:] + cyc[:start]
                    ordered = [
                        work[(nodes[i], nodes[(i + 1) % len(nodes)])]
                        for i in range(len(nodes))
                    ]
                    found[key] = (margin, ordered)
            # branch on every cycle edge so this cycle cannot monopolise
            # edges that other (possibly better) cycles share with it
            for i, u in enumerate(cyc):
                queue.append(removed | {(u, cyc[(i + 1) % len(cyc)])})
    return list(found.values())


def _edge_action(e):
    if e["leg"] == "exchange":
        return {
            "type": "exchange",
            "instruction": "Exchange %s -> %s at %.6g %s/%s (%s)"
                           % (e["u"], e["v"], e["rate"], e["v"], e["u"],
                              e["source"]),
        }
    if e.get("whisper"):
        return {"type": "whisper", "text": e["whisper"]}
    return {
        "type": "whisper",
        "text": "(draft) WTB %s paying %s at ~%.6g %s/%s -- copy the exact "
                "whisper from the trade listing before sending"
                % (e["v"], e["u"], e["rate"], e["v"], e["u"]),
    }


def _cycle_opportunities(edges, params):
    min_margin = float(params["min_margin_pct"]) / 100.0
    min_vol = float(params["min_vol"])
    opps = []
    for margin, cyc_edges in _detect_cycles(edges, params):
        if margin + _EPS < min_margin:
            continue
        # A 2-cycle built purely from derived single-item edges across two
        # sources is the exact trade the spread detector reports — skip it.
        if (len(cyc_edges) == 2
                and all(e.get("derived") for e in cyc_edges)
                and cyc_edges[0]["source"] != cyc_edges[1]["source"]):
            continue
        bottleneck = min(e["vol"] for e in cyc_edges)
        if bottleneck + _EPS < min_vol:
            continue
        path = ["%s->%s" % (e["u"], e["v"]) for e in cyc_edges]
        minutes = sum(e["minutes"] for e in cyc_edges)
        conf = "low" if any(e["confidence"] == "low" for e in cyc_edges) \
            else "high"
        flags = sorted({f for e in cyc_edges for f in e["flags"]})
        actions = [_edge_action(e) for e in cyc_edges]
        opps.append(_make_opportunity(
            "cycle", path, margin, bottleneck, minutes, conf, flags,
            actions, params))
    return opps


# --------------------------------------------------------------------------
# (b) two-hop cross-source spreads on single-item quotes
# --------------------------------------------------------------------------

def _single_quotes(rows, params):
    quotes = []
    for r in rows:
        item = r.get("item") or ""
        if not item or "->" in item:
            continue
        raw = _raw_dict(r.get("raw"))
        clean, conf, flags = _apply_listing_filter(raw, params)
        buy = _pos(r.get("buy"))
        if clean is not None:
            buy = clean              # filtered quote overrides naive buy
        sell = _pos(r.get("sell"))
        quotes.append({
            "item": item,
            "source": r.get("source") or "",
            "buy": buy,
            "sell": sell,
            # volumes normalized to chaos depth for gating and sizing
            "buy_vol": _chaos_depth(_pos(r.get("buy_vol")), buy, raw) or 0.0,
            "sell_vol": _chaos_depth(_pos(r.get("sell_vol")), sell, raw)
                        or 0.0,
            "confidence": conf,
            "flags": list(flags),
            "whisper": raw.get("whisper"),
        })
    return quotes


def _spread_actions(item, b, s, params):
    actions = []
    bkind, _ = _leg_kind(b["source"], params)
    if bkind == "exchange":
        actions.append({
            "type": "exchange",
            "instruction": "Buy %s on %s at ~%.6g chaos each"
                           % (item, b["source"], b["buy"]),
        })
    else:
        actions.append({
            "type": "whisper",
            "text": b["whisper"] or
            "(draft) WTB %s @ ~%.6g chaos each -- copy the exact whisper "
            "from the trade listing before sending" % (item, b["buy"]),
        })
    skind, _ = _leg_kind(s["source"], params)
    if skind == "exchange":
        actions.append({
            "type": "exchange",
            "instruction": "Sell %s on %s at ~%.6g chaos each"
                           % (item, s["source"], s["sell"]),
        })
    else:
        actions.append({
            "type": "whisper",
            "text": s["whisper"] or
            "(draft) WTS %s @ ~%.6g chaos each -- list it and answer "
            "incoming whispers manually" % (item, s["sell"]),
        })
    return actions


def _spread_opportunities(rows, params):
    haircut = float(params["haircut"])
    min_margin = float(params["min_margin_pct"]) / 100.0
    min_vol = float(params["min_vol"])
    by_item = {}
    for q in _single_quotes(rows, params):
        by_item.setdefault(q["item"], []).append(q)

    opps = []
    for item in sorted(by_item):
        quotes = by_item[item]
        for b in quotes:                       # buy at source B ...
            if b["buy"] is None:
                continue
            for s in quotes:                   # ... sell at source A
                if s is b or s["source"] == b["source"] or s["sell"] is None:
                    continue
                margin = (s["sell"] / b["buy"]) * (1.0 - haircut) ** 2 - 1.0
                if margin + _EPS < min_margin:
                    continue
                bottleneck = min(b["buy_vol"], s["sell_vol"])
                if bottleneck + _EPS < min_vol:
                    continue
                _, bmin = _leg_kind(b["source"], params)
                _, smin = _leg_kind(s["source"], params)
                path = [
                    "chaos->%s @ %s" % (item, b["source"]),
                    "%s->chaos @ %s" % (item, s["source"]),
                ]
                conf = "low" if "low" in (b["confidence"], s["confidence"]) \
                    else "high"
                flags = sorted(set(b["flags"]) | set(s["flags"]))
                opps.append(_make_opportunity(
                    "spread", path, margin, bottleneck, bmin + smin, conf,
                    flags, _spread_actions(item, b, s, params), params))
    return opps


# --------------------------------------------------------------------------
# ranking + output contract
# --------------------------------------------------------------------------

def _make_opportunity(kind, path, margin, bottleneck, minutes, confidence,
                      flags, actions, params):
    size = min(bottleneck * 0.25, float(params["bankroll_c"]) * 0.2)
    profit = margin * size
    hours = minutes / 60.0
    per_hour = profit / hours if hours > 0 else 0.0
    min_vol = float(params["min_vol"])
    liq = 1.0 if min_vol <= 0 else min(1.0, bottleneck / (5.0 * min_vol))
    opp_id = hashlib.sha1(
        ("%s:%s" % (kind, "|".join(path))).encode("utf-8")).hexdigest()[:16]
    return {
        "id": opp_id,
        "kind": kind,
        "path": list(path),
        "margin_pct": round(margin * 100.0, 4),
        "est_profit_c": round(profit, 2),
        "est_profit_per_hour": round(per_hour, 2),
        "liq_score": round(liq, 3),
        "confidence": confidence,
        "flags": list(flags),
        "actions": actions,
    }


def scan(rows, params=None):
    """Run both detectors over a snapshot row set.

    Accepts BOTH row conventions: pair rows feed the cycle detector
    directly, single-item rows feed the spread detector and are also
    converted into implied chaos pairs (pair_rows_from_currency) before
    graph building, so mispriced single-item quotes surface as cycles.
    Returns opportunity dicts (see module docstring / INTERFACES.md),
    sorted by est_profit_per_hour descending (id breaks ties, so output
    order is deterministic).  Never mutates the input rows.
    """
    p = dict(DEFAULT_PARAMS)
    if params:
        p.update(params)
    rows = [dict(r) for r in (rows or [])]
    if p.get("league"):
        rows = [r for r in rows
                if not r.get("league") or r.get("league") == p["league"]]
    rows = _latest(rows)
    graph_rows = rows + pair_rows_from_currency(rows)
    opps = _cycle_opportunities(_build_pair_edges(graph_rows, p), p)
    opps += _spread_opportunities(rows, p)
    opps.sort(key=lambda o: (-o["est_profit_per_hour"], o["id"]))
    return opps
