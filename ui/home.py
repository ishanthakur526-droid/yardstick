"""Home: what each decision would have been worth, what the stock report misses, and Monday's list."""

from __future__ import annotations

import math

import pandas as pd
import streamlit as st

from core import charts, levers
from core.dataset import Dataset
from core.fmt import indian, month_label, short_item
from core.misses import closing_store, contractor_stock, contractor_stock_all, ratio_gaps
from core.planner import DEFAULT_COVER_WEEKS, MAX_SHORTAGE_SHARE, Settings
from core.safe import csv_bytes, md
from ui.common import lever_rows_table
from ui.state import k, planner_limits, run_calibration, run_levers, run_week


def render(ds: Dataset) -> None:
    ledger, cfg, fp = ds.ledger, ds.cfg, ds.fingerprint
    codes = list(ledger.items["item_code"])
    names = dict(zip(ledger.items["item_code"], ledger.items["item_name"]))
    anchor = cfg.anchor_code if cfg.anchor_code in codes else codes[0]
    _, _, takeover = planner_limits(ds)
    base = Settings(takeover=takeover)

    st.markdown(
        "**What each decision would have been worth.** Yardstick replays this project month by month. Each month the "
        "planner sees only what was known by then. Each bar below switches on one more decision the company controls, "
        "so the step from one bar to the next is what that decision is worth. A saving that leaves the site short does "
        "not count."
    )
    c1, c2, c3 = st.columns([3, 2, 1.5], vertical_alignment="bottom")
    code = c1.selectbox(
        "Item",
        codes,
        index=codes.index(anchor),
        format_func=lambda c: f"{short_item(names[c])}  ·  {ledger.unit(c)}",
        key=k("home_item"),
    )
    weeks = c2.slider(
        "Contractor cover (weeks)",
        0,
        8,
        int(DEFAULT_COVER_WEEKS),
        key=k("home_cover"),
        help="How much forecast use contractors may hold at month end. 0: they get exactly what the site uses.",
    )
    boq_cap = c3.toggle(
        "BOQ cap",
        value=True,
        key=k("home_boq"),
        help="Never order more in total than the bill of quantities.",
    )
    tables = run_levers(fp, ds, base.takeover, base.window, base.safety, float(weeks), boq_cap)
    rows = tables[code]
    unit, dec = ledger.unit(code), ledger.decimals(code)
    cap_source = rows["cap_source"].iloc[-1]
    if cap_source == "stand-in":
        rows = rows.assign(label=rows["label"].str.replace("+ BOQ cap", "+ BOQ cap (stand-in)", regex=False))

    _headline(rows, unit, dec, short_item(names[code], 60))
    st.plotly_chart(charts.lever_waterfall(rows, unit, dec), config={"displayModeBar": False})
    st.dataframe(lever_rows_table(rows, dec), hide_index=True)
    if cap_source == "stand-in":
        st.caption(
            f"This project's data has no bill of quantities, so what it really bought "
            f"({indian(rows['bought'].iloc[0], dec)} {md(unit)}) stands in for the BOQ. It is the one number the planner "
            "knows from the future; a real project knows its BOQ at the start."
        )
    _how_waterfall(weeks, base)

    st.subheader(f"All {len(codes)} items, same levers")
    _portfolio(tables)

    st.subheader("What the stock report misses")
    _misses(ds, anchor)

    st.subheader("Monday's list")
    _monday(ds, rows, base.takeover, float(weeks), boq_cap)


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _headline(rows: pd.DataFrame, unit: str, dec: int, name: str) -> None:
    first, last = rows.iloc[0], rows.iloc[-1]
    if last["counts"]:
        st.success(
            f"**{md(name)}:** average idle stock **{indian(first['avg_idle'], dec)} → {indian(last['avg_idle'], dec)} "
            f"{md(unit)}** ({-last['cut']:+.0%}) with every lever on, and the site kept supplied."
        )
    else:
        st.warning(
            f"**{md(name)}:** with every lever on, the plan leaves {last['shortage_pct']:.1%} of demand unsupplied, so its "
            "saving does not count. Allow contractors more cover."
        )


