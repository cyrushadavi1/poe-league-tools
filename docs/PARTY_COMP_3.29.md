# 4-man party plan — 3.29 Curse of the Allflame

Written 2026-07-16. Sources: Snap's "[POE] Mirage Party Builds" video
(youtube.com/watch?v=CCVJhe1jt1A — Mirage event, core ascendancies, so it
maps onto 3.29 directly), the decoded PoBs below, `data/3.29/summary.json`
(patch claims cite summary item ids in brackets), and web research on the
Bloodline/Reliquarian systems (tagged *(web)* — post-knowledge-cutoff,
verify in game). Earlier drafts assumed a 2-3 player party; this
supersedes them: **carry + aurabot + 2 others, one of whom is a very
casual player who needs a tanky, low-APM build.**

## Reference PoBs (Snap, Mirage event — use as buildgen starting points)

| Role | Class | PoB |
|---|---|---|
| Reave Carry | Duelist/Slayer 96 | https://pobb.in/IbOyEBixzzP3 |
| Aurabot | Scion/Ascendant 99 | https://pobb.in/FqL_xxwGzT2J |
| Fortify/Banner bot | Duelist/Champion 96 | https://pobb.in/ThwsDN6ZVrJ- |
| Mana Guardian (Soul Link) | Templar/Guardian 97 | https://pobb.in/WPEkRA7IcGPM |
| Culler | Ranger/Pathfinder 97 | https://pobb.in/kSoLVgOStEqv |
| Crying Drugger (warcry/flask bot) | Ranger/Pathfinder 98 | https://pobb.in/ZiBFPR93fCNo |

Ultimatum 4-man @ ~75 (leveling-era versions): Carry (Warden)
https://pobb.in/nGIH-cH1t3X4 · Aura (Guardian) https://pobb.in/RcrRAmHhOquP ·
Banners (Champion) https://pobb.in/rtZVo1kSfYFO · Scion Crybot
https://pobb.in/8kbzmVnt6qyl. Also: ItsFineOkay's duo/trio sheet (linked in
the video description) for 2-3 man fallback nights.

## Recommended comp (4 players)

1. **Carry** — Snap's Reave Slayer is the proven template. 3.29 notes
   don't touch Reave; melee dodged the balance pass. ⚠ Its endgame
   version uses General's Cry + Blade Flurry — still legal (Blade Flurry
   has no cooldown) but General's Cry can no longer support cooldown
   skills [skill-generals-cry], and the "Reave AoE snapshotting" tech from
   Snap's companion video is `VERIFY:` at launch. Alternative if the
   carry player prefers casters: 3.29 is a self-cast spell patch —
   level-stacking cold crit (Taryn's Shiver → staff upgrade path
   [unique-taryns-shiver], [base-staff-caster-mods]) scales into 4-man
   monster life.
2. **Aurabot** — Scion Ascendant (Snap's template) or Guardian. 3.29
   additions to fold in: ring corruptions with 20-30% reservation
   efficiency of specific auras and amulet-only 20-30% aura effect
   corrupts [base-new-corruption-implicits]; Mask of the Tribunal buffed
   to 3%/250 attributes [unique-mask-of-the-tribunal → see summary];
   Doryani's Delusion level 30 purities for party max res
   [unique-doryanis-delusion]; socket colours no longer constrain aura
   setups (Skins/Alpha's Howl colour-free) [mechanic-socket-any-colour].
   Endgame goal *(web)*: the **Aul Bloodline** secondary ascendancy
   (drops from Aul in Delve, depth 111+) — 50% increased effect of
   Grace/Determination/Discipline at zero charges or Wrath/Anger/Hatred
   at max charges; shares ascendancy points with the class tree.
3. **Casual player (friend's wife)** — **Champion Fortify/Banner bot**
   (Snap's template). It is the lowest-APM role in the comp: plant
   banners, stay near the party; CwDT + Molten Shell handles defence,
   Champion is inherently tanky, and her banners/Fortify passively buff
   everyone — she contributes by existing. 3.29 doesn't touch Champion
   or banners (no patch data). Hand her the cheap defensive corruptions
   [base-new-corruption-implicits] and she becomes very hard to kill.
   Fallback if she wants to see big numbers: minions — non-Spectre
   minions no longer pause while attacking [mechanic-minions-no-attack-pause],
   so Zombies+SRS kill things while she walks.
4. **Fourth (flex)** — **Mana Guardian with Soul Link** (Snap's
   template): Soul Link onto the casual player is purpose-built
   insurance for this exact party. Alternatives: Crying Drugger
   (Pathfinder warcry/flask bot; Autoexertion automates the rotation) or
   a Culler — but note the classic culler skills got hit: Kinetic Blast
   of Clustering nerfed [skill-kinetic-blast] and Kinetic Fusillade
   (Mirage's #1 ladder skill, 8.46% share) lost a third of its attack
   speed, so culler DPS-tagging is weaker this patch.

## 3.29 party-wide notes

- Any party member can trigger Expedition detonators, Eldritch altars,
  Harvest and Sentinels [mechanic-party-league-interaction] — the
  support players can run mechanics while the carry clears.
- A hired Mercenary is a free 5th body: does NOT count as a party member
  for monster life or item quantity/rarity [mechanic-mercenaries-core].
- Reflect map mods are gone (replaced by mitigable Thorns on rares)
  [mechanic-reflect-reworked-thorns] — nobody has to vet the casual
  player's maps.
- *(web)* **Reliquarian** ascendancy is available in 3.29 with a new
  rotating set of unique-effect notables (GGG thread 3984866 — node
  images only, no text yet); community reports its defensive nodes are
  strong. Evaluate at launch as an alternative for slots 3/4.
- *(web)* 3.29 adds two new **Bloodline Classes** (secondary
  ascendancies), one league-exclusive — unrevealed at time of writing.
- 3.28 "Ancestors" ladder context (via `tools/meta.py`, 42,789 chars):
  Phrecia alternate ascendancies only — class picks there do NOT carry
  into 3.29; skill-level signal that does: Kinetic Fusillade/KB nerfed,
  RF and Void Sphere remain strong (Void Sphere further buffed
  [skill-voltaxic-burst → see also void-sphere entries in notes]).

## Owner TODOs

- Confirm roles with the party, then import the pobb.in codes above into
  `buildgen/party.example.json` → real `party.json` (item 2 of the
  DECISIONS.md checklist) and re-ship `builds/`.
- At launch: verify Reave snapshot tech, Reliquarian node text, and the
  two new Bloodline classes; re-run `advisor/exposure.py` per PoB once a
  key is set.
