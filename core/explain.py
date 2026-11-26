"""Turns the planner's numbers into the list a store-keeper can approve: one line per godown, with the reason."""

from __future__ import annotations

import pandas as pd

from .fmt import indian, month_label, short_godown
from .planner import Replay


def _q(x: float, unit: str) -> str:
    return f"{indian(x)} {unit}"


def month_plan(r: Replay, month: str) -> pd.DataFrame:
    """What the planner says at the start of `month`, using only what was known by then."""
    i = r.months.index(month)
    by = r.by_godown
    now = by[by["month"] == month].set_index("godown")
    prev = (
        by[by["month"] == r.months[i - 1]].set_index("godown")["stock_end"]
        if i > 0
        else pd.Series(0.0, index=r.godowns)
    )
    planned = r.moves[(r.moves["month"] == month) & (r.moves["when"] == "start of month")]
    lead = r.lead_months
    unit = r.unit
    planning = i >= r.settings.takeover
    land = r.months[i + lead] if i + lead < len(r.months) else None

    def on_the_way(gd: str) -> float:
        return float(by[(by["godown"] == gd) & (by["month"].isin(r.months[i + 1 : i + lead]))]["arrived"].sum())

    site_target = float(now["target"].sum())
    site_position = sum(
        float(
            prev.get(g, 0.0)
            + now.loc[g, "arrived"]
            + planned.loc[planned["to"] == g, "qty"].sum()
            - planned.loc[planned["from"] == g, "qty"].sum()
            + on_the_way(g)
        )
        for g in r.godowns
    )

    rows = []
    for gd in r.godowns:
        row = now.loc[gd]
        have_before = float(prev.get(gd, 0.0) + row["arrived"])
        mi = float(planned.loc[planned["to"] == gd, "qty"].sum())
        mo = float(planned.loc[planned["from"] == gd, "qty"].sum())
        coming = on_the_way(gd)
        order = float(row["order"])
        sources = ", ".join(short_godown(s) for s in planned.loc[planned["to"] == gd, "from"].unique())
        targets = ", ".join(short_godown(s) for s in planned.loc[planned["from"] == gd, "to"].unique())

        actions, why = [], []
        if not planning:
            actions.append("Original plan")
            why.append(
                f"The planner has not taken over yet: it needs {r.settings.takeover} months of use to forecast from."
            )
        else:
            if mi > 0:
                actions.append(f"Move in {_q(mi, unit)} from {sources}")
                why.append(
                    f"Would run short this month: it has {_q(have_before, unit)} and expects to need about "
                    f"{_q(row['forecast_use'] + row['safety'], unit)}. {sources} holds more than it needs."
                )
            if mo > 0:
                actions.append(f"Move out {_q(mo, unit)} to {targets}")
                why.append(
                    f"Holds more than it needs (target {_q(row['target'], unit)}); the extra goes where it is short."
                )
            if order > 0:
                lands = f", lands {month_label(land)}" if land else ""
                actions.append(f"Order {_q(order, unit)}{lands}")
                why.append(
                    f"Cover for {lead + 1} months of forecast use plus a safety margin is {_q(row['target'], unit)}; "
                    f"it has {_q(have_before + mi - mo, unit)} and {_q(coming, unit)} already on the way."
                )
            if not actions:
                actions.append("Hold")
                if have_before + coming >= row["target"] - 1e-9:
                    why.append(
                        f"{_q(have_before, unit)} in stock and {_q(coming, unit)} on the way already cover the target of "
                        f"{_q(row['target'], unit)}."
                    )
                else:
                    why.append(
                        f"Below its own target of {_q(row['target'], unit)}, but the whole site holds {_q(site_position, unit)} "
                        f"in stock and on the way against a site target of {_q(site_target, unit)}, so nothing new is ordered."
                    )
        rows.append(
            {
                "Godown": short_godown(gd),
                "In stock": have_before,
                "Forecast use / month": float(row["forecast_use"]),
                "Action": " + ".join(actions),
                "Why": " ".join(why),
            }
        )
    return pd.DataFrame(rows)


def verdict(r: Replay, counts: bool) -> tuple[str, str]:
    """('success'|'warning'|'info', one sentence). Honest about what the plan did and did not manage."""
    s = r.summary
    unit = r.unit
    if s["orders_n"] == 0:
        return "info", (
            f"Nothing left for the planner to decide: the whole requirement ({_q(s['requirement'], unit)}) had already arrived "
            f"before it took over, so the plan equals what really happened."
        )
    cut = s["reduction"]
    short = s["shortage_pct"]
    if not counts:
        return "warning", (
            f"This plan ran short on {short:.1%} of use ({_q(s['shortage_total'], unit)}), so its saving does not count. "
            f"Raise the safety margin to protect the site."
        )
    if cut <= 0:
        return (
            "info",
            "The planner does no better than what really happened on this item: most of the stock was already bought before it took over.",
        )
    return "success", (
        f"Average idle stock would have been {cut:.0%} lower ({_q(s['avg_real'], unit)} became {_q(s['avg_plan'], unit)}), "
        f"with {short:.1%} shortage. Total bought: {_q(s['plan_received'], unit)} against {_q(s['real_received'], unit)} really bought."
    )
