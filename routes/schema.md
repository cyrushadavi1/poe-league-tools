# Route file schema

One file per act: `act1.json` … `act10.json`. Files are loaded in numeric
act order (act1, act2, … act10 — plain lexicographic sorting would put
act10 between act1 and act2) and concatenated into a single step list.

```json
{
  "act": 1,
  "steps": [
    {
      "zone":    "The Coast",            // exact in-game zone name (drives auto-advance)
      "kind":    "travel",               // travel | kill | town | trial  (UI accent color)
      "arealvl": 45,                     // optional: the zone's monster (area) level, integer
                                         //   (drives XP-penalty warnings; town steps may omit it)
      "do":      ["Tag the waypoint"],   // the checklist shown for this step
      "layout":  "WP is halfway.",       // optional: layout / navigation hint
      "tip":     "Skip side areas."      // optional: speed or safety tip
    }
  ]
}
```

## Auto-advance semantics

- Entering a zone advances the guide to the first upcoming step whose
  `zone` matches, scanning only `lookahead` steps ahead (default 4).
- Zones not in the window (portals to town, side areas, logouts) are
  ignored, and re-entering the current zone (new instance) does nothing.
- Multi-visit zones (towns, backtracks) just appear as multiple steps.
- Manual `next`/`prev` hotkeys handle anything unusual.

`zone` must match the name in Client.txt's "You have entered X." line
exactly (case-insensitive) — e.g. "The Tidal Island", "Prisoner's Gate".
