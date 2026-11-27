"""Which project the app is showing, and the cached sums.

Every cached result is keyed by the data's fingerprint (hashed); the data itself is passed unhashed. So results for one
project are never served for another, and nothing a user uploads ever lands in a cache other sessions share.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core import actions as monday_list
from core import ingest, levers, synth
from core import sets as setcheck
from core.bundle import BundleError, load_tables
from core.dataset import Dataset, from_tables
from core.fmt import short_item
from core.planner import DEFAULT_COVER_WEEKS, Settings, counts, replay

BUNDLE, SAMPLE, CUSTOM = "bundle", "sample", "custom"
WIDGET_PREFIXES = ("home_", "see_", "plan_", "boq_", "set_")  # per-project choices, cleared when the project changes
TTL = 6 * 3600


@st.cache_resource(show_spinner="Reading the project's records…")
def bundled() -> tuple[Dataset | None, str]:
    """The project the host supplies as a secret, if any (shared by every visitor: it is the same for all of them)."""
    try:
        tables = load_tables()
        return (from_tables(tables, key=BUNDLE), "") if tables is not None else (None, "")
    except (BundleError, KeyError, ValueError) as err:
        return None, f"The project data supplied with this app could not be read ({err}). Showing the sample project."


@st.cache_resource(show_spinner="Building the sample project…")
def sample() -> Dataset:
    return from_tables(ingest.monthly_tables(synth.make_pack()), key=SAMPLE)


def choose() -> Dataset:
    """The project picker in the sidebar. The host's project comes first when there is one."""
    real, problem = bundled()
    options: dict[str, str] = {}
    if real is not None:
        options[BUNDLE] = real.cfg.title
    options[SAMPLE] = "Sample project (made-up data)"
    custom = st.session_state.get("custom_ds")
    if isinstance(custom, Dataset):
        options[CUSTOM] = f"Your data: {custom.cfg.project}"
    if st.session_state.get("dataset") not in options:
        st.session_state["dataset"] = next(iter(options))
    with st.sidebar:
        choice = st.radio("Project data", list(options), format_func=options.get, key="dataset")
        if problem:
            st.warning(problem)
    ds = real if choice == BUNDLE else (custom if choice == CUSTOM else sample())
    if st.session_state.get("_shown") != ds.fingerprint:  # a different project: forget choices made on the last one
        for key in [k for k in st.session_state if str(k).startswith(WIDGET_PREFIXES)]:
            del st.session_state[key]
        st.session_state["_shown"] = ds.fingerprint
    return ds


def k(name: str) -> str:
    """A widget key for one project. Each project gets its own widgets, so a choice list from the last project is never
    left on screen after switching (the browser would keep showing the old label). Old keys share the name's prefix and
    are cleared by `choose()`."""
    return f"{name}@{str(st.session_state.get('_shown', ''))[:8]}"


def planner_limits(ds: Dataset) -> tuple[int, int, int]:
    """Bounds for the 'months on the original plan' slider: a forecast needs history, and a score needs months after."""
    n = len(ds.ledger.months)
    top = max(3, min(18, n - 4))
    return 3, top, min(Settings().takeover, top)


@st.cache_data(show_spinner=False, max_entries=512, ttl=TTL)
def run_replay(
    fp: str,
    _ds: Dataset,
    code: str,
    takeover: int,
    window: int,
    safety: float,
    capped: bool = True,
    cover_weeks: float | None = DEFAULT_COVER_WEEKS,
):
    settings = Settings(takeover=takeover, window=window, safety=safety, capped=capped, cover_weeks=cover_weeks)
    return replay(_ds.ledger, code, settings, boq=_ds.boq_qty(code))


@st.cache_data(show_spinner="Replaying every item month by month…", max_entries=64, ttl=TTL)
def run_all(
    fp: str,
    _ds: Dataset,
    takeover: int,
    window: int,
    safety: float,
    capped: bool = True,
    cover_weeks: float | None = DEFAULT_COVER_WEEKS,
) -> pd.DataFrame:
    ledger = _ds.ledger
    rows = []
    for code in ledger.items["item_code"]:
        r = run_replay(fp, _ds, code, takeover, window, safety, capped, cover_weeks)
        s = r.summary
        rows.append(
            {
                "code": code,
                "item": short_item(ledger.item_name(code)),
                "unit": r.unit,
                "lead_days": int(ledger.lead_days(code)),
                "avg_real": s["avg_real"],
                "avg_plan": s["avg_plan"],
                "cut": s["reduction"],
                "peak_real": s["peak_real"],
                "peak_plan": s["peak_plan"],
                "short": s["shortage_pct"],
                "orders": s["orders_n"],
                "counts": counts(s),
                "forecast_error": s["forecast_error"],
                "forecast_bias": s["forecast_bias"],
                "span": s["span_months"],
                "months_served": s["months_served"],
                "eval_months": s["eval_months"],
                "plan_recv": s["plan_received"],
                "real_recv": s["real_received"],
                "cap_source": s["cap_source"],
            }
        )
    return pd.DataFrame(rows)


@st.cache_data(show_spinner="Replaying each lever on every item…", max_entries=64, ttl=TTL)
def run_levers(
    fp: str, _ds: Dataset, takeover: int, window: int, safety: float, cover_weeks: float, boq_cap: bool
) -> dict[str, pd.DataFrame]:
    """The lever waterfall for every item, built from the cached replays."""
    plan = levers.steps(Settings(takeover=takeover, window=window, safety=safety), cover_weeks, boq_cap)
    out = {}
    for code in _ds.ledger.items["item_code"]:
        out[code] = levers.rows(
            lambda s, c=code: run_replay(fp, _ds, c, s.takeover, s.window, s.safety, s.capped, s.cover_weeks), plan
        )
    return out


@st.cache_data(show_spinner="Building Monday's list…", max_entries=64, ttl=TTL)
def run_week(
    fp: str, _ds: Dataset, month: str, takeover: int, cover_weeks: float | None, boq_cap: bool
) -> pd.DataFrame:
    settings = Settings(takeover=takeover, cover_weeks=cover_weeks, capped=boq_cap)
    boq = {c: _ds.boq_qty(c) for c in _ds.ledger.items["item_code"]}
    return monday_list.week(_ds.ledger, month, settings, boq, _ds.sets)


@st.cache_data(show_spinner=False, max_entries=32, ttl=TTL)
def run_calibration(fp: str, _ds: Dataset) -> pd.DataFrame:
    return setcheck.calibrate(_ds.ledger, _ds.sets)


@st.cache_data(show_spinner=False, max_entries=64, ttl=TTL)
def run_balance(fp: str, _ds: Dataset, parent: str, basis: str, loose: bool):
    return setcheck.balance(
        _ds.ledger,
        _ds.sets,
        parent,
        basis,
        judge=("strong", "loose") if loose else ("strong",),
        min_parents=_ds.cfg.min_parents,
        min_sets=_ds.cfg.min_sets,
        cal=run_calibration(fp, _ds),
    )
