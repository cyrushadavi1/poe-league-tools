"""Headless tests for market/scanner.py: cycles, spreads, fixer filter,
ranking math, dedupe, leg caps.  Offline, stdlib only, no Qt."""
import copy
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "market")]

from scanner import (                       # noqa: E402
    DEFAULT_PARAMS, canonical_cycle, cycle_key, filter_listings,
    pair_rows_from_currency, scan,
)

H = 0.04
P = {"haircut": H, "min_margin_pct": 5.0, "min_vol": 20.0,
     "bankroll_c": 2000.0}
TS = "2026-07-07T12:00:00"

OPP_KEYS = {"id", "kind", "path", "margin_pct", "est_profit_c",
            "est_profit_per_hour", "liq_score", "confidence", "flags",
            "actions"}


def pair(u, v, rate, vol=100.0, source="bulk_trade", ts=TS, raw=None):
    return {"ts": ts, "source": source, "league": "Mirage",
            "item": f"{u}->{v}", "buy": None, "sell": rate,
            "buy_vol": None, "sell_vol": vol,
            "raw": json.dumps(raw) if raw is not None else None}


def item_row(item, buy=None, sell=None, buy_vol=None, sell_vol=None,
             source="trade", ts=TS, raw=None):
    return {"ts": ts, "source": source, "league": "Mirage", "item": item,
            "buy": buy, "sell": sell, "buy_vol": buy_vol,
            "sell_vol": sell_vol,
            "raw": json.dumps(raw) if raw is not None else None}


def cycle_rows(nodes, margin, vols=None, source="bulk_trade"):
    """Plant a cycle whose effective margin (after per-leg haircut) is
    exactly `margin`: last leg's raw rate carries the whole product."""
    n = len(nodes)
    prod_raw = (1.0 + margin) / (1.0 - H) ** n
    vols = vols or [100.0] * n
    rows = []
    for i in range(n):
        rate = prod_raw if i == n - 1 else 1.0
        rows.append(pair(nodes[i], nodes[(i + 1) % n], rate, vols[i],
                         source=source))
    return rows


# ------------------------------------------------ planted 3-cycle at +8 %
rows8 = cycle_rows(["chaos", "divine", "exalt"], 0.08)
res = scan(rows8, P)
assert len(res) == 1, f"expected exactly 1 opportunity, got {len(res)}"
opp = res[0]
assert set(opp.keys()) == OPP_KEYS, f"contract keys mismatch: {opp.keys()}"
assert opp["kind"] == "cycle"
assert abs(opp["margin_pct"] - 8.0) <= 0.1, opp["margin_pct"]
# canonical rotation starts at the lexicographically smallest node
assert opp["path"] == ["chaos->divine", "divine->exalt", "exalt->chaos"]
assert opp["confidence"] == "high" and opp["flags"] == []

# ranking math, spot-checked exactly:
#   size = min(100 * 0.25, 2000 * 0.2) = 25;  profit = 0.08 * 25 = 2.0
#   3 trade legs * 2 min = 6 min = 0.1 h  ->  20.0 / h
#   liq_score = min(1, 100 / (5 * 20)) = 1.0
assert abs(opp["est_profit_c"] - 2.0) < 1e-6, opp["est_profit_c"]
assert abs(opp["est_profit_per_hour"] - 20.0) < 1e-6
assert opp["liq_score"] == 1.0
assert all(a["type"] == "whisper" for a in opp["actions"])
assert len(opp["actions"]) == 3

# stable id: same kind+path -> same id across runs
assert scan(rows8, P)[0]["id"] == opp["id"]
assert len(opp["id"]) == 16

# input rows are not mutated
snapshot = copy.deepcopy(rows8)
scan(rows8, P)
assert rows8 == snapshot

