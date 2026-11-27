# HANDOVER

Last update: Fri 27 Nov 2026, IST. Phase 3 of the v2 rebuild (actions: the weekly pack, BOM fix sheet, contractor
holdings, money layer, set-driven children) is done, reviewed by Opus and released as **v1.3** (on `main`).

The day-one hackathon build is preserved at tag `vch3-submission` (commit e1da49c) and in the frozen repo
`ishanthakur526-droid/vch3-yardstick`. **Never push to that repo.** This repo (`ishanthakur526-droid/yardstick`)
carries its history and is where all work continues.

## Where things stand
- **Branches:** work on `dev`, push often (CI runs ruff + pytest on Python 3.12 on every push). Fast-forward `main`
  only at the end of a green phase and tag it (`v1.1`, `v1.2`, ...). The Streamlit app deploys from `main`.
- **Live app:** not yet deployed from this repo (the owner deploys it; see "Deploy" below).
- **Tests:** 147, all green locally (real-project tests included); the real-project tests skip in CI.
- **What v1.3 adds:** the actions (see "Phase 3, as built" below): the weekly action pack on Home, the BOM fix
  sheet and the set-driven bake-off on Match the sets, contractor holdings against the rule, rupees from the
  visitor's own rates.
- **What v1.2 added:** the levers framing (see "Phase 2, as built" below): Home is the first screen.
- **What v1.1 was:** the day-one features (See it, Plan it, Requisition, Match the sets, About), now working on any
  project's data:
  - `core/config.py`: the anchor item (what a BOQ counts in), closing stores, set thresholds, labels, read from an
    optional `meta` table or worked out from the records (each guess shown as an assumption).
  - `core/ingest.py`: raw stores extract (5 CSVs; sets and transfers optional) to monthly tables, shared by the CLI
    and the app.
  - `core/dataset.py`: one project's data plus a fingerprint; every cached result is keyed by it.
  - `core/synth.py`: a made-up sample project (seed 7). It is the public demo when no real data is loaded and the
    CI dataset.
  - `ui/`: one module per screen; `app.py` is a thin router; the sidebar picks the project.
- **Results on the real project** (pinned by tests), default levers (BOQ cap on, 2 weeks of contractor cover):
  poles 29,572 → 30,497 (order by use) → 23,688 (+ BOQ cap, stand-in) → 17,551 (+ cover) average idle; 13 of 16
  items count, median 50% less idle stock. With 0 weeks of cover the old v1.1 headline reproduces exactly (poles
  15,347; 13 of 16, median 54%).
- **Sample project:** 9 of 9 items count with both levers, median 43%. Its dynamics were calibrated to the real
  project's measured profile before the planner was run on it (see the docstring); do not retune it to change the
  planner's score.

