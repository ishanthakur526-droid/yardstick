"""Requisition: type the BOQ, get the buy list (what to buy, and the last day to order it)."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core import export
from core import requisition as reqs
from core.dataset import Dataset
from core.fmt import month_label, short_item
from core.safe import csv_bytes, md
from ui.common import XLSX, qty
from ui.state import k


def example_quantity(ds: Dataset) -> int:
    """Where the BOQ box starts: the project's own example if it has one, else about a quarter of recent use."""
    if ds.cfg.requisition_example:
        return int(ds.cfg.requisition_example)
    site = ds.ledger.site
    recent = site[site["item_code"] == ds.cfg.anchor_code]["used"].tail(3).mean()
    return int(max(100, round(float(recent) * 3, -2))) if pd.notna(recent) and recent > 0 else 100


def render(ds: Dataset) -> None:
    ledger, cfg = ds.ledger, ds.cfg
    anchor = cfg.anchor_code if cfg.anchor_code in set(ledger.items["item_code"]) else None
    codes = [c for c in ledger.items["item_code"] if c != anchor]
    names = dict(zip(ledger.items["item_code"], ledger.items["item_name"]))
    noun = cfg.anchor_plural
    st.markdown(
        "**Type the BOQ, get the buy list.** Head-office procurement starts from the bill of quantities"
        + (f": how many {md(noun)}, plus any other lines the tender lists. " if anchor else ". ")
        + "Yardstick works out every part that is needed, takes away what is already in the stores, and lists what to "
        "buy and the last day to order each item so the deadline holds."
    )
    b1, b2, b3 = st.columns(3)
    anchors = 0
    if anchor:
        example = example_quantity(ds)
        anchors = b1.number_input(
            f"{noun.capitalize()} on the BOQ",
            min_value=0,
            max_value=5_000_000,
            value=example,
            step=100,
            key=k("boq_anchor"),
            help=f"{example:,} is an example to start from. Type the real number from the tender.",
        )
    days_left = b2.number_input(
        "Days until the material must be on site",
        min_value=7,
        max_value=720,
        value=90,
        step=7,
        key=k("boq_days"),
        help="Each item's usual lead time is taken off this to give its order-by date.",
    )
    handoff = b3.number_input(
        "Days procurement needs to place the order",
        min_value=0,
        max_value=30,
        value=7,
        step=1,
        key=k("boq_handoff"),
        help="Procurement teams typically need 7 to 10 days. The list must reach them this long before the "
        "order-by date.",
    )

    with st.expander(
        "More BOQ lines (optional): anything entered here is used as the requirement", expanded=not anchor
    ):
        grid = pd.DataFrame(
            {
                "Item": [short_item(names[c]) for c in codes],
                "Unit": [ledger.unit(c) for c in codes],
                "BOQ quantity": [None] * len(codes),
            }
        )
        lines_in = st.data_editor(
            grid,
            hide_index=True,
            disabled=["Item", "Unit"],
            key=k("boq_lines"),
            column_config={
                "BOQ quantity": st.column_config.NumberColumn(
                    min_value=0, help="Leave empty when the BOQ has no such line."
                )
            },
        )
        sheet = st.file_uploader(
            "…or upload the BOQ (CSV or Excel with an item column and a quantity column)",
            type=["csv", "xlsx"],
            key=k("boq_file"),
        )
        template = (
            ledger.items[["item_code", "item_name", "unit"]]
            .rename(columns={"item_code": "Item code", "item_name": "Item", "unit": "Unit"})
            .assign(Quantity="")
        )
        st.download_button(
            "BOQ template (CSV)",
            csv_bytes(template),
            file_name="yardstick_boq_template.csv",
            mime="text/csv",
        )

    boq = {anchor: float(anchors)} if anchor else {}
    boq.update({c: float(q) for c, q in zip(codes, lines_in["BOQ quantity"]) if pd.notna(q) and q > 0})
    if sheet is not None:
        from_sheet, unmatched, problem = _read_boq(sheet, ledger)
        if problem:
            st.error(f"Could not use {md(sheet.name)}: {md(problem)}.")
        else:
            boq.update(from_sheet)
            st.caption(
                f"Read {len(from_sheet)} lines from {md(sheet.name)}; they replace the lines above."
                + (f" Not recognised: {md(', '.join(unmatched[:5]))}." if unmatched else "")
            )

    with st.expander("How the parts are worked out"):
        basis = st.radio(
            "Parts that come with a set (eye hooks, lugs, bolts) use the ratio…",
            list(reqs.BASES),
            format_func=reqs.BASES.get,
            horizontal=True,
            key=k("boq_basis"),
            help="The master data file and the site's real use often disagree (see Match the sets). Both are shown "
            "below whichever you pick.",
        )
        per_anchor = st.checkbox(
            f"Estimate every other item from what this project used per {md(cfg.anchor_noun)}",
            value=bool(anchor),
            key=k("boq_per_anchor"),
            disabled=not anchor,
            help=f"Only for items the BOQ does not list, and only when {md(noun)} are on the BOQ.",
        )
        held = st.checkbox("Count stock still with contractors as available", value=False, key=k("boq_held"))

    req = reqs.requisition(
        ledger,
        ds.sets,
        boq,
        basis=basis,
        per_anchor=per_anchor,
        with_contractors=held,
        days_left=int(days_left),
        handoff_days=int(handoff),
        anchor_code=anchor,
        anchor_noun=cfg.anchor_noun,
    )
    stock_label = month_label(req.stock_month)
    if req.unknown:
        st.warning(f"Not items of this project, so left out: {md(', '.join(req.unknown))}.")
    if req.lines.empty:
        st.info("Enter the BOQ quantities above to see the buy list.")
    else:
        _show(req, ledger, stock_label, int(days_left), int(handoff), cfg.source)

    with st.expander("How this works, and what it assumes"):
        example_note = (
            f"The {example_quantity(ds):,} {md(noun)} in the box is an example figure, not a real BOQ, and open orders "
            "are not taken off the buy list."
            if anchor
            else "Open orders are not taken off the buy list."
        )
        st.markdown(
            "- **The BOQ is the requirement.** A line entered on the BOQ is used as it is. Everything else is worked out "
            "from it and labelled with where it came from: a set ratio (the file's, or the site's real use)"
            + (f", or an estimate from what this project used per {md(cfg.anchor_noun)}" if anchor else "")
            + ".\n"
            f"- **Stock in hand** is the last month in the data ({stock_label}): every godown plus any closing store. "
            "Stock still with contractors is left out unless you tick the box.\n"
            "- **Order by** = the day the material is needed minus the item's usual lead time. **Send to procurement by** "
            "= that day minus the days procurement needs to place an order. If that day has passed, the line is marked "
            "too late.\n"
            "- **No over-buying.** Only what is missing after stock is bought, so the buy list leaves nothing idle. The "
            "site's real use is the safer choice for parts that come with a set: the file's ratios can buy several times "
            "what the site ever used.\n"
            f"- **Not in the data:** a bill of quantities, prices, and orders still on the way. {example_note}\n"
            "- **Next:** read the BOQ straight from the ERP extract."
        )