# ------------------------------------------------ +2 % cycle: below threshold
rows2 = cycle_rows(["dawn", "eve", "fuse"], 0.02)
assert scan(rows2, P) == [], "+2% cycle must be ignored at min_margin 5%"
# ...but the detector itself sees it once the threshold allows
low = scan(rows2, {**P, "min_margin_pct": 1.0})
assert len(low) == 1 and abs(low[0]["margin_pct"] - 2.0) <= 0.1

# ------------------------------------------------ low-liquidity cycle excluded
rows_thin = cycle_rows(["gcp", "harb", "ivory"], 0.08, vols=[100.0, 5.0, 100.0])
assert scan(rows_thin, P) == [], "bottleneck 5 < min_vol 20 must exclude"
ok = scan(rows_thin, {**P, "min_vol": 5.0})
assert len(ok) == 1 and abs(ok[0]["margin_pct"] - 8.0) <= 0.1
# bottleneck (5) drives sizing: size = min(5*0.25, 400) = 1.25
assert abs(ok[0]["est_profit_c"] - 0.1) < 1e-6

# ------------------------------------------------ 2-leg cycle works too
rows_two = cycle_rows(["chaos", "fusing"], 0.06)
two = scan(rows_two, P)
assert len(two) == 1 and len(two[0]["path"]) == 2
assert abs(two[0]["margin_pct"] - 6.0) <= 0.1

# ------------------------------------------------ rotation + reversal dedupe
assert canonical_cycle(["divine", "exalt", "chaos"]) == \
    ("chaos", "divine", "exalt")
assert canonical_cycle(["exalt", "chaos", "divine"]) == \
    ("chaos", "divine", "exalt")
assert cycle_key(["divine", "exalt", "chaos"]) == \
    cycle_key(["chaos", "divine", "exalt"])
# 2-cycles collapse with their reversal (identical directed edge set)...
assert cycle_key(["chaos", "fusing"]) == cycle_key(["fusing", "chaos"])
# ...but a 3+-cycle's reversal uses entirely different directed edges —
# a distinct executable trade — and keeps its own key
assert cycle_key(["chaos", "divine", "exalt"]) != \
    cycle_key(["chaos", "exalt", "divine"])
# same cycle with rows listed in a different order -> one opportunity, same id
shuffled = [rows8[2], rows8[0], rows8[1]]
res_shuf = scan(shuffled, P)
assert len(res_shuf) == 1 and res_shuf[0]["id"] == opp["id"]

# ------------------------------------------------ two cycles, one component
rows_two_tri = (cycle_rows(["chaos", "divine", "exalt"], 0.08) +
                cycle_rows(["chaos", "annul", "regal"], 0.10))
both = scan(rows_two_tri, P)
assert len(both) == 2, f"both triangles through 'chaos' found, got {len(both)}"
margins = sorted(o["margin_pct"] for o in both)
assert abs(margins[0] - 8.0) <= 0.1 and abs(margins[1] - 10.0) <= 0.1
# sorted by est_profit_per_hour descending -> the +10% triangle first
assert both[0]["margin_pct"] > both[1]["margin_pct"]

# ------------------------------------------------ 4-leg cap
rows4 = cycle_rows(["w1", "w2", "w3", "w4"], 0.08)
rows5 = cycle_rows(["v1", "v2", "v3", "v4", "v5"], 0.50)  # huge, but 5 legs
capped = scan(rows4 + rows5, P)
assert len(capped) == 1, "only the 4-leg cycle may pass the cap"
assert len(capped[0]["path"]) == 4
assert abs(capped[0]["margin_pct"] - 8.0) <= 0.1
assert not any("v1" in leg for leg in capped[0]["path"])
# the cap is a parameter: raising it admits the 5-leg cycle
uncapped = scan(rows5, {**P, "max_legs": 5})
assert len(uncapped) == 1 and len(uncapped[0]["path"]) == 5

