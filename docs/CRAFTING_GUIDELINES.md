# Crafting guidelines — league-start & leveling (3.29)

The human-readable version of what the crafting copilot knows. Three layers:

- **Order of operations** (`data/craft_order.json`) — the canonical
  sequence and the hard sequencing rules; plans live or die by this.
- **Principles** (`data/craft_guidelines.json`) — general judgment rules,
  fed to the LLM with every plan request.
- **Recipes** (`data/craft_recipes.json`) — concrete methods, filtered by
  item class and character level into the digest.

Distilled from r/pathofexile & r/PathOfExileBuilds threads (crafting
crash-course / crafting-while-leveling / craft-or-trade discussions, the
3.26 crafting AMA) with every game mechanic verified against poewiki
(2026-07-11). Reddit is consensus, the wiki is truth; where they
disagreed, the wiki won.

## Order of operations

The sequence, and *why* it's a sequence — each step is cheap or safe only
before the next one:

1. **Base.** Class/tags decide the mod pool, item level caps the tiers,
   and rarity-sensitive vendor recipes (+1 gems needs a NORMAL weapon;
   the rustic recipe wipes mods) must run before any currency touches
   the item.
2. **Quality.** +5% per whetstone/scrap/bauble on a normal item, +2%
   magic, +1% rare — and quality improves Jeweller's *and* Fusing
   outcomes, so it precedes socket work too.
3. **Sockets → links → colors.** Changing socket count disturbs links
   and colors; Fusings reroll links but keep colors. Bench prices are
   fixed — use them while leveling.
4. **Mods.** Transmute → alt → aug on the magic route; essence or alch
   on the rare route. Every full reroll (essence/chaos/alt/scour)
   deletes bench crafts, which is why benching isn't step 4.
5. **Upgrade or reset.** Regal keeps both magic mods and adds one;
   scour wipes everything except fractured mods.
6. **Bench.** The missing affix goes in last, into a confirmed open
   slot of the right kind. Re-crafting replaces it; a reroll deletes it.
7. **Corrupt.** Vaal Orb ends the item's story. While leveling: don't.

Hard rules the copilot's plans must never violate: quality before
fusings; count before links before colors; rerolls before bench; rarity-
sensitive recipes before currency; fractured/corrupted items are not
recipe ingredients; corruption is final. (Item already mid-sequence?
Plan from where it stands — scouring back to "textbook order" is only
right when the math says so.)

## Choosing the method

Order of operations says *when*; this says *with what*. Match the shape
of the need to the tool (`data/craft_methods.json`):

| You need | Use | Where it exists |
|---|---|---|
| Exactly one specific mod | **Essence** (bench if the slot's open) | drops from Act 1 |
| A themed combination | **Fossils** — raise/block whole mod classes | Delve, Act 4+ (lvl 14) |
| Fix half an item | **Harvest** reforge keeping prefixes/suffixes | maps only |
| Insurance on a gamble | **Beast imprint** (Craicic Chimeral), restore on brick | Einhar, Act 2+ |
| Free gear while leveling | **Rog** (Expedition) — follow his upgrade chain | campaign + maps |
| Better bench options | **Unveils** (Jun) — they compound, unveil everything | mostly maps |
| Implicits on keeper gear | **Eldritch** embers/ichors | endgame maps |
| Merge two half-good items | **Recombinators** | VERIFY 3.29 availability |

By stage: **campaign** = vendor recipes, alt/aug magics, essences, bench,
Rog (skip saving for harvest/eldritch — they don't exist yet); **early
maps** = essence+bench still, plus harvest reforges, unveils, fossil
themes; **75+** = resonator combos, veiled mods, eldritch implicits,
imprint-protected gambles.

## Principles

1. **Open affixes first.** Count open prefixes/suffixes before spending
   anything (the overlay digest does this for you). Wanting a bench life
   craft with no open prefix is the classic wasted-currency mistake.
2. **Life + resist on every rare.** Two out of three already there →
   bench the missing one; don't reroll the item.
3. **Essences over chaos/alch spam** for any targeted mod: same full
   reroll, one mod guaranteed, and league-start pricing favors essences
   heavily.
4. **Set a stop condition before the first orb.** "T3+ life and any
   resist" beats "perfect". Leveling gear only carries you ~10 levels.
5. **Buy enablers, craft commodities.** Mandatory uniques and links are
   trade buys; life/resist fillers, boots and weapons are crafts —
   especially days 1–2 when listings are thin and overpriced.
6. **Weapon first.** Re-craft or re-recipe the weapon every few acts
   before touching armour slots; casters ride the +1 gems recipe all
   campaign.
7. **Base and item level are the craft.** ilvl caps tiers; tags decide
   the pool. The right white base beats the wrong rare.
8. **A good blue beats a bad rare.** Don't regal a strong magic item
   without a plan (and an open affix) for the bench.
9. **Spend the cheap currency.** Transmutes/alts/augs/low essences are
   spendable at league start; regals/alchs/scours/chaos are savings.
10. **Don't out-craft your uniques.** Craft the slots the leveling
    uniques don't cover.
11. **Fractured mods are forever.** A fractured base with a build mod is
    the best craft target of the campaign.

## Recipe highlights (full data in craft_recipes.json)

- **+1 spell gems weapon** (vendor): NORMAL wand/sceptre/staff/rune
  dagger + 2+ gems totaling 40% quality, all with the element's tag.
  *The old ruby-ring + alteration form is gone* — save quality gems.
  Minion variant: normal helmet + 40% quality of Minion-tag gems.
- **% phys weapon** (vendor): weapon + Rustic Sash + Whetstone →
  magic sash 40–49%, rare sash 50–64% increased Physical Damage.
- **Orb of Binding**: normal base → rare with up to 4 linked sockets
  (Harbinger-sourced; 4-socket bases from ilvl 25).
- **Vendor shopping**: stock refreshes as you level — linked bases and
  movespeed boots; linked R-G-B resells for a Chromatic, 6-socket for
  7 Jeweller's.
- **Movement-speed boots** (alt → aug → regal → bench) and **utility
  flask suffixes** ('of Staunching' bleed immunity is near-mandatory
  by Act 3).

## Sources

- reddit.com/r/PathOfExileBuilds — "Crafting while leveling", 3.26
  crafting AMA; r/pathofexile — "Crafting crash course: Beginner to
  Advanced", "New Player - Craft or Trade?", Orb of Binding threads.
- poewiki.net — Vendor_recipe_system, Orb_of_Binding (mechanics, exact
  ranges, eligible classes).
