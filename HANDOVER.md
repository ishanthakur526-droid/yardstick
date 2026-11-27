# HANDOVER

Last update: Fri 27 Nov 2026, IST. Phase 5 of the v2 rebuild (hardening, profiling, Opus security review #2) is done
and released as **v2.0-rc** (on `main`). Phase 4 (the Your data screen) was v1.4; Phase 3 (the actions, Opus review #1)
was v1.3. Next: Phase 6, the private follow-up package (outside this repo), then v2.0.

The day-one hackathon build is preserved at tag `vch3-submission` (commit e1da49c) and in the frozen repo
`ishanthakur526-droid/vch3-yardstick`. **Never push to that repo.** This repo (`ishanthakur526-droid/yardstick`)
carries its history and is where all work continues.

## Where things stand
- **Branches:** work on `dev`, push often (CI runs ruff + pytest on Python 3.12 on every push). Fast-forward `main`
  only at the end of a green phase and tag it (`v1.1`, `v1.2`, ...). The Streamlit app deploys from `main`.
- **Live app:** not yet deployed from this repo (the owner deploys it; see "Deploy" below).
- **Tests:** 229, all green locally (real-project tests included); the real-project tests skip in CI.
- **What v2.0-rc adds:** hardening (see "Phase 5, as built" below): every name from data shown as text, upload and
  bundle limits that a hostile file cannot get past, an upload's results kept in that visitor's session only, every
  dependency locked, and the speed of every screen measured.
- **What v1.4 adds:** the **Your data** screen (see "Phase 4, as built" below): template, upload, table and column
  matching, a Pass / Warn / Fail check, and the project built into the visitor's session.
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
- **Data from a file is untrusted:** names shown anywhere Streamlit renders Markdown (markdown, alerts, captions,
  widget labels, help, metric values and deltas, radio and segmented-control options) go through `core.safe.md`;
  names in chart text through `core.safe.chart_text`; CSV downloads through `core.safe.csv_bytes`; Excel writers use
  `export.XLSX_OPTIONS` (no formulas or links from text). Never put data into `unsafe_allow_html`.
  `tests/test_hostile.py` checks all of it on every screen: run it after any UI change.
- **Caches:** anything computed from a dataset goes through `ui.state.cached` (not `st.cache_data` directly): it
  takes the fingerprint as a hashed argument and the dataset as `_ds`, shares results for the built-in projects, and
  keeps an upload's results in that visitor's session. Never put an uploaded dataset in `st.cache_resource`.
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

## Phase 4, as built (v1.4)
- **`core/upload.py`** is the upload path, separate from `core/ingest.py` (which it reuses for the sums):
  - `TABLES` is the format: per table, its columns (needed or not, text / number / date, what each holds, an example)
    and other names a column or a file often has in an ERP export. `guide()` and `template_bytes(filled=None)` come from
    it (a "How to fill" sheet, then a sheet per table with the needed headers highlighted; `filled` gives the sample
    project in the same format).
  - `read_upload(name, bytes)` returns `Sheet`s of text and never raises (a file it cannot read comes back with
    `problem` set, in plain words). Limits: `MAX_FILE_MB` 50 (also `server.maxUploadSize` in `.streamlit/config.toml`),
    `MAX_ROWS` 500,000 per file or sheet, `MAX_TOTAL_ROWS` 1,000,000, `MAX_UNPACKED_MB` (zip sizes checked before
    Excel is opened), `MAX_SHEETS` 20, and since Phase 5 the column, cell, file, zip-ratio and shared-text limits (see
    "Phase 5, as built"). Excel is opened by openpyxl read-only; it parses with defusedxml (installed). CSV: encoding (UTF-8, else cp1252) and delimiter (`,` `;` tab `|`) worked out. The header
    line is found within the first 20 lines (exports often put a title above it). The template's own "How to fill"
    sheet is skipped when the workbook comes back.
  - `guess_table` (the file or sheet name decides when it names a table, else the table whose needed columns are all
    found), `suggest_mapping` (exact name, then an exact alternative, then close spelling at 0.8 or more; each column
    used once), `apply_mapping` (renames; thousands separators dropped from numbers). Movement types and transfer
    directions are read from common words ("Issued", "Consumed", "Receipt from another store"), and the check says so.
  - `check(parts)` returns a `Report`: Pass / Warn / Fail lines and the mapped tables. **Fail** stops the build: a
    needed table or column missing, no usable lead time for an item (the planner needs it; nothing is assumed), no
    CONSUMPTION rows, no usable receipts, fewer than 7 months (3 to learn, 4 to score). **Warn** says every row left out
    and why (unreadable date or quantity, unknown item, no godown), negative quantities (counted as entered), repeated
    item codes, files added together, fewer than 13 months, unknown meta keys. `build(tables, project)` goes through
    `ingest.monthly_tables` and `dataset.from_tables(key="custom")`.
  - `ingest.parse_dates` also reads Excel day numbers (45292 = 1 Jan 2024) when a sheet stored dates as numbers.
- **`ui/data.py`** ("Your data", the last section): template and sample downloads; the uploader; per file a table
  picker and a "Match the columns" panel (open when a needed column was not found); the check as a wrapped list (it
  reads on a phone); a project name; "Use this data" (a callback: builds the project into
  `st.session_state["custom_ds"]`, selects it in the sidebar, opens Home). "Remove my data" undoes it. Parsed files
  and the report are kept in `st.session_state` (`data_sheets`, `data_report`) for that session only, dropped once the project is built. Widget keys start
  `data_` and are not in `WIDGET_PREFIXES`, so they survive a project switch.
- **Tested:** the sample round-trips through its own workbook to an identical ledger; an export with its own headers,
  title lines, day-first dates and `;` reads to the same ledger as the original; **Lumino's real raw export reads to
  exactly the same monthly tables through the upload as through the command line** (local only; reads the raw CSVs from
  `YARDSTICK_RAW_CSV` or `../data-lumino/...`); every Fail and Warn; every limit; files that are not what they say.
  Checked by eye in the built-in browser with real uploads (three CSVs with ERP headers, and the sample workbook) at
  desktop and phone size, through to Home on the uploaded project.

## Phase 5, as built (v2.0-rc)
- **Names are text, never markup.** Streamlit renders Markdown in far more than `st.markdown`: metric values and
  deltas, widget labels, help tooltips, radio and segmented-control options, expander and tab labels. Units and item
  names reached metric values unescaped (See it, Plan it, Match the sets); fixed with `md()`. Plotly reads `<a href>`,
  `<b>` and the like in chart text: units, godown labels and child names now go through `safe.chart_text`
  (HTML-escaped; Plotly shows `&lt;` as `<`). **`tests/test_hostile.py`** runs every section, and the Your data screen,
  on a project whose codes, names, units, godowns and project fields carry `![i](https://...)`, `[l](...)`, `<img>`
  and `<a href>`, and checks every string Streamlit will render as Markdown (walking the element protos) and every
  Plotly string. Selectbox options, text-input values and dataframes are plain text and skipped. It also checks that
  every workbook (plan, buy list, upload format, pack) writes formula-like text as a string. Both checks were
  mutation-tested (they fail with the escape or `XLSX_OPTIONS` removed).
- **BOQ upload** (Requisition): `requisition.read_boq` reads through `upload.read_upload` (same size, row and
  unpacked-size limits, title lines, `;`, thousands separators), takes the first sheet with an item and a quantity
  column, refuses more than `MAX_BOQ_LINES` (20,000), never raises, and says why. Read once per upload
  (`boq_read@...` in session state).
- **Bundle** (`core/bundle.py`): empty, cut short when pasted (the inflater did not reach the end), unreadable tables,
  more than `MAX_TABLES`, and runaway JSON nesting all raise `BundleError` with a plain reason; the inflate cap stays.
  `state.bundled` shows the reason and the sample for a damaged bundle, and logs (without content) a bundle whose
  tables are not a project's.
- **Memory:** after "Use this data", `data_sheets` and `data_report` are dropped. On Lumino's 231,000-row export they
  held ~92 MB and ~62 MB (tracemalloc) against ~1 MB for the built project.
- **Dependencies:** `requirements.in` holds the seven direct pins; `requirements.txt` is the full lock
  (`uv pip compile requirements.in --universal --python-version 3.12 -o requirements.txt`, seeded from the tested
  venv; keep the "Changing this file..." comment as its first line).
- **Speed** (`tools/profile_sections.py`, AppTest on this laptop; warm = caches filled, what a visitor clicking around
  sees; cold = first open after the process starts, shared by every visitor afterwards):

  | Section | Sample cold | Sample warm | Real cold | Real warm |
  |---|---|---|---|---|
  | Home | 6.06 s* | 0.40 s | 4.33 s | 0.47 s |
  | See it | 0.73 s | 0.19 s | 0.59 s | 0.24 s |
  | Plan it | 1.82 s | 0.31 s | 2.76 s | 0.39 s |
  | Requisition | 0.79 s | 0.19 s | 0.59 s | 0.23 s |
  | Match the sets | 0.85 s | 0.32 s | 1.13 s | 0.35 s |
  | About the data | 0.35 s | 0.02 s | 0.36 s | 0.02 s |
  | Your data | 0.47 s | 0.02 s | 0.35 s | 0.02 s |

  \* includes importing the app and building the sample. Every section is under 0.5 s warm. Plan it built its Excel
  file on every render; it is now built on click (0.59 s -> 0.39 s warm, real). Home's cold time is the lever
  replays (48 at ~57 ms, pandas-bound), Monday's list and the holdings; the planner was not reworked for it (the
  maths is reviewed and pinned). **Wake the app and open Home once before sending the link**: the caches are shared,
  so every later visitor gets the warm times.

- **Opus security review #2, 27 Nov** (ingest and upload, exports, caches, markdown; it ran proofs on small crafted
  files). **Must-fix, both fixed with tests (`tests/test_limits.py`):**
  1. Upload parsing blew a few KB up into GBs or minutes: a CSV with a 1,000-cell header (every row padded to it), an
     xlsx with one cell in column XFD per row (pandas pads every row to the widest), 48 MB of short lines (the
     delimiter guess split the whole file), many files read before the total-rows check, fuzzy matching of long
     headers on every click. Now: Excel read with openpyxl directly (read-only, values only), rows cut to
     `MAX_COLUMNS` (100) as they are read, one `MAX_CELLS` budget (4,000,000: Lumino's real export uses 1,650,404) for
     all files of an upload, `MAX_FILES` (12), the delimiter guessed from the top 64 KB, header names cut to 100
     characters and close spelling tried only on short ones, guesses worked out once per upload, a zip part over
     `MAX_ZIP_RATIO` (100x; real ones are ~7-8x) or over `MAX_SHARED_TEXT_MB` (40) of shared text refused,
     `MAX_UNPACKED_MB` down to 200. The real export still reads in ~3 s.
  2. A ~1 KB upload with dates in the years 1000 and 9999 built a ledger grid of billions of cells. Now
     `ingest.parse_dates` treats dates outside 1990-2100 as unreadable (left out, with a warning); the check fails a
     span over `ledger.MAX_MONTHS` (120) and a project over `MAX_ITEMS` (2,000), `MAX_GODOWNS` (100) or `MAX_GRID`
     (250,000 item-godown-months; Kanpur is ~4,000); `build_ledger` refuses the same before building its grid.

  **Should-fix, fixed:** the BOQ box label used `anchor_noun` from the data unescaped (now `md()`, and the hostile
  project sets it); `client.showErrorDetails = "none"` (no traceback or exception text reaches a visitor); an upload's
  results sat in the shared `st.cache_data` for up to 6 hours, against the page's "nothing is stored" (now
  `state.cached`, session only); the shared-strings bomb (above). **Notes, left as they are:** Streamlit's own
  dataframe toolbar "Download as CSV" does not neutralise formulas (only the visitor's own upload or the trusted
  bundle reach it); `md()` leaves bare URLs and line breaks (a `www.` link in a name becomes clickable; own session or
  bundle only); a `BundleError` quotes up to 40 characters of a table name from the secret; slider floats are not
  snapped to the step (a crafted value makes a new cache entry, bounded by `max_entries` and the TTL); a failed
  upload's log line may hold fragments of its values; `_unique` can still give two columns one name (the file is then
  refused with a vague message); Streamlit holds every uploaded file in memory while the uploader has it.
  **Checked and sound:** zip sizes and CRCs, defusedxml (billion laughs and external entities refused), the CSV field
  limit, every workbook and CSV download, static-only HTML, chart text, the secret (never printed, not in git
  history), no cross-session path (fingerprint keys, nothing mutates the shared datasets, Streamlit clamps widget
  values), deferred downloads fail with a generic error.