# ------------------------------------------------ exchange legs: 0.5 min each
rows_ex = cycle_rows(["c2", "d2", "e2"], 0.08, source="currency_exchange")
ex = scan(rows_ex, P)[0]
# 3 exchange legs * 0.5 min = 1.5 min -> 2.0 profit / 0.025 h = 80 / h
assert abs(ex["est_profit_per_hour"] - 80.0) < 1e-6
assert all(a["type"] == "exchange" for a in ex["actions"])

# ------------------------------------------------ whisper passthrough
w_rows = cycle_rows(["chaos", "divine", "exalt"], 0.08)
w_rows[0]["raw"] = json.dumps({"whisper": "Hi, I'd like to buy your Divine"})
w = scan(w_rows, P)[0]
assert w["actions"][0] == {"type": "whisper",
                           "text": "Hi, I'd like to buy your Divine"}

# ------------------------------------------------ stale rows ignored
stale = pair("chaos", "divine", 50.0, ts="2026-07-07T09:00:00")  # old & wild
fresh = scan(rows8 + [stale], P)
assert len(fresh) == 1 and abs(fresh[0]["margin_pct"] - 8.0) <= 0.1, \
    "latest (source,item) row must win over a stale one"

# ================================================= filter_listings (fixer)
# two planted low-balls dropped, re-quoted at the honest price
prices = [50, 55, 100, 101, 102, 103, 104, 105, 106, 107]
clean, conf, flags = filter_listings(prices)
assert clean == 100, f"re-quote at honest cheapest, got {clean}"
assert conf == "high"
assert "lowballs_dropped" in flags and "price_fixing_suspect" not in flags

# < 6 in-band listings -> confidence low + price_fixing_suspect
clean, conf, flags = filter_listings([50, 100, 101, 102, 103])
assert clean == 100 and conf == "low" and "price_fixing_suspect" in flags

# clean book: nothing dropped, no flags
clean, conf, flags = filter_listings([100.0] * 8)
assert (clean, conf, flags) == (100.0, "high", [])

# boundary: exactly 25 % below the median is NOT a lowball
clean, conf, flags = filter_listings([75.0] + [100.0] * 9)
assert clean == 75.0 and conf == "high" and flags == []

# only the cheapest n=20 listings are considered
clean, conf, flags = filter_listings(list(range(100, 120)) + [10000] * 5)
assert clean == 100 and conf == "high"

# at most k are dropped, even if more sit below the band; the post-drop
# quote still sits below the band -> degraded, never promoted as clean
clean, conf, flags = filter_listings([10, 11, 12, 13] + [100] * 10, k=3)
assert clean == 13 and "lowballs_dropped" in flags
assert conf == "low" and "price_fixing_suspect" in flags, \
    "a bait price left after the k-drop must not get high confidence"

# regression: >k lowballs (a real fixer posts many) never yields a HIGH-
# confidence quote at the fixer's price
clean, conf, flags = filter_listings([1, 1, 1, 1] + [10] * 15)
assert clean == 1 and conf == "low" and "price_fixing_suspect" in flags

# no usable prices
assert filter_listings([]) == (None, "low", ["no_listings"])
assert filter_listings([0, -5]) == (None, "low", ["no_listings"])

# ================================================= spread detector
spread_rows = [
    item_row("Orb of Unmaking", buy=10.0, buy_vol=200.0, source="exchange"),
    item_row("Orb of Unmaking", sell=11.5, sell_vol=60.0, source="trade"),
]
sp = scan(spread_rows, P)
assert len(sp) == 1 and sp[0]["kind"] == "spread"
exp_margin = (11.5 / 10.0) * (1 - H) ** 2 - 1          # = 5.984 %
assert abs(sp[0]["margin_pct"] - exp_margin * 100) <= 0.1
assert sp[0]["path"] == ["chaos->Orb of Unmaking @ exchange",
                         "Orb of Unmaking->chaos @ trade"]
