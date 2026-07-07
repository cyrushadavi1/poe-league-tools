# Plan Addendum — LLM Integrations & Market Intelligence

**Extends:** IMPLEMENTATION_PLAN.md (same invariants, VERIFY protocol, DECISIONS.md convention, dependency budget rules). Task numbering continues from 18.
**Priority tiers:** P0 = before Jul 24 launch · P1 = launch week (Jul 24–31) · P2 = after.

---

## 1. Where LLMs belong in this system (design principle)

Use LLMs at the **language/judgment seams**; keep deterministic code in every hot loop.

| Good fit (build these) | Wrong tool (never) |
|---|---|
| Patch prose → structured deltas | Ratio arithmetic, margin math |
| "Is this rare an upgrade for *this* build?" | Polling schedules, rate limiting |
| Route JSON vs. wiki text discrepancy finding | Zone-event matching |
| NL → trade-query DSL (schema-constrained) | Anything on the 300 ms log-poll path |
| Market anomaly *explanation*, demand forecasting from patch notes | Opportunity *detection* (that's graph math) |
| Post-run retros, strategy briefs | Trade execution decisions in real time |

## 2. Shared LLM infrastructure — `llm/client.py` (task 19, P0, S)

One wrapper used by advisor, market, and overlay features. Spec:

- `LLM(tier)` where tier ∈ `{fast, standard, deep}` mapping to model strings in `llm/config.json` (defaults: fast = Haiku-class, standard = Sonnet-class, deep = Opus-class; `VERIFY:` current model strings via https://docs.claude.com/en/docs_site_map.md before pinning).
- `complete(system, messages, max_tokens, json_schema=None)` — if `json_schema` given, validate (reuse `advisor/validate.py` machinery generalized), one reprompt-with-error retry, then raise.
- Retries: 3, exponential backoff, only on transient errors.
- **Usage meter:** append `{ts, feature, tier, in_tokens, out_tokens}` to `llm_usage.jsonl`; `tools/llm_report.py` prints spend by feature.
- **Kill switch:** env `POE_TOOLS_LLM=off` → every caller must degrade gracefully (documented per feature below).
- Prompts live as constants in each feature's `prompts.py`; every prompt has a `--dry-run`-style snapshot test (same convention as the advisor).

## 3. Phase 5 — LLM features across the toolkit

### 5A. Route verification copilot — `tools/verify_routes_llm.py` (task 20, **P0**, M)

*This one is on the critical path: it de-risks task 7 (acts 2–10).* For each act: fetch + cache the relevant poewiki quest/zone pages (`data/wiki_cache/`), then prompt (standard tier) with the act JSON + page texts. Output schema: `{"findings": [{"step_id", "severity": "error|warn", "issue", "evidence", "source_url"}]}` — evidence quotes must be short fragments, and findings are **advisory**: a human fixes the JSON; the tool never edits routes. Feed confirmed findings into REVIEW.md.
Acceptance: seed a corrupted copy of act1.json with 3 planted errors (wrong reward NPC, missing trial, fake skill point) → verifier reports ≥ 3/3; clean act1 → 0 errors (warns allowed). Degrades to: skip, validator still gates.

### 5B. Nerf-exposure report — `advisor/exposure.py` (task 27, P1, S)

Input: any PoB code. Extract gems (existing parser) + uniques/keystones (extend `pob.py` minimally: `Items/Item` text blocks — take first line as name; Spec keystones via tree data). Prompt (standard) with those lists + `data/3.29/summary.json` → per-component table: `{component, change, direction, source, quote}` + overall verdict. Everything unsupported by the summary is tagged `assumption`. Output: `exposure_<build>.md`.

### 5C. Clipboard item evaluator (task 21: parser+rules **P0** M; LLM path P1 S)

The accepted pattern in the PoE tool ecosystem (Awakened PoE Trade et al.): the *player* presses the game's own Ctrl+C on an item; the tool reacts to the clipboard. We synthesize zero input — Qt's `QApplication.clipboard()` `dataChanged` signal is the trigger (no new deps, no polling loop).

- `overlay/itemtext.py` (stdlib, testable): parse the copy format — blocks split by `--------`, header block has Item Class / Rarity / name / base; parse `Sockets:` links, life/resists/movement-speed lines, item level. `VERIFY:` capture 5 real clipboard samples in Mirage as fixtures before finalizing the grammar.
- Decision engine, rules first: during campaign, verdicts from deterministic checks (links ≥ current best, movement-speed boots, resist totals vs. a per-act resist budget table, weapon DPS lines for the build's damage type from `build_notes`). Verdict enum: `TAKE / SKIP / CHECK`.
- `CHECK` → LLM (fast tier), context = item text + build summary (class, main skill, build_notes) + character level; one-sentence verdict + reason; 3 s timeout → fall back to `CHECK (no net)`; cache by SHA-1 of item text; hard cap 10 calls/min.
- UI: verdict renders in the overlay meta line for ~6 s, hotkey-toggleable feature. Kill switch → rules-only mode.
- Acceptance: parser fixtures round-trip; rules unit-tested against synthetic items; a leveling session produces sane verdicts (manual smoke, logged).

### 5D. NL → trade query — `tools/tradeq.py` (task 28a, P2, M)

`tradeq "boots 30 movespeed, life, cold res, max 5c"` → LLM (standard) emits official-trade-API query JSON constrained by a schema **and** validated against the real stat catalog (fetch once from the trade API's data/stats endpoint — `VERIFY:` path at runtime, cache to `data/trade_stats.json`). Tool POSTs the search (a read operation), prints the result count + site URL, opens the browser. The human does everything after that. Degrades to: print the JSON for manual pasting.

### 5E. Post-run retro (task 28b, P2, S)

`tools/retro.py runs/<file>.json`: splits vs PB, level-vs-time curve, death lines from the session log (`"<name> has been slain"` — `VERIFY:` exact wording from a deliberate Mirage death, add to watcher fixtures). LLM (standard) writes a half-page retro with 3 concrete changes for next run. Degrades to: stats table only.

### 5F. In-overlay "ask" hotkey (task 28c, P2, M)

F8 → small input row on the card; context = current step + next 2 steps + last 30 log lines + build_notes + zone level. Standard tier, stream answer into the meta area. Strict scope: Q&A only, no actions. Skip if latency in practice > ~4 s median.

---

## 4. Phase 6 — Market intelligence (`market/`)

### 4.0 The line (extends invariant #1 — read before building)

**Allowed and built here:** fetching public market data with a rate-limit-honoring client and an identifying User-Agent; storing and analyzing it; detecting opportunities; desktop alerts; pre-drafting whisper text; copying it to the clipboard on a keypress; a manual trade journal.
**Forbidden, never build:** sending whispers or any message automatically; accepting/initiating trades; synthesizing any input into the game or the website; auto-refresh sniping loops that fire actions; listing/price manipulation; ignoring HTTP 429s or rate-limit headers. Trade bots are a GGG banwave category; this system's edge is *selection quality*, not action speed.

**Economics honesty (set expectations):** obvious margins compress within days of league start; manual execution caps throughput at roughly a flip per few minutes; price-fixers poison naive signals. Peak windows: league days 1–7 and post-patch shocks. The journal (4.5) exists to measure whether this beats just playing the game.

### 4.1 Data sources — `market/sources.py` (task 22, P0, M)

- **poe.ninja** (primary, aggregated): currency overview + item overviews (scarabs, essences, div cards, uniques). `VERIFY:` current endpoint paths/params by inspecting the site's network calls; expected shape includes pay/receive ratios per currency pair direction and listing counts. Poll: 5 min (currency), 30 min (items).
- **Official trade site API** (targeted): bulk exchange endpoint for direction-specific quotes on specific pairs, search+fetch for watchlist items. Client requirements: honor `X-Rate-Limit-*` / `Retry-After` headers with a token-bucket (`VERIFY:` header names from live responses), User-Agent `poe-league-tools/1.0 (contact: <owner email>)`, global concurrency 1, and hard floor of 1 request/2 s regardless of headers.
- **In-game currency exchange** ratios arrive via ninja's tracking of it (`VERIFY:` coverage); we never touch the game client.
- Stretch (P2): GGG's official OAuth developer API / public stash river for firehose data — registration required, `VERIFY:` at GGG's developer docs before scoping.

### 4.2 Storage — `market/store.py` (task 23, P0, S)

SQLite (stdlib), `market/market.db` (gitignored):

```sql
CREATE TABLE snapshots(ts TEXT, source TEXT, league TEXT, item TEXT,
  buy REAL, sell REAL, buy_vol REAL, sell_vol REAL, raw TEXT,
  PRIMARY KEY(ts, source, item));
CREATE TABLE opportunities(id TEXT PRIMARY KEY, ts TEXT, kind TEXT, path TEXT,
  margin_pct REAL, est_profit_c REAL, liq_score REAL, confidence TEXT, flags TEXT);
CREATE TABLE executions(id TEXT PRIMARY KEY, opp_id TEXT, ts TEXT,
  legs TEXT, realized_profit_c REAL, minutes REAL, notes TEXT);
```

`market/daemon.py`: polling loop writing snapshots; single process; clean shutdown.

### 4.3 Scanner — `market/scanner.py` (task 24, P0 core / P1 tuning, L)

Deterministic. Two detectors over the latest snapshot set:

**(a) Cycle arbitrage.** Nodes = tradable units with two-way quotes. Directed edge u→v with effective rate `r = quote(u→v) × (1 − h)` (haircut `h` default 0.04 for slippage/staleness, configurable). Weight `w = −ln r`. Run Bellman-Ford (or SPFA) per component; any negative cycle is an arbitrage loop with margin `exp(−Σw) − 1`. Report cycles with margin ≥ `min_margin` (default 5 %) and bottleneck liquidity `min(edge volumes) ≥ min_vol`. Deduplicate cycle rotations (canonical rotation = start at lexicographically smallest node).

**(b) Two-hop spreads.** Same item quoted across venues/directions (exchange buy vs. bulk-trade sell, basket-vs-chaos for scarabs/essences): flag when spread beats haircut + threshold.

**Anti-price-fixing filter (mandatory before ranking):** for trade-listing-derived quotes, take the top N listings; if the cheapest k are > x % below the band median, drop them and re-quote; require ≥ m listings inside the band; otherwise set `confidence = "low"` and flag `price_fixing_suspect`. Defaults N=20, k≤3, x=25 %, m=6 — tune during the Mirage rehearsal.

**Ranking:** `est_profit_per_hour = margin × size / (legs × leg_minutes/60)` with `size = min(bottleneck_liquidity × 0.25, bankroll × 0.2)`; `leg_minutes` default 2 (manual whisper→trade), 0.5 for exchange legs. Bankroll in `market/config.json`.

Output object per opportunity: `{id, kind: "cycle"|"spread", path: [legs], margin_pct, est_profit_c, est_profit_per_hour, liq_score, confidence, flags, actions: [{type: "whisper", text} | {type: "exchange", instruction}]}` — whisper text comes from the trade API's fetch response when available; exchange legs render as an instruction line.

Acceptance: unit tests with synthetic rate graphs (a planted 3-cycle at +8 % found; +2 % below threshold ignored; fixer-poisoned quote filtered); snapshot fixtures captured from Mirage.

### 4.4 LLM layer — `market/brief.py` + `advisor` crossover (task 26, P1, M)

Three prompts (all standard tier, all degrade to "skip" under the kill switch — the scanner runs without them):

1. **Launch watchlist:** inputs = `data/3.29/summary.json` + the advisor's recommendation set → `market/watchlist.json`: `[{item, reason, source, expected_window}]` (uniques/bases/currency enabling buffed archetypes). The daemon adds watchlist items to targeted trade polling. Every entry must cite a summary item or be tagged `assumption`.
2. **Daily brief:** inputs = top-20 opportunities + 24 h trendlines + watchlist hits → one-page markdown: what to flip, what to hold, what changed, explicitly flagging low-confidence signals.
3. **Anomaly explainer:** on demand for one opportunity: given its quotes, listing distribution, and recent news snippets, label probable cause `{price_fixing | patch_demand | low_liquidity | genuine}` with reasoning. Advisory only; never changes scanner output.

### 4.5 Execution console — `market/console.py` (task 25, P1, M)

Plain-terminal dashboard (stdlib): ranked opportunities table, refresh on keypress, and per-row actions: `[c]` copy next leg's whisper to clipboard (Windows: `subprocess.run("clip", input=text.encode(), shell=False)` — `VERIFY` behavior; fallback prints for manual copy), `[j]` journal a fill (prompts for realized amounts + minutes → `executions`), `[x]` dismiss, `[?]` anomaly-explain. Threshold alert = console bell + highlighted row (no toast dep). `tools/pnl.py`: realized vs. expected by day/kind — the calibration loop for haircut and thresholds.

**Every send and every trade is a human action. The console never touches the game or the website's message functions.**

### 4.6 Mirage rehearsal (before Jul 20 — same window as the route live-test)

Run daemon + scanner against the dying Mirage economy for ≥ 24 h: validates endpoint shapes (closes the VERIFYs), produces snapshot fixtures, tunes the fixer filter on a market where fixers are rampant, and dry-runs the console with journal entries. Findings → DECISIONS.md.

---

## 5. Task table additions

| # | task | tier | depends | size |
|---|---|---|---|---|
| 19 | llm/client + usage meter + kill switch | P0 | 15 | S |
| 20 | 5A route verifier (fixtures + wiki cache) | P0 | 19 | M |
| 21 | 5C item parser + rules engine (LLM path P1) | P0 | 8 | M |
| 22 | market sources + rate-limit client | P0 | — | M |
| 23 | store + snapshot daemon | P0 | 22 | S |
| 24 | scanner (cycles, spreads, fixer filter) + tests | P0 | 23 | L |
| 25 | execution console + journal + pnl | P1 | 24 | M |
| 26 | watchlist + daily brief + anomaly explainer | P1 | 19, 24, 18 | M |
| 27 | 5B nerf-exposure report | P1 | 16 | S |
| 28 | 5D tradeq · 5E retro · 5F overlay ask | P2 | 19 | M each |

Revised critical path: 4→5→(6,20)→7→17 unchanged; market P0 tasks (22–24) parallelize against the route grind and **must** hit the Mirage rehearsal window. If time pressure forces cuts before launch: cut 21's LLM path and all of 26 first; never cut 17.

## 6. New pre-made decisions

SQLite for market storage (stdlib). Clipboard is read via Qt signal only; the copy-to-clipboard action uses the OS `clip` utility on an explicit keypress. Model tiers: fast/standard/deep with strings resolved from live docs at setup. Haircut 4 %, min margin 5 %, bankroll fraction 20 % as starting parameters — tuned via the journal, not vibes. Console is terminal-only (consistent with the CLI-not-web decision). The scanner never blocks on an LLM call.

## 7. Owner inputs for DECISIONS.md

1. Starting bankroll assumption for sizing (in chaos/div terms) once 3.29 stabilizes.
2. Contact string for the trade-API User-Agent.
3. Whisper etiquette: use the API-provided template verbatim, or a custom polite template?