## Run, test, build the bundle
```
uv venv --python 3.12 .venv && uv pip install --python .venv/Scripts/python.exe -r requirements-dev.txt
.venv/Scripts/python.exe -m pytest              # all tests
.venv/Scripts/ruff.exe check . && .venv/Scripts/ruff.exe format --check .
.venv/Scripts/python.exe -m streamlit run app.py
python tools/make_bundle.py <folder with the csv files> --meta <meta.json> --secrets data_local/streamlit_secrets.toml
```
- The raw data is never in the repo. `data_local/` (git-ignored) holds `bundle.txt` (read locally) and
  `streamlit_secrets.toml` (one line, `YARDSTICK_BUNDLE = "..."`, for the host's Secrets box).
- Without any bundle the app runs on the sample project; deleting the host secret switches the public app to it.

## Rules that hold every phase
- **No look-ahead.** The planner at month t reads only months before t (the cap on total buying is the one stated
  exception). Tests scramble the future and check earlier decisions do not move; every new planning feature gets the
  same test.
- **A saving that starves the site does not count** (under 1% of use short). Defaults (takeover 9, window 6, safety
  2.33) were fixed before looking at results; change one only with a walk-forward check and a note on screen.
- **Every number on screen comes from code** and the screen says how it is computed. No AI model in the numbers.
- **Data from a file is untrusted:** names shown in markdown go through `core.safe.md`; CSV downloads through
  `core.safe.csv_bytes`; Excel writers use `export.XLSX_OPTIONS` (no formulas or links from text). Never put data
  into `unsafe_allow_html`.
- **Caches:** `st.cache_data` functions take the dataset fingerprint as a hashed argument and the dataset as `_ds`.
  Never put an uploaded dataset in `st.cache_resource`.
- **Small, clear commits**; no secrets, keys, `.env`, raw CSV/XLSX or bundles in git. After a push that changes
  `core/`, bump the comment line in `requirements.txt` (a hot reload on the host once kept a stale module).

## Phase 2, as built (v1.2)
- **Lever 1, BOQ cap** (`Settings.capped`): `planner.cap_for` takes the item's BOQ (`Dataset.boq_qty`) when the data
  has one, else what was really bought, labelled `cap_source = "stand-in"`; `"none"` when switched off.
- **Lever 2, contractor cover** (`Settings.cover_weeks`, default 2.0, fixed before any result was seen):
  - Issue = `max(0, use + keep - P)`, then `P <- P + handed out - used`, with `keep = cover x forecast(site use so
    far)` and cover = weeks / 4.345 months. A godown shortfall eats the top-up first; only use the pool cannot cover
    counts as a site shortage.
  - **Two deliberate departures from the earlier note**, both recorded here: (1) the note's formula
    `use + max(0, cover x f - (P - use))` double-counts use (cover 0 and an empty pool would issue twice the use) and
    breaks conservation, so the consistent form above is used; (2) the issue rule runs from month 0 with an empty
    pool, not from takeover with the real pool, because it is a stores rule that needs no planner history and because
    only then does cover 0 reproduce the old `basis="use"` exactly. That equivalence was proved on scenarios and on
    every sample and real item before `basis` was removed (commit 0b35c2a); `test_zero_cover_is_the_old_plan_on_site_use`
    keeps proving it at the simulate level.
  - `cover_weeks=None` (contractors draw as they really drew) replaced `basis="issues"`. It is now scored on all idle
    stock (godowns + their real holdings), and their **returns go back into the godowns** (the old basis dropped net
    returns, which made a small Kanpur item look 93% short).
  - `planner.decide` is the start-of-month call; `simulate` passes it only the months before. Monday's list uses it too.
- **`core/levers.py`**: `steps`, `rows`, `item_waterfall`, `portfolio`. **`core/misses.py`**: contractor stock, ratio
  gaps (a factor beyond 100x is counted as a likely unit mismatch, not the headline), closing-store transfers.
  **`core/actions.py`**: Monday's list on the real stock position as of a month (stock at the end of the month before
  plus this month's deliveries; deliveries within one lead time count as on order), scramble-tested.
- **UI:** `ui/home.py` is the first section; Plan it has the lever controls and a "What each lever is worth" table;
  number tiles wrap (`ui.common.tiles`) instead of cutting numbers off; widget keys go through `ui.state.k(name)`, which
  adds the project's fingerprint (after a project switch the browser kept showing the old item's name).

## Phase 3, as built (v1.3)
- **Weekly action pack** (`core/pack.py`): `build(ds, month, settings, rates, actions)` returns the sheets as tables
  (Summary, Transfers, Orders, Issue holds, Set alerts, BOM fixes, Assumptions); `workbook`/`write_sheets` write them
  with `export.XLSX_OPTIONS`. Order by = the first Monday of the as-of month; each order line says the month it lands.
  Set alerts are the `sets.balance` rows at the end of the month before, with ratios from `calibrate(before=month)`.
  **PO hold/accelerate is cut**: the data has no open-orders table (the pack's assumptions say so). On Home the pack is
  a `download_button` with a callable, so it is built only on click (AppTest does not click it; `tests/test_pack.py`
  covers `build` and the bytes).
- **BOM fix sheet** (`core/bomfix.py`): Keep / Change the ratio / Check the unit first (beyond 100x, `UNIT_SUSPECT`);
  suggested ratio to three figures; blank "approved" columns. Nothing is ever applied. Downloadable on Match the sets
  (whole project) and in the pack (as of the month).
- **Contractor holdings** (`core/holdings.py`): `series` (start of each month: held at last month end, allowed =
  cover x forecast of use so far, excess, weeks held, alert when excess > one step) and `at(month)`. Monday's list uses
  the same `allowed` rule, so its issue holds equal the alerts (tested). Home shows the series as a chart.
- **Money** (`core/money.py`): `clean_rates`, `item_value`, `portfolio`, `rupees`. Rates and the cost of capital
  (placeholder 12%) live in a `st.data_editor` keyed per project (`home_rates@...`), session only; they never enter a
  cache. The "saving that counts" adds up only items whose step keeps shortage within 1%. **No rupee figure for the real
  project is written anywhere** (tests use made-up rates on the sample).
- **Set-driven children** (`core/setplan.py`): driver = each parent's use x its stated ratio, added over the child's
  sets; forecast = `cumsum(child[:t]) / cumsum(driver[:t])` x `planner.forecast(driver[:t])`, falling back to the
  child's own forecast before any parent use. `bakeoff` scores WAPE over lead + 1 months on the replay's scored months;
  `adopt_at(t)` repeats it on spans fully before t. `planner.decide(..., f_total=)` takes the forecast; only Monday's
  list (and so the pack) uses it, and only for a child adopted as of that month. **The replay and the waterfall stay
  per item.** Real project: 2 of 6 children adopted (one clear win, one tie); sample: 2 of 3.
- **`sets.calibrate(before=)`** reads only months before; with no use yet the factor is nan and the line is "Keep".
- **Tests:** a shared `shaken_future()` in `tests/conftest.py` scrambles everything from a month on; used for holdings,
  the as-of calibration, the set forecast and its adoption, Monday's list with sets, and every sheet of the pack.
- **Opus review #1 (lever maths), 27 Nov.** Verified: no look-ahead in the replay (scrambles over 4 months x 9 items
  x cover None/0/2/6), conservation and the pool identity on 3,000 random scenarios, the pool never negative,
  cover 0 = the old basis, the cap never breached after rounding, the 1% rule's numerator and denominator in both
  modes, the bake-off scored on the same spans. **Must-fix, fixed:** set-imbalance thresholds scaled to the
  whole project's size leaked the future into the pack's as-of set alerts; `ProjectConfig.scaled` now records
  which were scaled and `pack.as_of_thresholds` scales them again on the months before (the pack scramble test
  now includes months with alerts). **Should-fix, fixed:** no set forecast on the as-drawn basis; the order's
  reason names a contractor top-up; `_up` keeps the item's own decimals; Monday's stand-in cap counts the same
  godowns as the replay; the pooled per-godown `shortage` column is documented as the site's shortage spread
  over the godowns.

