"""About the data: where the numbers come from, how each one is built, and what was assumed."""

from __future__ import annotations

import streamlit as st

from core.dataset import Dataset
from core.fmt import indian
from core.ledger import data_notes
from core.safe import md


def render(ds: Dataset) -> None:
    ledger, cfg = ds.ledger, ds.cfg
    notes = data_notes(ledger)
    st.subheader("Where the numbers come from")
    if cfg.about:
        st.markdown(md(cfg.about))
    elif cfg.source:
        st.markdown(f"Source: {md(cfg.source)}.")
    else:
        st.markdown("Data uploaded in this session. It is held only for this session and is not stored.")
    closing = list(ledger.closing)
    closing_line = (
        f"- {', '.join(f'**{md(c)}**' for c in closing)}: "
        + ("a closing store" if len(closing) == 1 else "closing stores")
        + " that only take leftover stock at wind-down, so the planner does not plan for "
        + ("it" if len(closing) == 1 else "them")
        + ".\n"
        if closing
        else ""
    )
    st.markdown(
        "**How each number is built**\n"
        "- *Idle in a godown* = receipts + transfers in − transfers out − issued to contractors + returned, added up "
        "from the start.\n"
        "- *Held by contractors* = issued − returned − used, added up from the start. Usage has no godown, so this is "
        "project-wide.\n"
        "- Rows that look identical are separate entries and are all counted. Rows with zero quantity add nothing.\n"
        "- Where a running total dips below zero (a late entry), it is shown as zero.\n" + closing_line
    )
    working = len(ledger.working)
    places = f"{working} godowns" + (
        f" and {len(closing)} closing store{'s' if len(closing) > 1 else ''}" if closing else ""
    )
    st.markdown(
        "**Checks on this data**\n"
        f"- {notes['items']} items, {places}, {notes['months']}.\n"
        f"- {notes['godown_months_below_zero']} godown-months dip below zero from late entries (worst dip "
        f"{indian(notes['worst_dip'])}); they are shown as zero."
    )
    if cfg.assumed:
        st.markdown("**Worked out from the data (please check)**\n" + "\n".join(f"- {md(n)}" for n in cfg.assumed))
    st.caption(
        "Yardstick started at Vibe Coders Hackathon 3 (Calcutta AI Club, 21 Nov 2026). No AI model sits in the "
        "numbers: every figure is computed by code and can be traced to the records."
    )