# buy leg on the exchange -> exchange action; sell leg -> whisper draft
assert sp[0]["actions"][0]["type"] == "exchange"
assert sp[0]["actions"][1]["type"] == "whisper"
# ranking (volumes are listing counts, normalized to chaos depth):
#   bottleneck = min(200 * 10, 60 * 11.5) = 690 chaos
#   size = min(690 * 0.25, 400) = 172.5;  minutes = 0.5 + 2.0 = 2.5
exp_profit = exp_margin * 172.5
assert abs(sp[0]["est_profit_c"] - round(exp_profit, 2)) < 1e-9
assert abs(sp[0]["est_profit_per_hour"] - round(exp_profit * 24, 2)) < 1e-9
assert sp[0]["liq_score"] == round(min(1.0, 690 / 100.0), 3)

# too-thin spread is ignored
thin = [
    item_row("Orb of Unmaking", buy=10.0, buy_vol=200.0, source="exchange"),
    item_row("Orb of Unmaking", sell=10.5, sell_vol=60.0, source="trade"),
]
assert scan(thin, P) == []

# same-source buy/sell never flags a *spread*; since the implied-pair glue
# landed, a same-source crossed book (sell > buy on one venue) surfaces as
# a 2-leg cycle instead (the spread detector deliberately skips same-source
# pairs, so this is new signal, not a duplicate).
same_src = [item_row("Vaal Orb", buy=10.0, sell=12.0, buy_vol=99.0,
                     sell_vol=99.0, source="trade")]
ss = scan(same_src, P)
assert len(ss) == 1 and ss[0]["kind"] == "cycle"
exp_margin = (12.0 / 10.0) * (1 - H) ** 2 - 1          # = 10.592 %
assert abs(ss[0]["margin_pct"] - exp_margin * 100) <= 0.1
assert sorted(ss[0]["path"]) == ["Vaal Orb->chaos", "chaos->Vaal Orb"]
assert scan([item_row("Vaal Orb", buy=10.0, sell=10.2, buy_vol=99.0,
                      sell_vol=99.0, source="trade")], P) == [], \
    "an ordinary (uncrossed-after-haircut) book is not an opportunity"

# ------------------------------------------------ fixer filter inside scan
poisoned = [
    item_row("Chayula Breachstone", buy=50.0, buy_vol=40.0, source="trade",
             raw={"listings": [50, 55, 100, 101, 102, 103, 104, 105,
                               106, 107]}),
    item_row("Chayula Breachstone", sell=120.0, sell_vol=100.0,
             source="faustus_exchange"),
]
fx = scan(poisoned, P)
assert len(fx) == 1
# the honest quote is 100 (the 50/55 lowballs are dropped), not the naive 50
exp_margin = (120.0 / 100.0) * (1 - H) ** 2 - 1        # = 10.592 %
assert abs(fx[0]["margin_pct"] - exp_margin * 100) <= 0.1, fx[0]["margin_pct"]
assert fx[0]["margin_pct"] < 20, "naive lowball quote would show ~121%"
assert fx[0]["confidence"] == "high"
# minutes = 2.0 (trade buy) + 0.5 (exchange sell); chaos depth =
# min(40*100, 100*120) = 4000 -> size = min(1000, 400) = 400 (bankroll cap)
exp_pph = (exp_margin * 400.0) / (2.5 / 60.0)
assert abs(fx[0]["est_profit_per_hour"] - round(exp_pph, 2)) < 1e-9

# sparse book -> low confidence + flag propagate to the opportunity
sparse = [
    item_row("Chayula Breachstone", buy=50.0, buy_vol=40.0, source="trade",
             raw={"listings": [50, 100, 101, 102, 103]}),
    item_row("Chayula Breachstone", sell=120.0, sell_vol=100.0,
             source="faustus_exchange"),
]
fs = scan(sparse, P)
assert len(fs) == 1 and fs[0]["confidence"] == "low"
assert "price_fixing_suspect" in fs[0]["flags"]

