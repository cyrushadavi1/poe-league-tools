# League-start wealth planner

`market/wealth.py` converts the shared **League Start Wealth Plan: A Guide
for Profit Crafting & Investing (3.29 Update)** into a short, deterministic
plan for the current league day and bankroll.

It fills the gap between the existing tools:

- the crafting copilot answers “what can I do with this item?”;
- the scanner answers “what live spread exists right now?”;
- the wealth planner answers “which kinds of crafts, flips, arbitrage, and
  investments fit my stage and risk budget?”

## Usage

```text
python -m market.wealth --day 1 --bankroll-c 150 --risk low
python -m market.wealth --day 6 --bankroll-c 1200 --risk medium
python -m market.wealth --day 10 --bankroll-c 4000 --risk high \
  --category investment --category flipping
python -m market.wealth --day 6 --bankroll-c 1200 --json --out plan.json
```

The output includes:

- the league stage and its economic goal;
- ranked plays that fit the bankroll and risk profile;
- the live-price check required before committing;
- a stop-loss for every play;
- deferred plays and the reason they are unsuitable now.

The authored strategy data lives in `data/wealth_playbook.json`, so it can
be updated after the 3.29 economy reveals real margins without changing
code.

The 3.29 review added two deliberately separated corruption plays:

- generate and usually sell Locus of Corruption temples while the market
  is still discovering prices;
- only after proving demand, run small fixed-risk batches for the new
  maximum-charge, reservation, action-speed, maximum-resistance,
  gem-level, cast-speed, and explode implicits.

This separation prevents a guaranteed temple sale from being silently
treated as a free crafting attempt. It also keeps Grey Spire and Stasis
Prison implicit-magnitude projects in their own extreme-risk price check.

## Safety and scope

The planner is advisory and offline. It never fetches prices, lists items,
copies whispers, performs vendor recipes, crafts, or executes trades.
Use the market daemon/scanner for live prices, and perform every action
manually.