def _how_waterfall(weeks: int, base: Settings) -> None:
    with st.expander("How these numbers are computed"):
        st.markdown(
            "- **Actual:** stock in the godowns (receipts + transfers in − transfers out − issues + returns, running "
            "total) plus stock with contractors (issues − returns − recorded use, running total), from the records.\n"
            "- **Order by use:** the planner orders each month on a forecast of what the godowns hand out (the higher of "
            f"the last 3 and last {base.window} months' average), for the lead time plus this month, plus a safety margin "
            f"of {base.safety:g} standard deviations. It moves stock between godowns before ordering. No cap on total "
            "buying. Contractors draw what they really drew and return what they really returned.\n"
            "- **+ BOQ cap:** the same, never ordering more in total than the bill of quantities.\n"
            f"- **+ contractor cover:** contractors are issued only enough to end each month holding {weeks} weeks of "
            "forecast use: issue = use + cover − what they already hold. A godown shortfall eats into that cover before "
            "it stops the site.\n"
            f"- **Scoring:** the first {base.takeover} months run on what really arrived (a forecast needs history), and "
            "scoring starts once the planner's own orders can land (one lead time later). Average and peak idle are "
            "month-end stock over those months, godowns plus contractors, in every bar. "
            f"{levers.SHORTAGE_RULE} Until the cover bar, demand is what contractors drew; from it, what the site used.\n"
            "- **No peeking:** at each month the planner reads only earlier months. Tests scramble the future and check "
            "that no earlier decision moves."
        )


def _portfolio(tables: dict[str, pd.DataFrame]) -> None:
    p = levers.portfolio(tables)
    st.dataframe(
        pd.DataFrame(
            {
                "Step": p["label"],
                "Items whose saving counts": [f"{a} of {b}" for a, b in zip(p["items_counting"], p["items"])],
                "Median cut in average idle (those items)": [
                    "–" if i == 0 or math.isnan(v) else f"{v:.0%}" for i, v in enumerate(p["median_cut"])
                ],
                "Worst shortage": ["–" if i == 0 else f"{v:.1%}" for i, v in enumerate(p["worst_shortage"])],
            }
        ),
        hide_index=True,
    )
    st.caption(
        f"Each item is replayed on its own, with the same levers. Units differ, so quantities are not added across items: "
        f"the table counts the items whose plan left no more than {MAX_SHORTAGE_SHARE:.0%} of demand unsupplied, and "
        "gives the median cut in average idle stock among them. A negative cut means more idle stock than really sat."
    )


def _misses(ds: Dataset, anchor: str) -> None:
    ledger, cfg = ds.ledger, ds.cfg
    unit, dec = ledger.unit(anchor), ledger.decimals(anchor)
    noun = cfg.anchor_plural if cfg.anchor_code == anchor else unit
    a, b, c = st.columns(3)

    with a.container(border=True):
        st.markdown("**Stock with contractors**")
        held = contractor_stock(ledger, anchor)
        every = contractor_stock_all(ledger)
        many = int((every["avg_months_of_use"] > 1).sum())
        st.markdown(
            f"On average **{indian(held['avg_held'], dec)} {md(noun)}** sat with contractors: "
            f"{held['share_of_idle']:.0%} of all idle {md(noun)}. At the peak, {month_label(held['peak_month'])}, "
            f"{indian(held['peak_held'], dec)}"
            + (
                f", about {held['peak_months_of_use']:.1f} months of use."
                if not math.isnan(held["peak_months_of_use"])
                else "."
            )
        )
        st.caption(
            f"In {many} of {len(every)} items contractors held more than a month of use on average. A godown stock "
            "report counts issued stock as gone. How: issues − returns − recorded use, running total, averaged over "
            f"all {held['months']} months."
        )

    with b.container(border=True):
        st.markdown("**Set ratios in the master data**")
        if ds.sets.empty:
            st.markdown("This project's data has no sets table, so its parts-per-set ratios cannot be checked.")
        else:
            gaps = ratio_gaps(run_calibration(ds.fingerprint, ds))
            w = gaps["worst"]
            text = f"**{gaps['disagree']} of {gaps['pairs']}** stated ratios disagree with what the site used."
            if w:
                text += (
                    f" Furthest off: {md(short_item(w['child_name'], 40))} per {md(short_item(w['parent_name'], 40))}: "
                    f"the file says {w['stated']:g}, the site used about {w['observed']:.2f} ({w['factor']:.0f}×)."
                )
            if gaps["unit_suspect"]:
                text += (
                    f" Of those, {gaps['unit_suspect']} look{'s' if gaps['unit_suspect'] == 1 else ''} like a unit "
                    "mismatch (the part counted in another unit) rather than a wrong count."
                )
            st.markdown(text)
            st.caption(
                "How: for each child, what the site used against what the stated ratios predict from its parents' use; "
                "inside 0.75× to 1.33× counts as agreeing. **Match the sets** shows every pair."
            )

    with c.container(border=True):
        st.markdown("**Sent to the closing store**")
        cl = closing_store(ledger)
        if cl.empty:
            st.markdown("No closing store in this project's data.")
        else:
            row = cl[cl["item_code"] == anchor].iloc[0]
            sent = cl[cl["sent"] > 0]
            top = sent.sort_values("share", ascending=False).iloc[0] if len(sent) else None
            text = (
                f"**{indian(row['sent'], dec)} {md(noun)}** went to the closing store at wind-down: "
                f"{row['share']:.1%} of all received."
                if row["sent"] > 0
                else f"No {md(noun)} went to the closing store."
            )
            if top is not None:
                text += (
                    f" {len(sent)} of {len(cl)} items were sent there, up to {top['share']:.0%} of what was received "
                    f"({md(short_item(ledger.item_name(top['item_code']), 40))})."
                )
            st.markdown(text)
            st.caption(
                "How: transfers into the closing store, against everything received from vendors. Bought for this "
                "project and never used on it."
            )


