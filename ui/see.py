"""See it: month by month, per godown, what came in, went out, was used and sat idle."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core import charts
from core.dataset import Dataset
from core.fmt import indian, month_label, short_item
from core.ledger import item_grid, item_site, ledger_table, overview, peak_idle
from core.safe import csv_bytes, file_part, md
from ui.common import ALL_GODOWNS, number_columns, tiles
from ui.state import k


def render(ds: Dataset) -> None:
    ledger, cfg = ds.ledger, ds.cfg
    names = dict(zip(ledger.items["item_code"], ledger.items["item_name"]))
    codes = list(ledger.items["item_code"])
    ov = overview(ledger)

    pick_a, pick_b = st.columns([3, 2])
    default_code = cfg.anchor_code if cfg.anchor_code in codes else codes[0]
    code = pick_a.selectbox(
        "Item",
        codes,
        index=codes.index(default_code),
        format_func=lambda c: f"{short_item(names[c])}  ·  {ledger.unit(c)}",
        key=k("see_item"),
    )
    places = [ALL_GODOWNS] + list(ledger.godowns)
    place = pick_b.selectbox(
        "Godown", places, format_func=lambda g: g if g == ALL_GODOWNS else ledger.label(g), key=k("see_place")
    )

    unit, dec = ledger.unit(code), ledger.decimals(code)
    site = item_site(ledger, code)
    row = ov[ov["item_code"] == code].iloc[0]
    peak_month, peak = peak_idle(ledger, code)
    at_peak = site[site["month"] == peak_month].iloc[0]
    cover = at_peak["cover_months"]

    cover_text = f", about {cover:.0f} months of cover" if pd.notna(cover) and cover >= 1 else ""
    st.info(
        f"**{md(short_item(names[code], 80))}.** At the peak ({month_label(peak_month)}), **{indian(peak)} {md(unit)}** "
        f"were sitting idle ({indian(at_peak['godown_stock'])} in godowns, {indian(at_peak['with_contractors'])} with "
        f"contractors) while the site used **{indian(at_peak['used'])}** that month{cover_text}."
    )

    k1, k2, k3, k4, k5 = tiles("tiles_see")
    k1.metric("Arrived from vendors", f"{indian(row['received'])} {md(unit)}", border=True)
    k2.metric("Actually used", f"{indian(row['used'])} {md(unit)}", border=True)
    last = site.iloc[-1]
    k3.metric(
        "Idle at project end",
        f"{indian(last['idle_total'])} {md(unit)}",
        border=True,
        help=f"{indian(last['godown_stock'])} in godowns + {indian(last['with_contractors'])} with contractors",
    )
    k4.metric("Peak idle", f"{indian(peak)} {md(unit)}", border=True, help=month_label(peak_month))
    k5.metric("Lead time", f"{int(row['lead_time_days'])} days", border=True)

    st.plotly_chart(charts.stock_story(site, unit))
    st.caption(
        "Stock held by contractors can only be tracked for the whole project: usage is recorded per project, "
        "not per godown."
    )

    st.subheader("Where it piled up")
    st.plotly_chart(charts.godown_heatmap(item_grid(ledger, code), unit, ledger.labels))

    st.subheader("Month by month")
    godown = None if place == ALL_GODOWNS else place
    table = ledger_table(ledger, code, godown)
    st.dataframe(table, hide_index=True, column_config=number_columns(table, dec), height=420)
    st.download_button(
        "Download this ledger (CSV)",
        csv_bytes(table),
        file_name=f"yardstick_{file_part(code)}_{'all' if godown is None else file_part(ledger.label(godown))}.csv",
        mime="text/csv",
    )

    st.subheader(f"All {len(ov)} items at a glance")
    show = ov.sort_values("peak_idle", ascending=False)
    glance = pd.DataFrame(
        {
            "Item": show["item"].map(short_item),
            "Unit": show["unit"],
            "Lead time (days)": show["lead_time_days"],
            "Arrived": [indian(v) for v in show["received"]],
            "Used": [indian(v) for v in show["used"]],
            "Idle at project end": [indian(v) for v in show["idle_now"]],
            "Peak idle": [indian(v) for v in show["peak_idle"]],
            "Peak month": show["peak_month"].map(month_label),
            "Used in that month": [indian(v) for v in show["used_that_month"]],
        }
    )
    st.dataframe(glance, hide_index=True)
