"""Plan it: replay the project month by month, deciding only from what was known at the time."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core import charts, explain, export
from core.dataset import Dataset
from core.fmt import indian, month_label, short_item
from core.planner import MAX_SHORTAGE_SHARE, Settings, counts
from core.safe import csv_bytes, file_part, md
from ui.common import XLSX, number_columns
from ui.state import planner_limits, run_all, run_replay


def render(ds: Dataset) -> None:
    ledger, cfg, fp = ds.ledger, ds.cfg, ds.fingerprint
    names = dict(zip(ledger.items["item_code"], ledger.items["item_name"]))
    codes = list(ledger.items["item_code"])
    n_items = len(codes)
    st.markdown(
        "**Replay the project, month by month.** Each month the planner sees only what was known by then, and says what to "
        "order, hold or move. Then its plan is put next to what really happened: how much less stock would have sat idle, "
        "and any shortage it would have caused. A saving that starves the site does not count."
    )
    code = st.selectbox(
        "Item",
        codes,
        index=codes.index(cfg.anchor_code) if cfg.anchor_code in codes else 0,
        format_func=lambda c: f"{short_item(names[c])}  ·  {ledger.unit(c)}",
        key="plan_item",
    )
    low, high, start = planner_limits(ds)
    with st.expander("Planner settings"):
        s1, s2, s3 = st.columns(3)
        if high > low:
            takeover = s1.slider(
                "Months on the original plan first",
                low,
                high,
                start,
                help="A forecast needs history. Until then the project runs on what really arrived.",
                key="plan_takeover",
            )
        else:
            takeover = start
            s1.caption(f"The project is short, so the planner takes over after {takeover} months.")
        window = s2.slider("Months of use the forecast averages", 3, 12, Settings().window, key="plan_window")
        safety = s3.slider(
            "Safety margin (standard deviations)",
            0.0,
            4.0,
            Settings().safety,
            0.1,
            help="Higher means fewer shortages and more idle stock.",
            key="plan_safety",
        )

    with st.expander("No spare stock: what would that cost on a continuous plan?"):
        trade = []
        for label, k in (
            ("No spare stock (0)", 0.0),
            ("Light cushion (1.0)", 1.0),
            (f"This screen's setting ({safety:.2f})", safety),
        ):
            d = run_all(fp, ds, takeover, window, k)
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
            "A plan that orders exactly the forecast runs short in every month that comes in above it, and use varies "
            "from month to month. The safety margin pays for that. When the quantity is fixed by a bill of quantities "
            "there is nothing to forecast, so no spare stock is the right rule: see **Requisition**."
        )

    r = run_replay(fp, ds, code, takeover, window, safety)
    s = r.summary
    unit, dec = r.unit, ledger.decimals(code)
    ok = counts(s)
    kind, message = explain.verdict(r, ok)
    getattr(st, kind)(md(message))

    st.caption(
        f"Scored from {month_label(s['eval_from'])}, the first month the planner's own orders could land "
        f"(lead time {r.lead_months} months)."
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
        f"({indian(s['moves_planned_qty'])} {md(unit)}), {s['moves_n'] - s['moves_planned_n']} during the month to "
        "cover a godown that ran out.",
    )

    if pd.notna(s["forecast_error"]):
        st.caption(
            f"Forecast check: over the {s['span_months']} months an order has to cover, the forecast was off by "
            f"{s['forecast_error']:.0%} of what the site really used ({'high' if s['forecast_bias'] > 0 else 'low'} by "
            f"{abs(s['forecast_bias']):.0%} overall). The site was fully supplied in {s['months_served']} of "
            f"{s['eval_months']} scored months."
        )
    st.plotly_chart(charts.replay_chart(r.monthly, r.settings.takeover, r.eval_start, unit))

    st.subheader("What the planner says, one month at a time")
    planning_months = [m for i, m in enumerate(r.months) if i >= r.settings.takeover] or [r.months[-1]]
    peak_month = s["peak_real_month"] if s["peak_real_month"] in planning_months else planning_months[0]
    if len(planning_months) > 1:
        month = st.select_slider(
            "Month", options=planning_months, value=peak_month, format_func=month_label, key="plan_month"
        )
    else:
        month = planning_months[0]
    row = r.monthly[r.monthly["month"] == month].iloc[0]
    st.caption(
        f"In {month_label(month)} the project really received {indian(row['real_arrived'])} {md(unit)} and the site "
        f"used {indian(row['used'])}; {indian(row['real_idle'])} {md(unit)} sat idle at month end. The planner, knowing "
        f"only the months before, would have ordered {indian(row['plan_order'])} and ended with "
        f"{indian(row['plan_idle'])} idle."
    )
    plan_now = explain.month_plan(r, month)
    cards = st.columns(2)
    for i, row_ in enumerate(plan_now.to_dict("records")):
        with cards[i % 2].container(border=True):
            st.markdown(f"**{md(row_['Godown'])}**  ·  {md(row_['Action'])}")
            st.caption(
                f"In stock {indian(row_['In stock'])} {md(unit)}  ·  expects about "
                f"{indian(row_['Forecast use / month'])} a month"
            )
            st.write(md(row_["Why"]))
    d1, d2, _ = st.columns([1, 1, 3])
    d1.download_button(
        "Download this month's list (CSV)",
        csv_bytes(plan_now),
        file_name=f"yardstick_{file_part(code)}_{month}_plan.csv",
        mime="text/csv",
    )
    d2.download_button(
        "Download the plan (Excel)",
        export.workbook(
            r, month, short_item(names[code], 80), run_all(fp, ds, takeover, window, safety), source=cfg.source
        ),
        file_name=f"yardstick_{file_part(code)}_{month}.xlsx",
        mime=XLSX,
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
    st.dataframe(table, hide_index=True, column_config=number_columns(table, dec), height=360)
    st.download_button(
        "Download the replay (CSV)",
        csv_bytes(table),
        file_name=f"yardstick_{file_part(code)}_replay.csv",
        mime="text/csv",
    )

    st.subheader(f"All {n_items} items, same settings")
    everything = run_all(fp, ds, takeover, window, safety)
    n_ok = int(everything["counts"].sum())
    median_cut = float(everything.loc[everything["counts"], "cut"].median()) if n_ok else 0.0
    st.markdown(
        f"**{n_ok} of {len(everything)} items** kept the site supplied (under {MAX_SHORTAGE_SHARE:.0%} of use short). "
        f"On those, the median cut in average idle stock is **{median_cut:.0%}**."
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

    _depends(ds, takeover, window, safety)
    _forecast_check(everything, window)

    with st.expander("How this plan works, and what it assumes"):
        st.markdown(
            f"- **Orders.** Each month: forecast = the higher of the last 3 and last {window} months' average use. "
            f"Cover = that forecast for the lead time plus this month, plus {safety:.2f} standard deviations of recent use "
            "as a safety margin. The order is whatever is missing after counting stock and orders already on the way. "
            "It never buys more in total than the project really bought (the data has no bill of quantities).\n"
            "- **Moves.** Stock above one godown's own need goes to a godown that would run short this month, before "
            "anything new is ordered. Transfers are same-day in the records, so a move during the month is allowed too "
            "and is counted.\n"
            "- **No peeking, with one stated exception.** The planner reads only months before the one it is deciding; "
            "the tests change the future and check nothing earlier moves. The exception is the cap on total buying: it is "
            "what the project really bought in all, standing in for the bill of quantities a project knows at the start. "
            "**What the saving depends on** shows the result without it.\n"
            f"- **First {takeover} months** run on what really arrived, because a forecast needs history. Orders landing "
            "within the first lead time after that are inherited too. Scoring starts after that.\n"
            "- **Use by godown is an estimate.** Use is recorded for the whole project only, so each month's use is split "
            "by what each godown handed out.\n"
            "- **Contractor-held stock** is part of the real idle figure. In the plan, contractors draw what the site "
            "uses, so none is held back."
        )


def _depends(ds: Dataset, takeover: int, window: int, safety: float) -> None:
    """The headline rests on two assumptions: take each away and show what is left."""
    ledger, cfg, fp = ds.ledger, ds.cfg, ds.fingerprint
    anchor = cfg.anchor_code if cfg.anchor_code in set(ledger.items["item_code"]) else None
    noun = cfg.anchor_plural.capitalize()
    st.subheader("What the saving depends on")
    st.markdown(
        f"The headline rests on two assumptions. Here is each one taken away, on all {len(ledger.items)} items."
    )
    scenarios = (
        ("As above", True, "use"),
        ("The planner does not know the project's total requirement", False, "use"),
        ("Contractors keep drawing ahead of use, as they really did", True, "issues"),
        ("Both taken away", False, "issues"),
    )
    anchor_rows, sens = {}, []
    for label, capped_, basis_ in scenarios:
        d_ = run_all(fp, ds, takeover, window, safety, capped_, basis_)
        ok_ = d_[d_["counts"]]
        row = {
            "Scenario": label,
            "Items that kept the site supplied": f"{len(ok_)} of {len(d_)}",
            "Median cut in idle stock": f"{ok_['cut'].median():.0%}" if len(ok_) else "–",
        }
        if anchor:
            p_ = d_[d_["code"] == anchor].iloc[0]
            anchor_rows[(capped_, basis_)] = p_
            row[f"{noun}: average idle stock, really → with the plan"] = (
                f"{indian(p_['avg_real'])} → {indian(p_['avg_plan'])} ({-p_['cut']:+.0%})"
            )
            row[f"{noun} the plan buys (really bought)"] = f"{indian(p_['plan_recv'])} ({indian(p_['real_recv'])})"
        sens.append(row)
    st.dataframe(pd.DataFrame(sens), hide_index=True)
    held_text = ""
    if anchor:
        held = anchor_rows[(True, "use")]["avg_real"] - anchor_rows[(True, "issues")]["avg_real"]
        held_text = f"On {md(cfg.anchor_plural)} they held about {indian(held)} on average. "
    st.caption(
        "The first row is the headline. It assumes (1) the planner knows the project's total requirement, as a bill of "
        "quantities would tell it (the data has none, so what the project really bought stands in), and (2) stock "
        "reaches contractors only as it is used. In reality contractors drew ahead of use. "
        f"{held_text}In the last two rows they keep doing that, so only godown stock is compared."
    )


def _forecast_check(everything: pd.DataFrame, window: int) -> None:
    st.subheader(f"How good is the forecast? All {len(everything)} items, on this project's own history")
    fc = everything.dropna(subset=["forecast_error"]).sort_values("forecast_error")
    if not len(fc):
        st.caption("The project is too short to check the forecast.")
        return
    served, scored = int(fc["months_served"].sum()), int(fc["eval_months"].sum())
    lean = float(fc["forecast_bias"].median())
    st.markdown(
        "At the start of each scored month the planner forecasts use. That forecast is then checked against what the "
        "site really used over the months an order has to cover (the lead time plus one). Across the "
        f"{len(fc)} items it was off by a median **{fc['forecast_error'].median():.0%}** of real use and ran "
        f"{'high' if lean > 0 else 'low'} by a median **{abs(lean):.0%}**. It leans high on purpose: it takes the higher "
        f"of the last 3 and last {window} months' average, so one quiet month does not starve the site. With that "
        f"cushion the site was fully supplied in **{served} of {scored}** item-months ({served / max(scored, 1):.0%})."
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
        "Use on a construction project comes in lumps, so no forecast is exact month by month. The test that matters "
        "is the last column: the plan re-forecasts every month, and the site did not run short."
    )
