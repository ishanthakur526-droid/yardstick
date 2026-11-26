"""Yardstick: a stock and ordering planner that runs on what the site actually uses.

UI only. The numbers come from `core/`.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core import charts, explain, export
from core import requisition as reqs
from core import sets as setcheck
from core.bundle import load_tables
from core.fmt import indian, month_label, short_godown, short_item
from core.ledger import build_ledger, data_notes, item_grid, item_site, ledger_table, overview, peak_idle
from core.planner import MAX_SHORTAGE_SHARE, Settings, counts, replay

st.set_page_config(page_title="Yardstick · stock planner for Lumino", page_icon="📏", layout="wide")


@st.cache_resource(show_spinner="Reading the project's records…")
def get_ledger():
    tables = load_tables()
    if tables is None:
        return None
    return build_ledger(tables["flows"], tables["used"], tables["items"]), tables["sets"]


@st.cache_data(show_spinner=False)
def run_replay(_ledger, code: str, takeover: int, window: int, safety: float, capped: bool = True, basis: str = "use"):
    return replay(_ledger, code, Settings(takeover=takeover, window=window, safety=safety, capped=capped, basis=basis))


@st.cache_data(show_spinner="Replaying all 16 items month by month…")
def run_all(
    _ledger, takeover: int, window: int, safety: float, capped: bool = True, basis: str = "use"
) -> pd.DataFrame:
    rows = []
    for code in _ledger.items["item_code"]:
        r = run_replay(_ledger, code, takeover, window, safety, capped, basis)
        s = r.summary
        rows.append(
            {
                "code": code,
                "item": short_item(_ledger.item_name(code)),
                "unit": r.unit,
                "lead_days": int(_ledger.items.set_index("item_code").loc[code, "lead_time_days"]),
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
            }
        )
    return pd.DataFrame(rows)


def decimals_for(unit: str) -> int:
    return 2 if unit in {"MT", "KM"} else 0


def number_columns(df: pd.DataFrame, unit: str) -> dict:
    dec = decimals_for(unit)
    cfg = {}
    for col in df.columns:
        if col == "Month":
            continue
        cfg[col] = st.column_config.NumberColumn(format="%.1f" if col == "Months of cover" else f"%.{dec}f")
    return cfg


bundle = get_ledger()

st.markdown(
    "<h1 style='margin-bottom:0'>Yardstick</h1>"
    "<p style='font-size:1.15rem;opacity:.85;margin:.1rem 0 .2rem'>"
    "A stock and ordering planner that runs on what the site actually uses.</p>",
    unsafe_allow_html=True,
)

if bundle is None:
    st.error(
        "This copy of the app has no project data loaded, so there is nothing to show. "
        "The data is not stored in the public code; the host provides it as a secret."
    )
    st.stop()

ledger, sets_table = bundle
st.caption(
    f"Lumino Industries · one real project around Kanpur · {month_label(ledger.months[0])} to "
    f"{month_label(ledger.months[-1])} · {len(ledger.items)} items · {len(ledger.godowns) - 1} godowns · "
    "built at Vibe Coders Hackathon 3"
)

_head = run_all(ledger, Settings().takeover, Settings().window, Settings().safety)
_ok = _head[_head["counts"]]
if len(_ok):
    st.success(
        f"**{len(_ok)} of {len(_head)} items** could have run on a median **{_ok['cut'].median():.0%} less idle stock**, with under "
        f"{MAX_SHORTAGE_SHARE:.0%} of use left unsupplied, if the planner knew the project's total requirement and stock reached contractors as it was used. "
        "**Plan it** replays any item month by month and shows what the saving depends on; **Requisition** turns a new tender's BOQ into a buy list."
    )

section = st.segmented_control(
    "Section",
    ["See it", "Plan it", "Requisition", "Match the sets", "About the data"],
    default="See it",
    required=True,
    key="section",
    label_visibility="collapsed",
)

# --------------------------------------------------------------------------------------------
if section == "See it":
    names = dict(zip(ledger.items["item_code"], ledger.items["item_name"]))
    codes = list(ledger.items["item_code"])
    ov = overview(ledger)

    pick_a, pick_b = st.columns([3, 2])
    default_code = "MPO010014" if "MPO010014" in codes else codes[0]
    code = pick_a.selectbox(
        "Item",
        codes,
        index=codes.index(default_code),
        format_func=lambda c: f"{short_item(names[c])}  ·  {ledger.unit(c)}",
    )
    places = ["All godowns"] + [g for g in ledger.godowns]
    place = pick_b.selectbox("Godown", places, format_func=lambda g: g if g == "All godowns" else short_godown(g))

    unit = ledger.unit(code)
    site = item_site(ledger, code)
    row = ov[ov["item_code"] == code].iloc[0]
    peak_month, peak = peak_idle(ledger, code)
    at_peak = site[site["month"] == peak_month].iloc[0]
    cover = at_peak["cover_months"]

    cover_text = f", about {cover:.0f} months of cover" if pd.notna(cover) and cover >= 1 else ""
    st.info(
        f"**{short_item(names[code], 80)}.** At the peak ({month_label(peak_month)}), **{indian(peak)} {unit}** "
        f"were sitting idle ({indian(at_peak['godown_stock'])} in godowns, {indian(at_peak['with_contractors'])} with "
        f"contractors) while the site used **{indian(at_peak['used'])}** that month{cover_text}."
    )

    # Five tiles in a row: a seven-digit quantity plus its unit must not be cut off with "…" on a laptop screen.
    st.markdown(
        "<style>[data-testid='stMetricValue'], [data-testid='stMetricValue'] * {font-size: clamp(1.25rem, 1.75vw, 2.25rem) !important;}</style>",
        unsafe_allow_html=True,
    )
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Arrived from vendors", f"{indian(row['received'])} {unit}", border=True)
    k2.metric("Actually used", f"{indian(row['used'])} {unit}", border=True)
    last = site.iloc[-1]
    k3.metric(
        "Idle at project end",
        f"{indian(last['idle_total'])} {unit}",
        border=True,
        help=f"{indian(last['godown_stock'])} in godowns + {indian(last['with_contractors'])} with contractors",
    )
    k4.metric("Peak idle", f"{indian(peak)} {unit}", border=True, help=month_label(peak_month))
    k5.metric("Lead time", f"{int(row['lead_time_days'])} days", border=True)

    st.plotly_chart(charts.stock_story(site, unit))
    st.caption(
        "Contractor-held stock can only be tracked for the whole project: Lumino records usage per project, not per district."
    )

    st.subheader("Where it piled up")
    st.plotly_chart(charts.godown_heatmap(item_grid(ledger, code), unit))

    st.subheader("Month by month")
    godown = None if place == "All godowns" else place
    table = ledger_table(ledger, code, godown)
    st.dataframe(table, hide_index=True, column_config=number_columns(table, unit), height=420)
    st.download_button(
        "Download this ledger (CSV)",
        table.to_csv(index=False).encode("utf-8"),
        file_name=f"yardstick_{code}_{'all' if godown is None else short_godown(godown).lower()}.csv",
        mime="text/csv",
    )

    st.subheader("All 16 items at a glance")
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

# --------------------------------------------------------------------------------------------
if section == "Plan it":
    names = dict(zip(ledger.items["item_code"], ledger.items["item_name"]))
    codes = list(ledger.items["item_code"])
    st.markdown(
        "**Replay the project, month by month.** Each month the planner sees only what was known by then, and says what to "
        "order, hold or move. Then its plan is put next to what really happened: how much less stock would have sat idle, "
        "and any shortage it would have caused. A saving that starves the site does not count."
    )
    code = st.selectbox(
        "Item",
        codes,
        index=codes.index("MPO010014") if "MPO010014" in codes else 0,
        format_func=lambda c: f"{short_item(names[c])}  ·  {ledger.unit(c)}",
        key="plan_item",
    )
    with st.expander("Planner settings"):
        s1, s2, s3 = st.columns(3)
        takeover = s1.slider(
            "Months on the original plan first",
            3,
            18,
            9,
            help="A forecast needs history. Until then the project runs on what really arrived.",
        )
        window = s2.slider("Months of use the forecast averages", 3, 12, 6)
        safety = s3.slider(
            "Safety margin (standard deviations)",
            0.0,
            4.0,
            2.33,
            0.1,
            help="Higher means fewer shortages and more idle stock.",
        )

    with st.expander("Lumino keeps no spare stock. What would that cost on a continuous plan?"):
        trade = []
        for label, k in (
            ("No spare stock (0)", 0.0),
            ("Light cushion (1.0)", 1.0),
            (f"This screen's setting ({safety:.2f})", safety),
        ):
            d = run_all(ledger, takeover, window, k)
            ok_ = d[d["counts"]]
            trade.append(
                {
                    "Safety margin": label,
                    "Items that kept the site supplied": f"{len(ok_)} of {len(d)}",
                    "Median cut in idle stock": f"{ok_['cut'].median():.0%}" if len(ok_) else "–",
                    "Worst shortage (share of use)": f"{d['short'].max():.1%}",
                }
            )
        st.dataframe(pd.DataFrame(trade), hide_index=True)
        st.caption(
            "A plan that orders exactly the forecast runs short in every month that comes in above it, and use varies from month to month. The safety margin pays for "
            "that. When the quantity is fixed by a bill of quantities there is nothing to forecast, so no spare stock is the right rule: see **Requisition**."
        )

    r = run_replay(ledger, code, takeover, window, safety)
    s = r.summary
    unit = r.unit
    ok = counts(s)
    kind, message = explain.verdict(r, ok)
    getattr(st, kind)(message)

    st.caption(
        f"Scored from {month_label(s['eval_from'])}, the first month the planner's own orders could land (lead time {r.lead_months} months)."
    )
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric(
        "Average idle stock",
        f"{indian(s['avg_plan'])} {unit}",
        delta=f"{-s['reduction']:+.0%} vs {indian(s['avg_real'])} really",
        delta_color="inverse",
        border=True,
    )
    peak_change = (s["peak_plan"] / s["peak_real"] - 1) if s["peak_real"] > 0 else 0.0
    m2.metric(
        "Peak idle stock",
        f"{indian(s['peak_plan'])} {unit}",
        delta=f"{peak_change:+.0%} vs {indian(s['peak_real'])} really",
        delta_color="inverse",
        border=True,
    )
    m3.metric(
        "Use it could not supply",
        f"{s['shortage_pct']:.2%}",
        delta=f"{s['months_short']} months short",
        delta_color="off",
        border=True,
        help=f"Limit for the saving to count: {MAX_SHORTAGE_SHARE:.0%} of use.",
    )
    m4.metric(
        "Orders placed", f"{s['orders_n']}", delta=f"{indian(s['orders_qty'])} {unit}", delta_color="off", border=True
    )
    m5.metric(
        "Moves between godowns",
        f"{s['moves_n']}",
        delta=f"{indian(s['moves_qty'])} {unit}",
        delta_color="off",
        border=True,
        help=f"Stock moved instead of newly ordered. {s['moves_planned_n']} planned at the start of a month "
        f"({indian(s['moves_planned_qty'])} {unit}), {s['moves_n'] - s['moves_planned_n']} during the month to cover a godown that ran out.",
    )

    if pd.notna(s["forecast_error"]):
        st.caption(
            f"Forecast check: over the {s['span_months']} months an order has to cover, the forecast was off by {s['forecast_error']:.0%} of what the site "
            f"really used ({'high' if s['forecast_bias'] > 0 else 'low'} by {abs(s['forecast_bias']):.0%} overall). The site was fully supplied in "
            f"{s['months_served']} of {s['eval_months']} scored months."
        )
    st.plotly_chart(charts.replay_chart(r.monthly, r.settings.takeover, r.eval_start, unit))

    st.subheader("What the planner says, one month at a time")
    planning_months = [m for i, m in enumerate(r.months) if i >= r.settings.takeover]
    peak_month = s["peak_real_month"] if s["peak_real_month"] in planning_months else planning_months[0]
    month = st.select_slider(
        "Month", options=planning_months, value=peak_month, format_func=month_label, key="plan_month"
    )
    row = r.monthly[r.monthly["month"] == month].iloc[0]
    st.caption(
        f"In {month_label(month)} the project really received {indian(row['real_arrived'])} {unit} and the site used {indian(row['used'])}; "
        f"{indian(row['real_idle'])} {unit} sat idle at month end. The planner, knowing only the months before, would have ordered "
        f"{indian(row['plan_order'])} and ended with {indian(row['plan_idle'])} idle."
    )
    plan_now = explain.month_plan(r, month)
    cards = st.columns(2)
    for i, row_ in enumerate(plan_now.to_dict("records")):
        with cards[i % 2].container(border=True):
            st.markdown(f"**{row_['Godown']}**  ·  {row_['Action']}")
            st.caption(
                f"In stock {indian(row_['In stock'])} {unit}  ·  expects about {indian(row_['Forecast use / month'])} a month"
            )
            st.write(row_["Why"])
    d1, d2, _ = st.columns([1, 1, 3])
    d1.download_button(
        "Download this month's list (CSV)",
        plan_now.to_csv(index=False).encode("utf-8"),
        file_name=f"yardstick_{code}_{month}_plan.csv",
        mime="text/csv",
    )
    d2.download_button(
        "Download the plan (Excel)",
        export.workbook(r, month, short_item(names[code], 80), run_all(ledger, takeover, window, safety)),
        file_name=f"yardstick_{code}_{month}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    st.subheader("Month by month: the plan against what happened")
    table = pd.DataFrame(
        {
            "Month": r.monthly["month"],
            "Actually used": r.monthly["used"],
            "Really arrived": r.monthly["real_arrived"],
            "Really idle": r.monthly["real_idle"],
            "Planner ordered": r.monthly["plan_order"],
            "Plan: arrived": r.monthly["plan_arrived"],
            "Plan: idle": r.monthly["plan_idle"],
            "Plan: could not supply": r.monthly["plan_shortage"],
        }
    )
    st.dataframe(table, hide_index=True, column_config=number_columns(table, unit), height=360)
    st.download_button(
        "Download the replay (CSV)",
        table.to_csv(index=False).encode("utf-8"),
        file_name=f"yardstick_{code}_replay.csv",
        mime="text/csv",
    )

    st.subheader("All 16 items, same settings")
    everything = run_all(ledger, takeover, window, safety)
    n_ok = int(everything["counts"].sum())
    median_cut = float(everything.loc[everything["counts"], "cut"].median()) if n_ok else 0.0
    st.markdown(
        f"**{n_ok} of {len(everything)} items** kept the site supplied (under {MAX_SHORTAGE_SHARE:.0%} of use short). On those, the median "
        f"cut in average idle stock is **{median_cut:.0%}**."
    )
    board = everything.sort_values("cut", ascending=False)
    st.dataframe(
        pd.DataFrame(
            {
                "Item": board["item"],
                "Unit": board["unit"],
                "Lead time (days)": board["lead_days"],
                "Idle really (avg)": [indian(v) for v in board["avg_real"]],
                "Idle with planner (avg)": [indian(v) for v in board["avg_plan"]],
                "Cut": [f"{v:.0%}" for v in board["cut"]],
                "Peak really": [indian(v) for v in board["peak_real"]],
                "Peak with planner": [indian(v) for v in board["peak_plan"]],
                "Short": [f"{v:.1%}" for v in board["short"]],
                "Counts?": ["Yes" if c else "No: ran short" for c in board["counts"]],
            }
        ),
        hide_index=True,
    )

    st.subheader("What the saving depends on")
    st.markdown("The headline rests on two assumptions. Here is each one taken away, on all 16 items.")
    scenarios = (
        ("As above", True, "use"),
        ("The planner does not know the project's total requirement", False, "use"),
        ("Contractors keep drawing ahead of use, as they really did", True, "issues"),
        ("Both taken away", False, "issues"),
    )
    pole_rows, sens = {}, []
    for label, capped_, basis_ in scenarios:
        d_ = run_all(ledger, takeover, window, safety, capped_, basis_)
        ok_ = d_[d_["counts"]]
        p_ = d_[d_["code"] == reqs.POLE_CODE].iloc[0]
        pole_rows[(capped_, basis_)] = p_
        sens.append(
            {
                "Scenario": label,
                "Items that kept the site supplied": f"{len(ok_)} of {len(d_)}",
                "Median cut in idle stock": f"{ok_['cut'].median():.0%}" if len(ok_) else "–",
                "Poles: average idle stock, really → with the plan": f"{indian(p_['avg_real'])} → {indian(p_['avg_plan'])} ({-p_['cut']:+.0%})",
                "Poles the plan buys (really bought)": f"{indian(p_['plan_recv'])} ({indian(p_['real_recv'])})",
            }
        )
    st.dataframe(pd.DataFrame(sens), hide_index=True)
    held = pole_rows[(True, "use")]["avg_real"] - pole_rows[(True, "issues")]["avg_real"]
    st.caption(
        "The first row is the headline. It assumes (1) the planner knows the project's total requirement, as a bill of quantities would tell it (the data has none, "
        "so what the project really bought stands in), and (2) stock reaches contractors only as it is used. In reality contractors drew ahead of use: "
        f"on poles they held about {indian(held)} on average. In the last two rows they keep doing that, so only godown stock is compared. "
        "Take both assumptions away and, on poles, ordering by use alone no longer beats what happened."
    )

    st.subheader("How good is the forecast? All 16 items, on this project's own history")
    fc = everything.dropna(subset=["forecast_error"]).sort_values("forecast_error")
    if len(fc):
        served, scored = int(fc["months_served"].sum()), int(fc["eval_months"].sum())
        lean = float(fc["forecast_bias"].median())
        st.markdown(
            f"At the start of each scored month the planner forecasts use. That forecast is then checked against what the site really used over the months "
            f"an order has to cover (the lead time plus one). Across the {len(fc)} items it was off by a median **{fc['forecast_error'].median():.0%}** of real use "
            f"and ran {'high' if lean > 0 else 'low'} by a median **{abs(lean):.0%}**. It leans high on purpose: it takes the higher of the last 3 and last {window} "
            f"months' average, so one quiet month does not starve the site. With that cushion the site was fully supplied in **{served} of {scored}** item-months "
            f"({served / scored:.0%})."
        )
        st.dataframe(
            pd.DataFrame(
                {
                    "Item": fc["item"],
                    "Unit": fc["unit"],
                    "Months an order covers": fc["span"],
                    "Forecast off by (share of real use)": [f"{v:.0%}" for v in fc["forecast_error"]],
                    "Runs": [f"{'high' if b > 0 else 'low'} by {abs(b):.0%}" for b in fc["forecast_bias"]],
                    "Months fully supplied": [f"{a} of {b}" for a, b in zip(fc["months_served"], fc["eval_months"])],
                }
            ),
            hide_index=True,
        )
        st.caption(
            "Use on a construction project comes in lumps, so no forecast is exact month by month. The test that matters is the last column: "
            "the plan re-forecasts every month, and the site did not run short."
        )

    with st.expander("How this plan works, and what it assumes"):
        st.markdown(
            f"- **Orders.** Each month: forecast = the higher of the last 3 and last {window} months' average use. Cover = that forecast for the "
            f"lead time plus this month, plus {safety:.2f} standard deviations of recent use as a safety margin. The order is whatever is missing after "
            f"counting stock and orders already on the way. It never buys more in total than the project really bought "
            f"(the data has no bill of quantities).\n"
            f"- **Moves.** Stock above one godown's own need goes to a godown that would run short this month, before anything new is ordered. "
            f"Transfers are same-day in the records, so a move during the month is allowed too and is counted.\n"
            f"- **No peeking, with one stated exception.** The planner reads only months before the one it is deciding; the tests change the future and check nothing earlier moves. "
            f"The exception is the cap on total buying: it is what the project really bought in all, standing in for the bill of quantities Lumino would know at the start. "
            f"**What the saving depends on** shows the result without it.\n"
            f"- **First {takeover} months** run on what really arrived, because a forecast needs history. Orders landing within the first lead time after that "
            f"are inherited too. Scoring starts after that.\n"
            f"- **Use by godown is an estimate.** Lumino records use for the whole project only, so each month's use is split by what each godown handed out.\n"
            f"- **Contractor-held stock** is part of the real idle figure. In the plan, contractors draw what the site uses, so none is held back."
        )

# --------------------------------------------------------------------------------------------
if section == "Requisition":
    codes = [c for c in ledger.items["item_code"] if c != reqs.POLE_CODE]
    names = dict(zip(ledger.items["item_code"], ledger.items["item_name"]))
    st.markdown(
        "**Type the BOQ, get the buy list.** Head-office procurement starts from the bill of quantities: how many poles, plus any other lines "
        "the tender lists. Yardstick works out every part that is needed, takes away what is already in the stores, and lists what to buy and "
        "the last day to order each item so the deadline holds."
    )
    b1, b2, b3 = st.columns(3)
    poles = b1.number_input(
        "Poles on the BOQ",
        min_value=0,
        max_value=500000,
        value=1700,
        step=100,
        key="boq_poles",
        help="1,700 is an example, the size named in the problem statement. Type the real number from the tender.",
    )
    days_left = b2.number_input(
        "Days until the material must be on site",
        min_value=7,
        max_value=720,
        value=90,
        step=7,
        key="boq_days",
        help="Each item's usual lead time is taken off this to give its order-by date.",
    )
    handoff = b3.number_input(
        "Days procurement needs to place the order",
        min_value=0,
        max_value=30,
        value=7,
        step=1,
        key="boq_handoff",
        help="Lumino's procurement team asks for 7 to 10 days. The list must reach them this long before the order-by date.",
    )

    with st.expander("More BOQ lines (optional): anything entered here is used as the requirement"):
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
            key="boq_lines",
            column_config={
                "BOQ quantity": st.column_config.NumberColumn(
                    min_value=0, help="Leave empty when the BOQ has no such line."
                )
            },
        )
        sheet = st.file_uploader(
            "…or upload the BOQ (CSV or Excel with an item column and a quantity column)",
            type=["csv", "xlsx"],
            key="boq_file",
        )
        template = (
            ledger.items[["item_code", "item_name", "unit"]]
            .rename(columns={"item_code": "Item code", "item_name": "Item", "unit": "Unit"})
            .assign(Quantity="")
        )
        st.download_button(
            "BOQ template (CSV)",
            template.to_csv(index=False).encode("utf-8"),
            file_name="yardstick_boq_template.csv",
            mime="text/csv",
        )

    boq = {reqs.POLE_CODE: float(poles)}
    boq.update({c: float(q) for c, q in zip(codes, lines_in["BOQ quantity"]) if pd.notna(q) and q > 0})
    if sheet is not None:
        try:
            table = pd.read_csv(sheet) if sheet.name.lower().endswith(".csv") else pd.read_excel(sheet)
            from_sheet, unmatched = reqs.parse_boq(table, ledger.items)
            boq.update(from_sheet)
            st.caption(
                f"Read {len(from_sheet)} lines from {sheet.name}; they replace the lines above."
                + (f" Not recognised: {', '.join(unmatched[:5])}." if unmatched else "")
            )
        except Exception as err:  # a bad file must not break the page
            st.error(f"Could not read that file: {err}")

    with st.expander("How the parts are worked out"):
        basis = st.radio(
            "Parts that come with a set (eye hooks, lugs, bolts) use the ratio…",
            list(reqs.BASES),
            format_func=reqs.BASES.get,
            horizontal=True,
            key="boq_basis",
            help="Lumino's file and the site's real use often disagree (see Match the sets). Both are shown below whichever you pick.",
        )
        per_pole = st.checkbox(
            "Estimate every other item from what this project used per pole",
            value=True,
            key="boq_per_pole",
            help="Only for items the BOQ does not list, and only when poles are on the BOQ.",
        )
        held = st.checkbox("Count stock still with contractors as available", value=False, key="boq_held")

    req = reqs.requisition(
        ledger,
        sets_table,
        boq,
        basis=basis,
        per_pole=per_pole,
        with_contractors=held,
        days_left=int(days_left),
        handoff_days=int(handoff),
    )
    stock_label = month_label(req.stock_month)
    if req.unknown:
        st.warning(f"Not items of this project, so left out: {', '.join(req.unknown)}.")
    if req.lines.empty:
        st.info("Enter the poles on the BOQ, or add BOQ lines, to see the buy list.")
    else:
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
            why = lambda r_: (
                f"{r_.lead_days}-day lead time"
                if r_.lead_days > days_left
                else f"{r_.lead_days}-day lead time plus {int(handoff)} days for procurement to place the order"
            )
            st.warning(
                "**Cannot arrive in time:** "
                + "; ".join(f"{short_item(r_.item, 80)}: {why(r_)}" for r_ in late.itertuples())
                + f". Only {int(days_left)} days are left. Ask the vendor to expedite, or move stock between godowns."
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
                + f", for {short_item(first['item'], 80)}."
            )
        else:
            st.success("The stores already cover this BOQ. Nothing to buy.")

        def qty(v: float, unit: str) -> str:
            return indian(v, 2 if unit in ("MT", "KM") else 0)

        ln = req.lines
        st.dataframe(
            pd.DataFrame(
                {
                    "Item": [short_item(i, 46) for i in ln["item"]],
                    "Unit": ln["unit"],
                    "Needed": [qty(v, u) for v, u in zip(ln["needed"], ln["unit"])],
                    "Where the number comes from": ln["source"],
                    f"In stock ({stock_label})": [qty(v, u) for v, u in zip(ln["on_hand"], ln["unit"])],
                    "To buy": [qty(v, u) if v > 0 else "–" for v, u in zip(ln["buy"], ln["unit"])],
                    "Left in stock after": [qty(v, u) for v, u in zip(ln["left_over"], ln["unit"])],
                    "Order by / status": ln["status"],
                }
            ),
            hide_index=True,
        )

        derived = ln[ln["at_file"].notna()]
        if len(derived):
            st.subheader("Where Lumino's ratios and the site disagree")
            st.caption(
                "Parts that come with a set are worked out from the parent's quantity. Lumino's file and what the site really used often differ, so both "
                "are shown. The BOQ line, when there is one, always wins."
            )
            st.dataframe(
                pd.DataFrame(
                    {
                        "Item": [short_item(i, 46) for i in derived["item"]],
                        "Unit": derived["unit"],
                        "Needed at the file's ratio": [qty(v, u) for v, u in zip(derived["at_file"], derived["unit"])],
                        "Needed at the site's ratio": [qty(v, u) for v, u in zip(derived["at_site"], derived["unit"])],
                        "What it says": derived["check"],
                    }
                ),
                hide_index=True,
            )

        st.download_button(
            "Download the buy list (Excel)",
            export.requisition_workbook(req),
            file_name="yardstick_buy_list.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    with st.expander("How this works, and what it assumes"):
        st.markdown(
            f"- **The BOQ is the requirement.** A line entered on the BOQ is used as it is. Everything else is worked out from it and labelled with where it came from: "
            f"a set ratio (the file's, or the site's real use), or an estimate from what this project used per pole.\n"
            f"- **Stock in hand** is the last month in the data ({stock_label}): every godown plus the closing store. Stock still with contractors is left out unless you tick the box.\n"
            f"- **Order by** = the day the material is needed minus the item's usual lead time. **Send to procurement by** = that day minus the days procurement needs to place an order. If that day has passed, the line is marked too late.\n"
            f"- **No over-buying.** Only what is missing after stock is bought, so the buy list leaves nothing idle. The site's real use is the safer choice for parts that come with a set: the file's ratios can buy several times what the site ever used.\n"
            f"- **Not in the data:** a bill of quantities, prices, and orders still on the way. The 1,700 poles is an example figure, not a Lumino BOQ, and open orders are not taken off the buy list.\n"
            f"- **Next:** read the BOQ straight from Lumino's ERP extract, which their ERP team already updates every day."
        )


# --------------------------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def run_calibration(_ledger, sets_table: pd.DataFrame) -> pd.DataFrame:
    return setcheck.calibrate(_ledger, sets_table)


@st.cache_data(show_spinner=False)
def run_balance(_ledger, sets_table: pd.DataFrame, parent: str, basis: str, loose: bool):
    return setcheck.balance(_ledger, sets_table, parent, basis, judge=("strong", "loose") if loose else ("strong",))


if section == "Match the sets":
    cal = run_calibration(ledger, sets_table)
    n_pairs, n_agree = len(cal), int(cal["agrees"].sum())
    st.markdown(
        "**Some items only work as a set.** A pole clamp is no use without its eye hook and bolts; if one runs short, "
        "the whole set waits however much of the rest is lying in the store. This checks, godown by godown and month by month, "
        "whether the children went out in the ratio their parents needed."
    )
    st.warning(
        f"**Lumino's ratios agree with what the site really used for only {n_agree} of {n_pairs} pairs.** The data pack says to compare them "
        "with real use and ask. Below are both: the ratio as given, and the ratio the site's own use implies. Which one is right for each "
        "item is a question for Lumino's people."
    )
    ratio_table = pd.DataFrame(
        {
            "Set": cal["parent_name"].map(lambda n: short_item(n, 34)),
            "Child": cal["child_name"].map(lambda n: short_item(n, 40)),
            "Unit": cal["child_unit"],
            "Ratio as given": [f"{v:g}" for v in cal["stated"]],
            "Ratio from real use": [f"{v:.4g}" for v in cal["observed"]],
            "Off by": [
                f"{(f if f >= 1 else 1 / f):.1f}× " + ("too high" if f < 1 else "too low") if not a else "agrees"
                for f, a in zip(cal["factor"], cal["agrees"])
            ],
            "Follows its parent month to month": [
                f"{p} ({c:.2f})" if pd.notna(c) else "–" for p, c in zip(cal["pairing"], cal["monthly_correlation"])
            ],
        }
    )
    st.dataframe(ratio_table, hide_index=True)
    st.caption(
        "'Off by' says how far the stated ratio is from real use. 'Follows its parent' is how closely a child's monthly use tracks its parent's: "
        "only a child that tracks closely (0.8 or more) can be judged set by set."
    )

    parents = list(dict.fromkeys(cal["parent_code"]))
    pnames = dict(zip(cal["parent_code"], cal["parent_name"]))
    c1, c2, c3 = st.columns([3, 2, 2])
    parent = c1.selectbox("Set", parents, format_func=lambda p: short_item(pnames[p], 60), key="set_parent")
    basis_label = c2.radio("Ratio to use", ["From real use", "As Lumino gave it"], key="set_basis")
    loose = c3.checkbox(
        "Also judge loosely paired items",
        value=False,
        help="Children whose use only loosely follows the parent's. Expect many flags. Items that several sets share (the G.I. nuts and bolts) are never judged set by set.",
    )
    view = run_balance(ledger, sets_table, parent, "observed" if basis_label == "From real use" else "stated", loose)

    judged_children = [c for c, j in (view.judged or {}).items() if j]
    if not judged_children:
        st.info(
            "None of this set's children is issued set by set (their use does not follow the parent's closely enough), so the records cannot say "
            "whether this set is in balance. Ask Lumino how contractors draw them, or tick the box above to see what a loose reading gives."
        )
    else:
        flags = setcheck.flagged(view)
        cal_m = setcheck.calendar(view)
        n_bad = int((cal_m["status"] != "balanced").sum())
        k1, k2, k3 = st.columns(3)
        k1.metric(
            "Months out of balance",
            f"{n_bad} of {len(cal_m)}",
            border=True,
            help="At least one godown is off on at least one judged child.",
        )
        k2.metric(
            "Serious / watch",
            f"{int((flags['severity'] == 'serious').sum())} / {int((flags['severity'] == 'watch').sum())}",
            border=True,
            help=f"Godown-month accounts off by {setcheck.SERIOUS:.0%} / {setcheck.WATCH:.0%} of all sets issued so far.",
        )
        k3.metric("Judged children", ", ".join(short_item(view.names[c], 22) for c in judged_children), border=True)

        st.plotly_chart(charts.set_calendar(cal_m))
        st.caption(
            "Every month at least one godown's account is off on a judged child. Serious: off by 30% or more of all sets issued so far. Watch: 15% or more."
        )

        child = st.selectbox(
            "Child", judged_children, format_func=lambda c: short_item(view.names[c], 60), key="set_child"
        )
        st.plotly_chart(
            charts.set_account(
                view.account[view.account["child"] == child],
                short_item(view.names[child], 28),
                setcheck.WATCH,
                setcheck.SERIOUS,
            )
        )

        st.subheader("Every flag, worst first")
        if len(flags):
            shown = pd.DataFrame(
                {
                    "Month": flags["month"],
                    "Godown": flags["godown"].map(short_godown),
                    "Child": flags["child_name"].map(lambda n: short_item(n, 34)),
                    "Severity": flags["severity"],
                    "Off by": [f"{v:+.0%}" for v in flags["off"]],
                    "Why": flags["reason"],
                }
            )
            st.dataframe(
                shown, hide_index=True, height=320, column_config={"Why": st.column_config.TextColumn(width="large")}
            )
            st.download_button(
                "Download the flags (CSV)",
                shown.to_csv(index=False).encode("utf-8"),
                file_name=f"yardstick_sets_{parent}.csv",
                mime="text/csv",
            )
        else:
            st.success("No godown is out of balance on the judged children.")

        st.subheader("What is stuck, in one month")
        pick = st.select_slider(
            "Month",
            options=ledger.months,
            value=ledger.months[len(ledger.months) // 2],
            format_func=month_label,
            key="set_month",
        )
        snap = setcheck.stuck(view, pick)
        checked = [m for m in view.members if m == view.parent or (view.judged or {}).get(m)]
        st.dataframe(
            pd.DataFrame(
                {
                    "Godown": snap["godown"].map(short_godown),
                    "Sets that can be finished": [indian(v) for v in snap["sets_possible"]],
                    "Limited by": snap["limiting"].map(lambda m: short_item(view.names[m], 30)),
                    **{
                        f"{short_item(view.names[m], 22)}: on hand ({view.units[m]})": [
                            indian(v) for v in snap[f"have_{m}"]
                        ]
                        for m in checked
                    },
                    **{
                        f"{short_item(view.names[m], 22)}: waiting": [indian(v) for v in snap[f"waiting_{m}"]]
                        for m in checked
                    },
                }
            ),
            hide_index=True,
        )
        st.caption("'Waiting' is the stock of each member that cannot be used until the limiting one arrives.")

    with st.expander("How this works, and what it does not know"):
        st.markdown(
            "- **The account.** For each child at each godown: the sets' worth issued so far (child issued ÷ ratio) against the parents issued so far. "
            "Negative means the child is behind and sets wait for it; positive means the child is ahead and piling up.\n"
            "- **Ratios.** 'From real use' keeps the shape of Lumino's numbers and scales each child by one factor so the project's totals match what the "
            "site used. 'As Lumino gave it' uses the file's numbers untouched.\n"
            "- **Not every child is issued set by set.** Bolts, nuts and some lugs are handed out in bulk. Judging them month by month would flag almost everything, "
            "so by default only children that follow their parent closely are judged, and the rest are listed above for Lumino to explain.\n"
            "- **Contractor-held stock** is not split by godown in the records, so the account measures what left each godown, not what was installed."
        )

# --------------------------------------------------------------------------------------------
if section == "About the data":
    notes = data_notes(ledger)
    st.subheader("Where the numbers come from")
    st.markdown(
        "One real Lumino project around Kanpur, March 2023 to October 2026, shared by Calcutta AI Club for this hackathon. "
        "The data belongs to Lumino Industries Limited. It is not stored in this app's public code; the host supplies it as a secret."
    )
    st.markdown(
        "**How each number is built** (from the data pack's own rules)\n"
        "- *Idle in a godown* = receipts + transfers in − transfers out − issued to contractors + returned, added up from the start.\n"
        "- *Held by contractors* = issued − returned − used, added up from the start. Usage has no godown, so this is project-wide.\n"
        "- Rows that look identical are separate entries and are all counted. Rows with zero quantity add nothing.\n"
        "- Where a running total dips below zero (a late entry), it is shown as zero.\n"
        "- `KANPUR CLOSER` is the closing store that took leftover stock when the project wound up in 2026."
    )
    st.markdown(
        f"**Checks on this data**\n"
        f"- {notes['items']} items, {notes['locations']} locations (6 godowns and the closing store), {notes['months']}.\n"
        f"- {notes['godown_months_below_zero']} godown-months dip below zero from late entries (worst dip "
        f"{indian(notes['worst_dip'])}); they are shown as zero."
    )