def _monday(ds: Dataset, rows: pd.DataFrame, takeover: int, weeks: float, boq_cap: bool) -> None:
    ledger = ds.ledger
    months = ledger.months[takeover:] or ledger.months[-1:]
    default = rows["peak_month"].iloc[0] if rows["peak_month"].iloc[0] in months else months[0]
    st.markdown(
        "The project in the data is finished, so pick a month: this is the list procurement and stores would have had "
        "on its first Monday, from the records as they stood then."
    )
    month = (
        st.select_slider("As of", options=months, value=default, format_func=month_label, key=k("home_asof"))
        if len(months) > 1
        else months[0]
    )
    week = run_week(ds.fingerprint, ds, month, takeover, weeks, boq_cap)
    if week.empty:
        st.info("Nothing to do that Monday: every godown covers its need and contractors hold no more than allowed.")
        return
    n = week["kind"].value_counts()
    st.markdown(
        f"**{plural(n.get('Move', 0), 'transfer')}, {plural(n.get('Order', 0), 'order')}, "
        f"{plural(n.get('Hold issues', 0), 'issue hold')}.** "
        "Transfers come first: stock already bought moves before anything new is ordered."
    )
    decimals = dict(zip(ledger.items["item_code"], ledger.items["decimals"]))
    show = pd.DataFrame(
        {
            "Item": week["item"].map(lambda s: short_item(s, 48)),
            "Action": week["kind"],
            "Quantity": [
                f"{indian(q, int(decimals[c]))} {u}" for q, c, u in zip(week["qty"], week["item_code"], week["unit"])
            ],
            "Detail": week["detail"],
            "Why": week["why"],
        }
    )
    st.dataframe(show, hide_index=True, height=min(420, 38 + 35 * len(show)))
    st.download_button(
        "Download Monday's list (CSV)", csv_bytes(show), file_name=f"yardstick_monday_{month}.csv", mime="text/csv"
    )
    with st.expander("How this list is computed"):
        st.markdown(
            f"- **Stock** is each godown's real stock at the end of the month before, plus what lands this month. "
            "Deliveries due within one lead time count as already on order.\n"
            "- **Demand** is forecast from the months before only: the site's use, spread over the godowns by what each "
            "handed out.\n"
            "- **Transfers:** a godown below this month's need takes stock from one above its own lead-time need.\n"
            "- **Orders:** what is missing for the lead time plus this month, with the safety margin, never past the cap. "
            "Contractors' stock beyond their allowed cover counts first, because it is used before anything new is "
            "issued.\n"
            f"- **Issue holds:** contractors hold more than {weeks:g} weeks of forecast use. The quantity is the excess."
        )
