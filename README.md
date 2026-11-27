# Yardstick

**What each stock decision on a project is worth, and Monday's list that follows from it.**

Built in one day at Vibe Coders Hackathon 3 (Kolkata, 21 Nov 2026) for Lumino Industries' problem statement **"1,700 Poles"**: a fixed supply plan kept sending material while the site used far less, and the surplus sat idle in a yard. Rebuilt since around one question an ERP report cannot answer: *which decisions would have kept that stock out of the yard, and by how much?*

## The idea
Idle stock on a project comes from two decisions the company controls:

1. **How much to buy.** Plan against the bill of quantities (BOQ) instead of pushing the full quantity out early.
2. **How much to issue.** Give contractors only a few weeks of cover instead of letting them draw months ahead.

Yardstick replays the project month by month with a planner that only sees the past. It switches the two decisions on one at a time and scores each one the same way: average and peak idle stock (godowns **and** contractors), and the share of demand left unsupplied. A plan that leaves more than 1% unsupplied does not count. Then it turns the policy into a Monday list for procurement and stores.

## The screens
| Screen | What it does |
|---|---|
| **Home** | The lever waterfall: Actual → order by use → + BOQ cap → + contractor cover (N weeks, a slider), for any item and for all items. Below it, **what the stock report misses** (stock with contractors, set ratios that disagree with the site, stock sent to the closing store) and **Monday's list** as of any month: transfers first, then orders, then contractor issue holds, each with its reason, plus a chart of contractor holdings against the cover rule. **The weekly action pack** (Excel) carries the same Monday: transfer vouchers, purchase orders with order-by dates and the month each lands, issue holds, set alerts, BOM fixes and the assumptions. **In rupees** appears only when a visitor types their own rates (kept for that browser session, never stored). Every number says how it is computed. |
| **See it** | For every item and godown, month by month: what came in, what went to contractors, what was used, what sat idle. |
| **Plan it** | The replay for one item: what the planner would order, hold or move each month and why, scored against what really happened, with both levers adjustable. |
| **Requisition** | A BOQ in, a buy list out: every part needed, minus stock in hand, with the last day to order each item. |
| **Match the sets** | The master data's parts-per-set ratios against what the site used, godown by godown; a **BOM fix sheet** to approve line by line (nothing is applied automatically); and, per child, whether planning it from its sets beats its own history. |

## Results on Lumino's Kanpur project (default settings: 2 weeks of contractor cover, BOQ cap on)
Kanpur, Mar 2023 to Oct 2026, 16 items, 6 godowns. The data has no BOQ, so what the project really bought stands in for it (labelled on screen as a stand-in).

**Poles, average idle stock:**
| Step | Average idle | vs actual |
|---|---|---|
| Actual | 29,572 | |
| Order by use (no cap; contractors draw as they did) | 30,497 | +3% |
| + BOQ cap | 23,688 | −20% |
| + contractor cover, 2 weeks | 17,551 | −41% |

Peak idle falls from 48,991 to 37,677, with no shortage. With no contractor cover at all (they get exactly what the site uses) the last step reaches 15,347.

**All 16 items:** a forecast on its own saves nothing (8 of 16 items keep the site supplied, median cut −3%). With both levers on, 13 of 16 do, with a median **50% less idle stock**. The other three ran short and are marked "does not count".

**What the stock report misses:**
- Contractors held 10,079 poles on average, 38% of all idle poles; at the peak, 15,388 (about 5 months of use).
- 7 of 8 stated set ratios disagree with what the site used. Ring lugs were used at about 23 times the stated rate; 3 of the 7 look like a unit mismatch (bolts stated in another unit).
- 1,779 poles went to the closing store at wind-down; 10 of 16 items were sent there, up to 24% of what was received.

**Monday's list for February 2025** (the month of peak idle): 14 transfers, 1 order, 16 contractor issue holds. In other words: don't buy, move what you have, and stop issuing ahead. The weekly pack for that Monday adds 7 master-data ratios to review (3 of them likely a unit mismatch).

**Planning children from their sets:** over the scored months it was no worse than a child's own history for 2 of 6 children (one clear win, one tie), so Monday's list uses it only for those, month by month, while it holds. The bake-off is the reason; the method is not assumed to be better.

**Forecast check:** over the months each order has to cover, the forecast is off by a median 58% of real demand. It leans high (median 38%) on purpose, so the site stays supplied: 508 of 512 scored item-months were fully supplied.

The sample project tells the same story in its own numbers: a forecast alone makes idle stock worse, and with both levers 9 of 9 items keep the site supplied, with a median 43% less idle stock.

## Honesty rules
- **No look-ahead.** The planner at month *t* reads only months before *t*. Every planning feature has a test that scrambles the future and checks no earlier decision moves (the replay, the contractor pool, Monday's list, contractor holdings, the as-of ratio calibration, the set-driven forecast and its adoption, every sheet of the weekly pack). The one stated exception is the stand-in cap when a project has no BOQ.
- **A saving that starves the site does not count** (over 1% of demand unsupplied).
- **Defaults were fixed before results were seen:** takeover 9 months, forecast window 6, safety margin 2.33, 2 weeks of contractor cover.
- **Every number comes from code**, and the screen says how it is computed. No AI model sits in the numbers.

## How it is built
- `core/ingest.py`, `core/config.py`, `core/dataset.py`: any project's stores extract becomes monthly tables with the project's own settings, read from the data or worked out and shown as assumptions.
- `core/ledger.py`: the monthly ledger from two sums (godown stock, stock with contractors).
- `core/planner.py`: the replay. `decide` is the start-of-month call (move, order, hold) and only ever sees earlier months. `simulate` runs it month by month, with the two levers: a cap on total buying, and a project-wide contractor pool that may hold N weeks of forecast use (issue = use + cover − what contractors hold; a shortfall eats their cover before it stops the site).
- `core/levers.py`: the waterfall rows and the all-items summary.
- `core/misses.py`: contractor-held stock, ratio gaps, closing-store transfers.
- `core/actions.py`: Monday's list, from the real stock position as of a month, using the same `decide`.
- `core/holdings.py`: contractor holdings against the cover rule; `core/bomfix.py`: the BOM fix sheet; `core/setplan.py`: children forecast from their sets (an expanding learned ratio) and the bake-off; `core/money.py`: rupees from the visitor's rates; `core/pack.py`: the weekly action pack.
- `core/sets.py`, `core/requisition.py`, `core/explain.py`, `core/export.py`: set ratios, the BOQ buy list, reasons per godown, Excel exports.
- `core/synth.py`: a made-up sample project, calibrated to a real project's measured dynamics before the planner was ever run on it. It is the demo when no real data is loaded and what CI tests run on.
- `app.py` and `ui/`: the Streamlit screens. No logic in them.
- `tests/`: 147 tests on scenarios with known answers, on the sample project, on a small project shaped nothing like the original, every screen headless, and, when the data is present, on the real numbers.

## Data
Real project data is **never in this repository**. `tools/make_bundle.py` sums the raw files to month × item × godown and packs them into one text blob, which the app reads from a host secret (`YARDSTICK_BUNDLE`), the environment, or a git-ignored local file. Without one, the app runs on its built-in sample project. The Kanpur data belongs to Lumino Industries Limited.

## Run it
```
pip install -r requirements-dev.txt
python tools/make_bundle.py <folder with the csv files> --meta <meta.json>   # writes data_local/bundle.txt (git-ignored)
streamlit run app.py
pytest
```

## Licence
Copyright © 2026 Ishan Thakur. All rights reserved. The code is public to read; using, copying or deploying it needs written permission.