def _read_boq(sheet, ledger) -> tuple[dict[str, float], list[str], str]:
    """The uploaded BOQ, read once per upload (not on every rerun) and kept for this session and project only."""
    sig = (sheet.file_id, sheet.name, sheet.size)
    memo = st.session_state.get(k("boq_read"))
    if memo is None or memo[0] != sig:
        memo = (sig, reqs.read_boq(sheet.name, sheet.getvalue(), ledger.items))
        st.session_state[k("boq_read")] = memo
    return memo[1]


def _show(req, ledger, stock_label: str, days_left: int, handoff: int, source: str) -> None:
    sm = reqs.summary(req)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Items needed", sm["lines"], border=True)
    m2.metric(
        "Already in stock",
        sm["covered"],
        border=True,
        help=f"Stock as at {stock_label}, the last month in the data.",
    )
    m3.metric("To buy", sm["to_buy"], border=True)
    m4.metric(
        "Too late for the deadline",
        sm["late"],
        border=True,
        help="Usual lead time, plus the days procurement needs to place the order, is longer than the days left.",
    )

    late = req.lines[req.lines["kind"] == "late"]
    soon = req.lines[req.lines["kind"] == "order"]
    if len(late):

        def why(r_) -> str:
            if r_.lead_days > days_left:
                return f"{r_.lead_days}-day lead time"
            return f"{r_.lead_days}-day lead time plus {handoff} days for procurement to place the order"

        st.warning(
            "**Cannot arrive in time:** "
            + "; ".join(f"{md(short_item(r_.item, 80))}: {why(r_)}" for r_ in late.itertuples())
            + f". Only {days_left} days are left. Ask the vendor to expedite, or move stock between godowns."
        )
    elif len(soon):
        first = soon.sort_values("order_by").iloc[0]
        when = first["send_by"] if handoff else first["order_by"]
        st.info(
            "Everything to buy can still arrive in time. "
            + (
                f"The first buy list has to reach procurement by **{when:%d %b}**"
                if handoff
                else f"The first order has to go out by **{when:%d %b}**"
            )
            + f", for {md(short_item(first['item'], 80))}."
        )
    else:
        st.success("The stores already cover this BOQ. Nothing to buy.")

    ln = req.lines
    dec = {c: ledger.decimals(c) for c in ln["item_code"]}
    st.dataframe(
        pd.DataFrame(
            {
                "Item": [short_item(i, 46) for i in ln["item"]],
                "Unit": ln["unit"],
                "Needed": [qty(v, dec[c]) for v, c in zip(ln["needed"], ln["item_code"])],
                "Where the number comes from": ln["source"],
                f"In stock ({stock_label})": [qty(v, dec[c]) for v, c in zip(ln["on_hand"], ln["item_code"])],
                "To buy": [qty(v, dec[c]) if v > 0 else "–" for v, c in zip(ln["buy"], ln["item_code"])],
                "Left in stock after": [qty(v, dec[c]) for v, c in zip(ln["left_over"], ln["item_code"])],
                "Order by / status": ln["status"],
            }
        ),
        hide_index=True,
    )

    derived = ln[ln["at_file"].notna()]
    if len(derived):
        st.subheader("Where the master data's ratios and the site disagree")
        st.caption(
            "Parts that come with a set are worked out from the parent's quantity. The master data file and what the "
            "site really used often differ, so both are shown. The BOQ line, when there is one, always wins."
        )
        st.dataframe(
            pd.DataFrame(
                {
                    "Item": [short_item(i, 46) for i in derived["item"]],
                    "Unit": derived["unit"],
                    "Needed at the file's ratio": [
                        qty(v, dec[c]) for v, c in zip(derived["at_file"], derived["item_code"])
                    ],
                    "Needed at the site's ratio": [
                        qty(v, dec[c]) for v, c in zip(derived["at_site"], derived["item_code"])
                    ],
                    "What it says": derived["check"],
                }
            ),
            hide_index=True,
        )

    st.download_button(
        "Download the buy list (Excel)",
        export.requisition_workbook(req, source=source),
        file_name="yardstick_buy_list.xlsx",
        mime=XLSX,
    )
