"""Yardstick: what each stock decision on a project is worth, and Monday's list that follows from it.

UI only: this file picks the project and the section. The numbers come from `core/`, the screens from `ui/`.
"""

from __future__ import annotations

import logging

import streamlit as st

from core.fmt import month_label
from core.safe import md
from ui import about, buylist, home, plan, see, state
from ui import sets as sets_ui

st.set_page_config(page_title="Yardstick · stock and ordering planner", page_icon="📏", layout="wide")
log = logging.getLogger("yardstick")

ds = state.choose()
ledger, cfg = ds.ledger, ds.cfg

st.markdown(
    "<h1 style='margin-bottom:0'>Yardstick</h1>"
    "<p style='font-size:1.15rem;opacity:.85;margin:.1rem 0 .2rem'>"
    "What each stock decision is worth, replayed on the project's own records.</p>",
    unsafe_allow_html=True,
)
st.caption(
    f"{md(cfg.title)} · {month_label(ledger.months[0])} to {month_label(ledger.months[-1])} · "
    f"{len(ledger.items)} items · {len(ledger.working)} godowns" + (" · made-up sample data" if cfg.is_sample else "")
)

SECTIONS = {
    "Home": home.render,
    "See it": see.render,
    "Plan it": plan.render,
    "Requisition": buylist.render,
    "Match the sets": sets_ui.render,
    "About the data": about.render,
}
if ds.sets.empty:
    SECTIONS.pop("Match the sets")
if st.session_state.get("section") not in SECTIONS:
    st.session_state["section"] = "Home"
section = st.segmented_control("Section", list(SECTIONS), required=True, key="section", label_visibility="collapsed")

try:
    SECTIONS[section](ds)
except Exception:  # noqa: BLE001 - one section failing must not take the page down; the details go to the log
    log.exception("section %r failed on project %s", section, ds.fingerprint)
    st.error(
        "This section hit a problem with this project's data, so it cannot be shown. The other sections still work. "
        "If the data was uploaded, check it on the Data screen."
    )