## Next: the v2 phases
- **Phase 4: data screen.** Upload the 5-CSV pack or one xlsx; a downloadable blank template; a Pass/Warn/Fail
  validator; a fuzzy column mapper (difflib plus synonyms). The uploaded dataset goes in
  `st.session_state["custom_ds"]`, which `ui/state.choose()` already offers in the sidebar. Release v1.4.
- **Phase 5: hardening.**
  - Upload size and row caps, an xlsx zip-size check, read-only openpyxl, defusedxml (already pinned).
  - The BOQ upload in `ui/buylist.py` goes through the same safe reader.
  - Profile and keep each screen under 2 s warm; then the Opus security review (ingest, export, caches, markdown).
- **Phase 6: the private follow-up package** for Rohit, in `F:\ClacuttaAIClub_Hackathon\followup\`, never in this repo.

## Known quirks
- Streamlit `AppTest` cannot drive file uploads. Inject a dataset with `at.session_state["custom_ds"] = ds`, then
  pick it with `at.radio(key="dataset").set_value("custom").run()`. Setting the radio's value directly fails when its
  options change between runs.
- On this Windows machine, a bash heredoc whose text contains `'''` breaks the shell. Write such scripts to a file
  first.
- The pytest config adds `-q`, so `pytest -q` prints no summary line; count the dots or use `-rA`.
- The local preview server does not always pick up edits to `ui/` or `core/` modules: stop and start it again before
  checking a change by eye.
- AppTest: widget keys carry the project's fingerprint, so read them by prefix (see `widget()` in `tests/test_app.py`).
- AppTest can drive a `st.data_editor`: set its key to `{"edited_rows": {row: {column: value}}, "added_rows": [],
  "deleted_rows": []}` and run (see `test_rupees_appear_only_after_a_rate_is_typed`). AppTest does not click download
  buttons, so test what they build directly (`tests/test_pack.py`).
- In the built-in browser, a data-editor cell edits like this: click the cell, press Return, type, press Return.

## Deploy (owner's clicks, Streamlit Community Cloud, free)
1. share.streamlit.io → Create app → Deploy a public app from GitHub.
2. Repository `ishanthakur526-droid/yardstick`, branch `main`, main file `app.py`. App URL: pick a short subdomain.
3. Advanced settings: Python 3.12; Secrets: paste the one line from `data_local/streamlit_secrets.toml`.
4. Deploy. Then `python tests/smoke_live.py <url>` (needs Playwright, dev only).

## If Claude is unavailable
1. Open Antigravity (`%LOCALAPPDATA%\Programs\antigravity`), then File > Open Folder > `F:\ClacuttaAIClub_Hackathon\yardstick`.
2. Paste this prompt into its agent:

> You are continuing the Yardstick v2 rebuild in this folder (public repo `yardstick`). Read HANDOVER.md and README.md first. Hard rules: never commit keys, `.env`, `secrets.toml`, `data_local/` or any raw company data (CSV, XLSX, ZIP); never push to the old `vch3-yardstick` repo; work on `dev`, commit small and often with clear messages, and only fast-forward `main` when the tests pass; keep the planner free of look-ahead (add a scramble-the-future test for any new planning logic); a saving that causes over 1% shortage does not count; no paid services. Continue from "Next: the v2 phases", test with `pytest` and `streamlit run app.py`, and tell me exactly what to click for any deploy step.
