"""Match the sets: does every child go out in the ratio its parent needs, godown by godown and month by month?"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core import bomfix, charts
from core import pack as weekly
from core import sets as setcheck
from core.dataset import Dataset
from core.fmt import indian, month_label, short_item
from core.safe import csv_bytes, file_part, md
from ui.common import XLSX
from ui.state import k, planner_limits, run_bakeoff, run_balance, run_calibration


def render(ds: Dataset) -> None:
    fp = ds.fingerprint
    if ds.sets.empty:
        st.info("This project's data has no sets table, so there are no sets to match.")
        return
    cal = run_calibration(fp, ds)
    n_pairs, n_agree = len(cal), int(cal["agrees"].sum())
    st.markdown(
        "**Some items only work as a set.** A pole clamp is no use without its eye hook and bolts; if one runs short, "
        "the whole set waits however much of the rest is lying in the store. This checks, godown by godown and month by "
        "month, whether the children went out in the ratio their parents needed."
    )
    message = (
        f"**The master data's ratios agree with what the site really used for {n_agree} of {n_pairs} pairs.** "
        "Below are both: the ratio as given, and the ratio the site's own use implies. Which one is right for each "
        "item is a question for the people who keep the master data."
    )
    (st.success if n_agree == n_pairs else st.warning)(message)
    ratio_table = pd.DataFrame(
        {
            "Set": cal["parent_name"].map(lambda n: short_item(n, 34)),
            "Child": cal["child_name"].map(lambda n: short_item(n, 40)),
            "Unit": cal["child_unit"],
            "Ratio as given": [f"{v:g}" for v in cal["stated"]],
            "Ratio from real use": [f"{v:.4g}" for v in cal["observed"]],
            "Off by": [
                "agrees" if a else f"{(f if f >= 1 else 1 / f):.1f}× " + ("too high" if f < 1 else "too low")
                for f, a in zip(cal["factor"], cal["agrees"])
            ],
            "Follows its parent month to month": [
                f"{p} ({c:.2f})" if pd.notna(c) else "–" for p, c in zip(cal["pairing"], cal["monthly_correlation"])
            ],
        }
    )
    st.dataframe(ratio_table, hide_index=True)
    st.caption(
        "'Off by' says how far the stated ratio is from real use. 'Follows its parent' is how closely a child's monthly "
        "use tracks its parent's: only a child that tracks closely (0.8 or more) can be judged set by set."
    )
    _fix_sheet(ds, cal)
    _bakeoff(ds)

    parents = list(dict.fromkeys(cal["parent_code"]))
    pnames = dict(zip(cal["parent_code"], cal["parent_name"]))
    c1, c2, c3 = st.columns([3, 2, 2])
    parent = c1.selectbox("Set", parents, format_func=lambda p: short_item(pnames[p], 60), key=k("set_parent"))
    basis_label = c2.radio("Ratio to use", ["From real use", "As the file gives it"], key=k("set_basis"))
    loose = c3.checkbox(
        "Also judge loosely paired items",
        value=False,
        key=k("set_loose"),
        help="Children whose use only loosely follows the parent's. Expect many flags. Items that several sets share "
        "are never judged set by set.",
    )
    view = run_balance(fp, ds, parent, "observed" if basis_label == "From real use" else "stated", loose)

    judged_children = [c for c, j in (view.judged or {}).items() if j]
    if not judged_children:
        st.info(
            "None of this set's children is issued set by set (their use does not follow the parent's closely enough), "
            "so the records cannot say whether this set is in balance. Ask the site team how contractors draw them, or "
            "tick the box above to see what a loose reading gives."
        )
    else:
        _judged(ds, view, judged_children, parent)

    with st.expander("How this works, and what it does not know"):
        st.markdown(
            "- **The account.** For each child at each godown: the sets' worth issued so far (child issued ÷ ratio) "
            "against the parents issued so far. Negative means the child is behind and sets wait for it; positive means "
            "the child is ahead and piling up.\n"
            "- **Ratios.** 'From real use' keeps the shape of the file's numbers and scales each child by one factor so "
            "the project's totals match what the site used. 'As the file gives it' uses the file's numbers untouched.\n"
            "- **Not every child is issued set by set.** Bolts, nuts and some lugs are handed out in bulk. Judging them "
            "month by month would flag almost everything, so by default only children that follow their parent closely "
            "are judged, and the rest are listed above for the site team to explain.\n"
            f"- **Big enough to matter.** A godown is judged once it has issued {ds.cfg.min_parents:,.0f} parents, and a "
            f"gap counts from {ds.cfg.min_sets:,.0f} sets.\n"
            "- **Contractor-held stock** is not split by godown in the records, so the account measures what left each "
            "godown, not what was installed."
        )


def _judged(ds: Dataset, view, judged_children: list[str], parent: str) -> None:
    ledger = ds.ledger
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
    k3.metric("Judged children", md(", ".join(short_item(view.names[c], 22) for c in judged_children)), border=True)

    st.plotly_chart(charts.set_calendar(cal_m))
    st.caption(
        f"How many godown accounts were off on a judged child, month by month. Serious: off by {setcheck.SERIOUS:.0%} "
        f"or more of all sets issued so far. Watch: {setcheck.WATCH:.0%} or more."
    )

    child = st.selectbox(
        "Child", judged_children, format_func=lambda c: short_item(view.names[c], 60), key=k("set_child")
    )
    st.plotly_chart(
        charts.set_account(
            view.account[view.account["child"] == child],
            short_item(view.names[child], 28),
            setcheck.WATCH,
            setcheck.SERIOUS,
            ledger.labels,
        )
    )

    st.subheader("Every flag, worst first")
    if len(flags):
        shown = pd.DataFrame(
            {
                "Month": flags["month"],
                "Godown": flags["godown"].map(ledger.label),
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
            csv_bytes(shown),
            file_name=f"yardstick_sets_{file_part(parent)}.csv",
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
        key=k("set_month"),
    )
    snap = setcheck.stuck(view, pick)
    checked = [m for m in view.members if m == view.parent or (view.judged or {}).get(m)]
    st.dataframe(
        pd.DataFrame(
            {
                "Godown": snap["godown"].map(ledger.label),
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


def _fix_sheet(ds: Dataset, cal: pd.DataFrame) -> None:
    fixes = bomfix.fix_sheet(cal)
    todo = fixes[fixes["action"] != bomfix.KEEP]
    unit = int((fixes["action"] == bomfix.UNIT).sum())
    st.markdown(
        f"**BOM fix sheet: {len(todo)} of {len(fixes)} ratios to review**"
        + (f", {unit} of them likely a unit mismatch (check the unit first)." if unit else ".")
        + " Nothing changes in the master data: the sheet proposes, a person approves each line."
    )
    sheet = bomfix.sheet_for_people(fixes)
    notes = pd.DataFrame(
        {
            "Assumptions and limits": [
                "Observed ratio: what the child really used, against what the stated ratios predict from its parents' "
                "use, over the whole project. A child several sets share gets one factor for all its ratios.",
                "Inside 0.75x to 1.33x counts as agreeing. More than 100 times off is more likely a unit mismatch "
                "(kilograms against tonnes) than a wrong count: fix the unit, not the ratio.",
                "Suggested ratios are rounded to three significant figures. Fill in the approved ratio, who approved it "
                "and when; nothing is applied automatically.",
                f"Source data: {ds.cfg.source}. Planning support, not an instruction.",
            ]
        }
    )
    st.download_button(
        "Download the BOM fix sheet (Excel)",
        lambda: weekly.write_sheets({"BOM fixes": sheet, "Assumptions": notes}),
        file_name=f"yardstick_bom_fixes_{file_part(ds.cfg.project)}.xlsx",
        mime=XLSX,
    )


def _bakeoff(ds: Dataset) -> None:
    _, _, takeover = planner_limits(ds)
    b = run_bakeoff(ds.fingerprint, ds, takeover)
    if b.empty:
        return
    st.markdown(
        f"**Plan children from their parents?** For {b['adopt'].sum()} of {len(b)} children, forecasting from the "
        "parents (times the ratio the site had shown so far) was no worse than the child's own history, so Monday's "
        "list plans them from their sets, month by month, only while that holds."
    )
    st.dataframe(
        pd.DataFrame(
            {
                "Child": b["child_name"].map(lambda n: short_item(n, 40)),
                "Unit": b["unit"],
                "Its sets": b["parents"].map(lambda n: short_item(n, 50)),
                "Error, own history": [f"{v:.0%}" for v in b["wape_item"]],
                "Error, from its sets": [f"{v:.0%}" for v in b["wape_set"]],
                "Plan from sets?": ["Yes" if a else "No" for a in b["adopt"]],
            }
        ),
        hide_index=True,
    )
    st.caption(
        "How: at every scored month, each way forecasts the lead time plus this month (what an order has to get "
        "right), from earlier months only; the ratio is learned as the child's use so far over its parents' use so far, "
        "never from the whole project. Error = the sum of misses over the sum of what the child really used "
        "(WAPE), over the same months as the replay."
    )
