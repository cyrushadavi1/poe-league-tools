# Vendored Exile-UI data

Source: https://github.com/Lailloken/Exile-UI (MIT, (c) Lailloken and
contributors) — vendored so tests and tools run offline.

- `areas.json` — `data/english/[leveltracker] areas.json`: campaign
  area ID ↔ display name ↔ monster level, one list per act (index 0 =
  act 1; index 10 is the epilogue). Display names sometimes drop a
  leading "The" ("Twilight Strand"); normalize before comparing with
  Client.txt zone names.
- `default_guide.json` — `data/english/[leveltracker] default guide.json`:
  the community leveling route, one list per act. Steps are either a
  list of markup lines or `{"condition": [...], "lines": [...]}`;
  `enter areaid<ID>` / `to areaid<ID>` tokens mark zone transitions.

Consumers: `tools/crosscheck_routes.py` (route QA against this data)
and `tools/simulate_client.py` (zone name → area ID for fake
'Generating' log lines). The zone-layout image pack is NOT vendored —
`tools/fetch_layouts.py` downloads it on demand.
