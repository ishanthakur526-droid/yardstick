# HANDOVER

Last update: Thu 26 Nov 2026, ~19:45 IST. Phase 1 of the v2 rebuild is done and released as **v1.1** (on `main`).

The day-one hackathon build is preserved at tag `vch3-submission` (commit e1da49c) and in the frozen repo
`ishanthakur526-droid/vch3-yardstick`. **Never push to that repo.** This repo (`ishanthakur526-droid/yardstick`)
carries its history and is where all work continues.

## Where things stand
- **Branches:** work on `dev`, push often (CI runs ruff + pytest on Python 3.12 on every push). Fast-forward `main`
  only at the end of a green phase and tag it (`v1.1`, `v1.2`, ...). The Streamlit app deploys from `main`.
- **Live app:** not yet deployed from this repo (the owner deploys it; see "Deploy" below).
- **Tests:** 93, all green locally (real-project tests included); the real-project tests skip in CI.
- **What v1.1 is:** the day-one features (See it, Plan it, Requisition, Match the sets, About), now working on any
  project's data:
  - `core/config.py`: the anchor item (what a BOQ counts in), closing stores, set thresholds, labels, read from an
    optional `meta` table or worked out from the records (each guess shown as an assumption).
  - `core/ingest.py`: raw stores extract (5 CSVs; sets and transfers optional) to monthly tables, shared by the CLI
    and the app.
  - `core/dataset.py`: one project's data plus a fingerprint; every cached result is keyed by it.
  - `core/synth.py`: a made-up sample project (seed 7). It is the public demo when no real data is loaded and the
    CI dataset.
  - `ui/`: one module per screen; `app.py` is a thin router; the sidebar picks the project.
- **Results unchanged on the real project** (pinned by tests): 13 of 16 items, median 54% less idle stock; poles
  29,572 → 15,347 average idle (−48%) with both assumptions; the "What the saving depends on" table reproduces.
- **Sample project:** 9 of 9 items, median 44% less idle stock under the same two assumptions. Its dynamics were
  calibrated to the real project's measured profile before the planner was run on it (see the docstring); do not
  retune it to change the planner's score.

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

## Next: the v2 phases
- **Phase 2: the lever waterfall (the new Home screen).**
  - Lever 1, the BOQ cap: `simulate`'s `requirement` comes from a BOQ table when the data has one (the sample
    does). Otherwise the project's real total stands in (Kanpur), labelled as a stand-in. It can be switched off.
  - Lever 2, contractor cover of N weeks: a project-wide contractor pool `P` in `simulate`.
    - Godown demand = `use[t] + max(0, cover·f − (P − use[t]))`, then `P = max(P − use[t], cover·f)`, where
      `f` is the existing leak-free forecast and cover = weeks / 4.345 months.
    - Before takeover, demand is the real net issue and `P` the real contractor stock.
    - Score idle as godown stock + `P` in every mode.
    - `cover_weeks=None` keeps the real issues (today's `basis="issues"`). Prove `cover_weeks=0` equals today's
      `basis="use"` before removing the old switch, then migrate `test_godown_basis_compares_like_with_like` and
      `test_real_poles_saving_under_each_assumption`.
  - New `core/levers.py`: rows Actual → order by use → + BOQ cap → + contractor cover, each with average and peak
    idle (godowns + contractors), shortage %, and whether it counts.
  - Home also shows "what the stock report misses": contractor-held stock, set ratios that disagree with the site,
    and stock sent to the closing store. Plus a preview of this week's actions.
  - Fix: the five See it tiles cut off numbers at ~800 px width.
- **Phase 3: actions.**
  - Weekly action pack (xlsx): orders with order-by dates, transfer vouchers before new orders, contractor issue
    holds, set alerts, BOM fixes, assumptions. Use an as-of month selector, because a finished project is replayed.
  - BOM fix sheet; contractor holdings alerts; money layer (rates typed by the visitor plus cost of capital; nothing
    in rupees without rates).
  - Set-driven child demand with a leak-free bake-off: adopt per child only if it is no worse. Use expanding ratios
    `cumsum(child[:t]) / cumsum(parent[:t])`, never the full-period calibration.
  - Then an independent review of the lever maths.
- **Phase 4: data screen.** Upload the 5-CSV pack or one xlsx; a downloadable blank template; a Pass/Warn/Fail
  validator; a fuzzy column mapper (difflib plus synonyms). The uploaded dataset goes in
  `st.session_state["custom_ds"]`, which `ui/state.choose()` already offers in the sidebar.
- **Phase 5: hardening.**
  - Upload size and row caps, an xlsx zip-size check, read-only openpyxl, defusedxml (already pinned).
  - The BOQ upload in `ui/buylist.py` goes through the same safe reader.
  - Profile and keep each screen under 2 s warm; a security review.

## Known quirks
- Streamlit `AppTest` cannot drive file uploads. Inject a dataset with `at.session_state["custom_ds"] = ds`, then
  pick it with `at.radio(key="dataset").set_value("custom").run()`. Setting the radio's value directly fails when its
  options change between runs.
- On this Windows machine, a bash heredoc whose text contains `'''` breaks the shell. Write such scripts to a file
  first.
- The pytest config adds `-q`, so `pytest -q` prints no summary line; count the dots or use `-rA`.

## Deploy (owner's clicks, Streamlit Community Cloud, free)
1. share.streamlit.io → Create app → Deploy a public app from GitHub.
2. Repository `ishanthakur526-droid/yardstick`, branch `main`, main file `app.py`. App URL: pick a short subdomain.
3. Advanced settings: Python 3.12; Secrets: paste the one line from `data_local/streamlit_secrets.toml`.
4. Deploy. Then `python tests/smoke_live.py <url>` (needs Playwright, dev only).

## If Claude is unavailable
1. Open Antigravity (`%LOCALAPPDATA%\Programs\antigravity`), then File > Open Folder > `F:\ClacuttaAIClub_Hackathon\yardstick`.
2. Paste this prompt into its agent:

> You are continuing the Yardstick v2 rebuild in this folder (public repo `yardstick`). Read HANDOVER.md and README.md first. Hard rules: never commit keys, `.env`, `secrets.toml`, `data_local/` or any raw company data (CSV, XLSX, ZIP); never push to the old `vch3-yardstick` repo; work on `dev`, commit small and often with clear messages, and only fast-forward `main` when the tests pass; keep the planner free of look-ahead (add a scramble-the-future test for any new planning logic); a saving that causes over 1% shortage does not count; no paid services. Continue from "Next: the v2 phases", test with `pytest` and `streamlit run app.py`, and tell me exactly what to click for any deploy step.