# fixer filter also applies to pair rows carrying listings
# (listing price = u per v; honest 100 -> rate 0.01)
pf = [pair("chaos", "mirror", 1.0 / 50.0,   # naive rate from the lowball
           raw={"listings": [50, 55, 100, 101, 102, 103, 104, 105, 106,
                             107]}),
      pair("mirror", "chaos", 115.0)]
pfr = scan(pf, P)
exp_margin = (1.0 / 100.0) * 115.0 * (1 - H) ** 2 - 1  # = 5.984 %
assert len(pfr) == 1 and abs(pfr[0]["margin_pct"] - exp_margin * 100) <= 0.1

# ================================================= implied-pair glue
# (single-item currency rows -> directed chaos pairs; the Divine 500/422
# example is straight from the market/sources.py docstring)
divine = item_row("Divine Orb", buy=500.0, sell=422.0, buy_vol=900.0,
                  sell_vol=850.0, source="poe.ninja")
pr = pair_rows_from_currency([divine])
assert len(pr) == 2
by_pair = {p["item"]: p for p in pr}
buy_edge = by_pair["chaos->Divine Orb"]
sell_edge = by_pair["Divine Orb->chaos"]
assert abs(buy_edge["sell"] - 1.0 / 500.0) < 1e-15, \
    "chaos->divine rate is units per chaos (1/buy)"
assert buy_edge["sell_vol"] == 900.0 * 500.0, \
    "buy-side volume normalized to chaos depth (900 listings x 500c)"
assert sell_edge["sell"] == 422.0, "divine->chaos rate is the sell quote"
assert sell_edge["sell_vol"] == 850.0 * 422.0, \
    "sell-side volume normalized to chaos depth (850 listings x 422c)"
assert all(p["source"] == "poe.ninja" and p["league"] == "Mirage"
           and p["ts"] == TS for p in pr)
# rows missing a side only emit the other direction
assert [p["item"] for p in
        pair_rows_from_currency([item_row("Exalted Orb", buy=20.0)])] == \
    ["chaos->Exalted Orb"]
assert [p["item"] for p in
        pair_rows_from_currency([item_row("Exalted Orb", sell=19.0)])] == \
    ["Exalted Orb->chaos"]
# pair rows and arrowless garbage are ignored; input is never mutated
assert pair_rows_from_currency([pair("chaos", "divine", 2.0)]) == []
assert pair_rows_from_currency([item_row("", buy=1.0)]) == []
snap_div = copy.deepcopy(divine)
pair_rows_from_currency([divine])
assert divine == snap_div

# the implied chaos->divine->chaos cycle loses money (buy 500, sell 422,
# two haircuts) and must NOT be an opportunity...
assert scan([divine], P) == []
# ...but a planted mispriced row (sell > buy on one source) IS found
planted = item_row("Mirror Shard", buy=100.0, sell=130.0, buy_vol=50.0,
                   sell_vol=45.0, source="poe.ninja")
found = scan([divine, planted], P)
assert len(found) == 1 and found[0]["kind"] == "cycle"
exp_margin = (130.0 / 100.0) * (1 - H) ** 2 - 1        # = 19.808 %
assert abs(found[0]["margin_pct"] - exp_margin * 100) <= 0.1
assert sorted(found[0]["path"]) == ["Mirror Shard->chaos",
                                    "chaos->Mirror Shard"]
# ranking on the implied cycle (chaos-normalized depth):
#   bottleneck = min(50*100, 45*130) = 5000 chaos ->
#   size = min(5000*0.25, 400) = 400 (bankroll cap); two 2-min legs = 4 min
exp_profit = exp_margin * 400.0
assert abs(found[0]["est_profit_c"] - round(exp_profit, 2)) < 1e-9
assert abs(found[0]["est_profit_per_hour"]
           - round(exp_profit / (4.0 / 60.0), 2)) < 1e-9
# the liquidity gate is chaos-denominated: 5 listings at 100c ~ 500c depth
# passes the default 20c gate but fails a 1000c one
thin_row = item_row("Mirror Shard", buy=100.0, sell=130.0, buy_vol=5.0,
                    sell_vol=5.0, source="poe.ninja")
