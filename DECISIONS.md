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

## 2026-07-07 friend onboarding sweep

- **Nobody hand-edits JSON to join.** `buildgen/party.py` now also writes
  `builds/party_bundle.json` (league + members: player/class/ascendancy +
  notes/plan *basenames* — the bundle travels with its folder, so paths
  from the generating machine would be wrong on every PC).
  `tools/join_party.py` (chained from `setup_pc.bat`, re-run-safe) reads
  it, asks "who are you?", finds Client.txt, and writes that machine's
  `overlay/config.json` — preserving unrelated tweaks, backing up a
  corrupt config to `.bak` instead of discarding it.
- **The old printed paste-block was broken by design** for the actual
  deploy flow (generate on the Mac → play on PCs): it embedded
  `os.path.abspath` of the notes file on the *generating* machine. Kept
  as a hand-editor fallback, now overlay-relative with forward slashes.
- **Client.txt discovery** (`overlay/find_client.py`, injectable-FS,
  shared by overlay/wizard/doctor): configured path → common installs →
  every Steam library out of `libraryfolders.vdf` (registry `SteamPath`
  + default dirs; read-only file parse) → per-drive layout scan.
- **Doctor** (`tools/preflight.py`, `doctor.bat`): OK/INFO/WARN/FAIL
  rows with a fix per line; exit 1 on FAIL. Catches the *silent* failure
  modes: `me` misspelt vs the bundle, `build_notes` pointing nowhere
  (previously skipped without a word — now also warned at overlay start),
  stale `Client.txt` from an abandoned install, a log tail with zero
  parseable lines (non-English client), typo'd config keys, duplicate
  hotkeys. Never crashes: every check is wrapped.
- **Failures must be loud where the friend is looking:** malformed
  config.json → plain-language error + exit 2 (no traceback);
  `run_overlay.bat` prints a doctor hint on non-zero exit; a missing
  Client.txt is parked on the overlay card itself (party row — no party
  events can arrive to repaint it while the log is missing).
- **`FRIENDS.md`** is the hand-out: two double-clicks, a symptom table,
  and the ToS answer up front. Ship the folder zipped (`builds/` is
  gitignored, so a clone would lack the bundle).
- **Generic plans verified against the live 3.28 meta (2026-07-08).**
  All seven `data/leveling_defaults.json` entries were rewritten to
  match Maxroll's class leveling guides (each page "Reviewed for PoE
  3.28 Mirage", Mar 2026) with every `@level` claim checked against
  poewiki's cargo DB (two API queries, repo User-Agent). Notable
  corrections over the first-draft classics: Ancestral Call removed
  from Ground Slam/Cleave lines (slams aren't strikes — it never
  worked); Templar now opens Holy Strike + Hallow (new 3.28 holy
  gems); Witch/Templar/Shadow use the ignite-prolif Armageddon Brand +
  Cremation route (not Controlled Destruction); Duelist/Scion open
  Spectral Throw into Sunder @12 with Close Combat @18; Ranger gets
  Momentum/LMP + Manaforged Arrows @28 tech. Re-verify after the
  Jul 16 patch notes (3.29 may shuffle gems again).
- **Bare PoBs get a generic class leveling plan.** A guide-site export
  with only an "Endgame" skill set used to produce zero gem notes
  (empty ⚙ row). `pob.make_plan` now falls back to
  `data/leveling_defaults.json` — authored per class (7 × acts 1-10,
  ≤110 chars/line for the card), league-agnostic classics, freely
  editable. Fallback fires only on ZERO act-tagged notes (a partial
  PoB is respected as authored); entries carry `"source": "generic"`
  (the overlay ignores extra keys) so plan.md and the doctor label
  them. Build-specific notes remain the upgrade path: title the PoB
  sets "Act N ..." and re-run party.py.
- **Zero-install PC bundle** (`tools/make_portable.py` →
  `dist/poe-league-tools-pc.zip`, ~92 MB): python.org's *Windows
  embeddable* CPython + the PyQt6 win_amd64 wheels unzipped straight
  into its site-packages (wheels are install-by-extract; PyQt6 has no
  post-install steps). Friends need no Python, no admin, no internet.
  Chosen over PyInstaller/Nuitka because those cannot cross-build and
  this project's build machine is a Mac; the embeddable approach is
  pure downloads + file assembly, so the Mac builds it (network at
  build time only, cached under `dist/cache/`).
  - The `._pth` file takes FULL control of the embedded interpreter's
    `sys.path` (no script-dir insertion), so it lists every package dir
    the repo imports by-same-directory: `..`, `..\overlay`,
    `..\buildgen`, `..\market`, `..\advisor`. Site stays disabled.
  - `EMBED_VERSION` pinned (3.13.14, verified live 2026-07-07); its
    major.minor must match pip's `--python-version` because
    `PyQt6_sip` wheels are cpXY-specific (handled in the tool).
  - Known limit: no pip inside the bundle, so LLM extras need the
    .venv flavor (or a future `--with-llm` that vendors `anthropic`+
    deps the same way). The .bat files prefer `python\` → `.venv` →
    system Python, so both flavors coexist.
  - Removed a stray empty `{overlay,routes,buildgen,tests}` dir at the
    repo root (2026-07-06 brace-expansion accident) that leaked into
    the first bundle build.

## 2026-07-08 field feedback (first friend test on Mirage)

- **Route fast-forward on startup** (requested: "fast forward to where
  my progress was"). `RouteEngine.fast_forward(zones, known_level)`
  takes the log tail's zone history (`client_watcher.recent_zones`,
  4 MB tail) and reconciles two estimates: replaying the full history
  through `on_zone` (exact when the log covers the run) and the last
  history zone that names a route step (rescues fresh installs,
  rotated logs, alt-character pollution). With a known level, the
  estimate whose area level fits better wins — that's what tells the
  act 1 from the act 6 "Lioneye's Watch". Startup-only: mid-play the
  lookahead window stays the teleport guard. `resume_route: true`
  config key (default on), F2/F3 to nudge a wrong landing.
- Friend verdict otherwise: runs fine, not intrusive; card is not
  clickable by design (hotkeys only, F6 = click-through).

## Owner inputs still needed (from the plan §7 + build findings)

1. **Starting bankroll** (chaos/div) for opportunity sizing once 3.29
   stabilizes — placeholder `bankroll_c: 2000` in `market/config.json`.
2. **Party character names** at league start: rerun `buildgen/party.py`
   with the real PoBs, re-ship `builds/`, everyone re-runs
   `setup_pc.bat` (it re-asks against the fresh bundle).
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
