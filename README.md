# Yardstick

**A stock and ordering planner that runs on what the site actually uses.**

Built in one day at Vibe Coders Hackathon 3 (Kolkata, 21 Nov 2026) for Lumino Industries' problem statement **"1,700 Poles"**: a fixed supply plan kept sending material while the site used far less, and the surplus sat idle in a yard.

On Lumino's real project data (Kanpur, Mar 2023 to Oct 2026, 16 items, 6 godowns, 2.3 lakh stock movements) Yardstick shows what happened, replays the project month by month with a planner that only sees the past, and compares the two honestly.

## The three jobs from the brief

| Job | Screen | What it does |
|---|---|---|
| 1. See it | **See it** | For every item and godown, month by month: what came in, what went to contractors, what was actually used, what sat idle. Poles peaked at 48,991 idle in Feb 2025, the month the site used 1,859. |
| 2. Plan it | **Plan it** | Replays the project. Each month the planner sees only what was known by then and says what to **order** (because of lead time), **hold**, or **move** from another godown, with a one-line reason per godown. Then it is scored against what really happened: idle stock **and** shortage. A saving that starves the site does not count. |
| 3. Match the sets | **Match the sets** | Compares Lumino's set ratios with what the site really used, and tracks each child item against its parents godown by godown. Only items that follow their parent closely are judged; the rest are listed for Lumino to explain. |

## Added after the desk visit: Requisition
Lumino's head-office procurement starts from a bill of quantities (how many poles, plus the other lines the tender lists), not from a monthly forecast. So **Requisition** takes a BOQ and returns a buy list: every part that is needed, minus what is already in the stores, with the last day to order each item so the deadline holds, and the day the list must reach procurement (Lumino's team asks for 7 to 10 days to place an order). A line typed on the BOQ always wins. Parts that come with a set use the set ratio (Lumino's file or what the site really used; both are shown). Everything else is estimated from what this project used per pole and is labelled as an estimate.

Example on the real data: a BOQ of 1,700 poles needs 16 lines. 13 are already in stock, 3 must be bought, and 1 of those cannot arrive in 90 days at its usual lead time. Read through Lumino's own file, the same BOQ would need 220 ring lugs where the site really used about 5,000, and thousands of tonnes of M16 bolts where the site used about half a tonne (the file's unit looks mixed up).

## Results (default settings, real data)
- 13 of 16 items: median **54% less idle stock** with under 1% of use unsupplied.
- Poles: average idle stock 29,572 → 15,347 (−48%), peak 48,991 → 32,490, zero shortage.
- **What that rests on** (shown on Plan it under "What the saving depends on", each assumption taken away in turn):
  1. The planner knows the project's total requirement, as a bill of quantities would tell it. The data has none, so what the project really bought (1,17,050 poles) stands in, and the plan never orders past it. Without it: still 13 of 16 items and a median 54%, but poles fall to a 37% cut and the plan would buy 1,33,442 poles.
  2. Stock reaches contractors only as it is used. In reality contractors drew ahead of use: on poles they held about 11,182 on average. If they keep doing that and only godown stock is compared: 7 of 16 items keep the site supplied, median cut 26%, poles 33%.
  3. Take both away and ordering by use alone no longer beats what happened on poles (idle stock +4%).
- Forecast check, all 16 items: at the start of each scored month the forecast is compared with what the site really used over the months an order has to cover. It is off by a median 51% of real use and leans high by a median 35% on purpose (the higher of the last 3 and last 6 months' average). With that cushion the site was fully supplied in 506 of 512 scored item-months.
- Honest limits: three items ran short and are marked "does not count"; the first 9 months run on the original plan because a forecast needs history; use per godown is estimated because Lumino records usage for the whole project only.
- Only 1 of 8 stated set ratios agrees with real use (the eye hook). Ring lugs were used at 22 times the stated rate.

## How it is built
- `core/ledger.py`: the monthly ledger from the data pack's own two sums (godown stock, stock with contractors).
- `core/planner.py`: the replay. At month *t* it reads only months before *t* (tests change the future and check nothing earlier moves), with one stated exception: the cap on total buying is what the project really bought in all, standing in for the BOQ total (a test pins that this is the only way future receipts reach it). Orders cover lead time plus a month of forecast use plus a safety margin, never more in total than the project really bought; stock above one godown's need moves to a godown about to run short.
- `core/sets.py`: ratio calibration against real use, pairing strength, running account per godown.
- `core/explain.py`, `core/export.py`: the reasons per godown and the Excel exports (the plan, and the buy list).
- `core/requisition.py`: BOQ to buy list. Set ratios (file and site), per-pole estimates, stock in hand, lead times and order-by dates; every line says where its number came from.
- `app.py`: the Streamlit screens. No logic in it.
- `tests/`: 47 tests on made-up scenarios with known answers and, when the data is present, on Lumino's real numbers.

No AI model sits in the numbers: every figure is computed by code and can be traced to the records.

## Data
The project data belongs to Lumino Industries Limited and is **not in this repository**. `tools/make_bundle.py` sums the raw files to month × item × godown and packs them into one text blob. The app reads that blob from a host secret (`LUMINO_BUNDLE`), the environment, or a git-ignored local file. Without it the app says so instead of crashing.

## Run it
```
pip install -r requirements.txt
python tools/make_bundle.py <folder with the Lumino csv files>   # writes data_local/bundle.txt (git-ignored)
streamlit run app.py
pytest
```

## Licence
Copyright © 2026 Ishan Thakur. All rights reserved. The code is public to read; using, copying or deploying it needs written permission.