assert len(scan([thin_row], P)) == 1
assert scan([thin_row], {**P, "min_vol": 1000.0}) == []

# -------------------------------------- masked-cycle regression (branching)
# A crossed cross-source book is reported as a spread and its derived
# 2-cycle is suppressed — but extracting that unreportable 2-cycle must not
# consume the chaos->B edge that a distinct, reportable 3-cycle needs
# (the old greedy worst-edge removal silently lost the 3-cycle).
mask_rows = [
    item_row("B", buy=100.0, buy_vol=100.0, source="src1"),
    item_row("B", sell=120.0, sell_vol=100.0, source="src2"),
    pair("B", "A", 0.5),
    pair("A", "chaos", 250.0),
]
mk = scan(mask_rows, P)
assert sorted(o["kind"] for o in mk) == ["cycle", "spread"], \
    f"expected the spread AND the 3-cycle, got {[o['kind'] for o in mk]}"
tri = [o for o in mk if o["kind"] == "cycle"][0]
assert len(tri["path"]) == 3 and "A->chaos" in tri["path"]
exp_margin = (1.0 / 100.0) * 0.5 * 250.0 * (1 - H) ** 3 - 1   # = 10.592 %
assert abs(tri["margin_pct"] - exp_margin * 100) <= 0.1

# ---------------------------------- forward + reverse 3-cycles both reported
# Six directed edges over {a1,b1,c1}, profitable in BOTH directions (6.12%
# each) while every 2-cycle stays below min_margin (4.04%): the two
# directions are edge-disjoint executable trades and must both surface.
bi_rows = [pair(u, v, 1.0625) for u, v in
           [("a1", "b1"), ("b1", "c1"), ("c1", "a1"),
            ("b1", "a1"), ("c1", "b1"), ("a1", "c1")]]
bi = scan(bi_rows, P)
assert len(bi) == 2, f"forward and reverse 3-cycles, got {len(bi)}"
assert all(len(o["path"]) == 3 for o in bi)
assert {tuple(sorted(o["path"])) for o in bi} == {
    ("a1->b1", "b1->c1", "c1->a1"), ("a1->c1", "b1->a1", "c1->b1")}

# ------------------------------------------------ mixed scan: sorted output
mixed = rows8 + spread_rows
mres = scan(mixed, P)
assert [o["kind"] for o in mres] == \
    sorted((o["kind"] for o in mres),
           key=lambda k: -[m["est_profit_per_hour"]
                           for m in mres if m["kind"] == k][0])
assert all(set(o.keys()) == OPP_KEYS for o in mres)
pph = [o["est_profit_per_hour"] for o in mres]
assert pph == sorted(pph, reverse=True)

# league filter drops foreign rows
foreign = copy.deepcopy(rows8)
for r in foreign:
    r["league"] = "Standard"
assert scan(foreign, {**P, "league": "Mirage"}) == []
assert len(scan(rows8, {**P, "league": "Mirage"})) == 1

# defaults exist and match the addendum starting parameters
assert DEFAULT_PARAMS["haircut"] == 0.04
assert DEFAULT_PARAMS["min_margin_pct"] == 5.0
assert DEFAULT_PARAMS["min_vol"] == 20.0
assert DEFAULT_PARAMS["bankroll_c"] == 2000.0
assert DEFAULT_PARAMS["leg_minutes"] == 2.0
assert DEFAULT_PARAMS["leg_minutes_exchange"] == 0.5
assert DEFAULT_PARAMS["max_legs"] == 4

print("ALL TESTS PASSED")
print(f"  planted +8% 3-cycle: margin {opp['margin_pct']}%, "
      f"{opp['est_profit_per_hour']}/h, id {opp['id']}")
print(f"  spread example: {sp[0]['path'][0]} -> {sp[0]['margin_pct']}%")