## Next
- **Phase 6: the private follow-up package** for Rohit, in `F:\ClacuttaAIClub_Hackathon\followup\`, never in this
  repo (see the plan's Phase 6 list), then the v2.0 release.

## Known quirks
- Streamlit `AppTest` cannot drive file uploads. Inject a dataset with `at.session_state["custom_ds"] = ds`, then
  pick it with `at.radio(key="dataset").set_value("custom").run()`. Setting the radio's value directly fails when its
  options change between runs. To test the Your data screen, put files already read in place:
  `at.session_state["data_sheets"] = {"sig": (), "sheets": upload.read_upload(name, data)}` (see `uploaded()` in
  `tests/test_app.py`).
- A real upload in the built-in browser: serve the files from a small local server that sends
  `Access-Control-Allow-Origin: http://localhost:8501`, then in the page `fetch` each one into a `File`, put them on
  `document.querySelector('input[type="file"]').files` through a `DataTransfer`, and dispatch a `change` event.
- On this Windows machine, a bash heredoc whose text contains `'''` breaks the shell. Write such scripts to a file
  first.
- The pytest config adds `-q`, so `pytest -q` prints no summary line; count the dots or use `-rA`.
- The local preview server does not always pick up edits to `ui/` or `core/` modules: stop and start it again before
  checking a change by eye.
- AppTest: a dataset injected as `custom_ds` has its results in `st.session_state["_upload_results"]`
  (`state.SESSION_RESULTS`), not in `st.cache_data`.
- `tests/test_hostile.py` walks the element protos AppTest holds (`at._tree`) for every string Streamlit renders as
  Markdown; if a Streamlit upgrade renames a proto type or field, check its `PLAIN` / `PLAIN_FIELDS` lists.
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
