# DECISIONS

Running record of decisions made while building the toolkit (per the plan's
DECISIONS.md convention). Newest at the bottom.

## Pre-made / confirmed during the 2026-07-07 build

- **Dependencies:** Python stdlib only, plus PyQt6 (overlay UI) and the
  optional `anthropic` package (LLM features only; everything degrades
  without it, kill switch `POE_TOOLS_LLM=off`).
- **LLM model tiers** (`llm/config.json`): fast = `claude-haiku-4-5`,
  standard = `claude-sonnet-5`, deep = `claude-opus-4-8`.
- **Trade/HTTP etiquette:** User-Agent `poe-league-tools/1.0 (contact:
  cyrus@hadavi.net)`, global concurrency 1, hard floor 1 request/2 s,
  Retry-After honored. Whisper text: the API-provided template verbatim,
  copied to the local clipboard on an explicit keypress only — nothing is
  ever sent automatically (GGG ToS line, see README).
- **Market storage:** SQLite (`market/market.db`, gitignored). Console is
  terminal-only (no curses — Windows-first plain ANSI).
- **Scanner starting parameters:** haircut 4 %, min margin 5 %, bankroll
  fraction 20 %, min volume 20 — tuned via the trade journal
  (`tools/pnl.py`), not vibes.
- **Rehearsal league:** `Mirage` (live-verified event league) in
  `market/config.json`; switch to the 3.29 league name at launch (Jul 24).
- **poe.ninja API:** legacy paths 404 as of 2026-07-07; everything lives
  under `poe.ninja/poe1/api/...` (economy overviews + protobuf builds API).
  Re-verify after the 3.29 launch.
- **Route files:** one `actN.json` per act, numeric-sorted by the engine
  (act10 after act9, not after act1). `arealvl` = zone monster level on
  every non-town step; drives the overlay XP-penalty warning.
- **XP-penalty formula** implemented from poewiki (safe zone
  `3 + floor(L/16)`, `((L+5)/(L+5+d^2.5))^1.5`, plus the 95+ table).
- **Scanner seam:** currency-overview rows are converted to directed pair
  rows via `scanner.pair_rows_from_currency`; derived cross-source 2-cycles
  are suppressed (the spread detector reports those), same-source crossed
  books are kept.
- **Reconstructed plan docs:** the original IMPLEMENTATION_PLAN.md was never
  in this repo; `docs/INTERFACES.md` now serves as the working contract.

## 2026-07-07 review sweep (post-build fixes)

- **Retry-After is honored in full everywhere:** `tools/meta.py` and
  `tools/verify_routes_llm.py` now parse both the seconds and HTTP-date
  forms, uncapped (previously capped at 120 s / misparsed dates as 5 s),
  mirroring `market/sources.py`. `tools/tradeq.py --post` persists its
  last-request timestamp and any 429/token-bucket deadline to
  `data/tradeq_state.json` so the budget survives across processes, and
  reads `X-Rate-Limit-*` headers.
- **Scanner volumes are chaos-normalized:** single-item listing counts are
  multiplied by unit price before gating/sizing (exchange rows already
  chaos); pair-row `sell_vol` is defined as chaos depth. `min_vol` is now
  a chaos-depth gate — retune it during the Mirage rehearsal (20 c is
  permissive).
- **Scanner correctness:** the fixer filter degrades quotes still below
  the band after the k-drop (never HIGH confidence on a bait price);
  cycle detection branches per cycle edge instead of greedy worst-edge
  removal (suppressed/over-cap cycles no longer mask reportable ones);
  3+-leg cycles keep forward and reverse as distinct trades.
- **Trade journal snapshots expectations:** `executions` gained
  `expected_profit_c`/`kind` columns filled at journal time; tools/pnl.py
  prefers them over the (rescanned) live opportunities row and no longer
  reports a locked DB as "no executions journaled yet".
- **Run tracker:** pb.json is only written by runs that reached act 10;
  the clock re-anchors on the first zone (login-queue idle excluded);
  the overlay autosaves the in-progress run every 30 s and primes the
  character level from the Client.txt tail on restart.
- **Client.txt parsing:** system lines are matched from line start (chat
  can no longer spoof events by embedding "] : "); guild tags
  (`<TAG> Name ...`) are accepted; partial (mid-flush) lines are buffered
  until their newline arrives.
- **simulate_client.py** refuses `--out` paths that look like a real
  Client.txt (override: `--i-know-what-im-doing`) and defaults to the
  system temp dir (Windows-safe).
- **Windows text encodings:** clip.exe gets UTF-16 (BOM) payloads;
  meta/verify_routes reconfigure stdout to UTF-8 for redirected output.
- **Clickthrough (F6) is Windows-only:** the non-Windows shortcut
  fallback cannot undo input transparency, so main.py no longer binds it
  there.

## Owner inputs still needed (from the plan §7 + build findings)

1. **Starting bankroll** (chaos/div) for opportunity sizing once 3.29
   stabilizes — placeholder `bankroll_c: 2000` in `market/config.json`.
2. **Party character names** → `overlay/config.json` `party` block, at
   league start (also rerun `buildgen/party.py` with the real PoBs).
3. **Mirage rehearsal checklist (before Jul 20):** run the market daemon
   ≥ 24 h against Mirage; capture 5 real Ctrl+C item samples into
   `tests/fixtures_items/` (replacing authored ones); die once on purpose
   and confirm the "has been slain" line matches the watcher; tune the
   price-fixing filter constants.
4. **July 16 (GGG Live):** paste the 3.29 patch notes through
   `advisor/summarize.py` → `data/3.29/summary.json`, then run
   `advisor/advise.py` + `advisor/exposure.py` on the party's PoBs and
   `market/brief.py watchlist`.
5. **At launch (Jul 24):** set the 3.29 league name in `market/config.json`,
   re-verify poe.ninja endpoints and `tools/meta.py` protobuf schema.
