"""Monday's list: what procurement and stores should do this week, from the stock position as the records stood.

The project in the data is finished, so the list is built "as of" a chosen month: it reads only the records before
that month, plus the deliveries already on order (receipts landing within one lead time were ordered before it).
For every item it says, with the reason:

  MOVE         stock from a godown holding more than its lead-time need to one that would run short this month
               (transfer vouchers come before any new purchase order)
  ORDER        what to order now so stock covers the lead time plus this month, with a safety margin, never past the
               cap (the BOQ when the data has one)
  HOLD ISSUES  contractors already hold more than the cover the issue rule allows: issue only what the site uses
               beyond that until their stock comes down

It runs the same decision step as the replay (`planner.decide`), on the real stock instead of the replayed one.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .fmt import indian, month_label
from .holdings import allowed, weeks_of_use
from .ledger import Ledger, item_site
from .planner import Settings, allocation_shares, cap_for, decide, forecast, lead_months
from .setplan import adopt_at, driver, set_forecast, use_series

COLUMNS = ["item_code", "item", "unit", "kind", "qty", "detail", "why", "from_godown", "to_godown", "lands"]
KIND_ORDER = {"Move": 0, "Order": 1, "Hold issues": 2}


def monday(month: str) -> pd.Timestamp:
    """The first Monday of a month: the day the list is for."""
    first = pd.Timestamp(month + "-01")
    return first + pd.Timedelta(days=(7 - first.weekday()) % 7)


@dataclass
class Position:
    """One item's stock position at the start of the as-of month (all from records before it, plus open orders)."""

    have: np.ndarray  # per godown: stock at the end of last month plus what lands this month
    coming: np.ndarray  # per godown: on order, landing before a new order could
    past_total: np.ndarray  # demand on the godowns, months before this one
    past_by_godown: np.ndarray
    held: float  # with contractors at the end of last month
    keep: float  # what the issue rule lets contractors hold (nan: no rule)
    bought: float  # received so far plus on order
    lead: int


def position(ledger: Ledger, code: str, i: int, settings: Settings) -> Position:
    months, working = ledger.months, ledger.working
    lead = lead_months(ledger.lead_days(code))
    grid = ledger.grid[ledger.grid["item_code"] == code]

    def matrix(col: str) -> np.ndarray:
        return (
            grid.pivot(index="month", columns="godown", values=col)
            .reindex(index=months, columns=working)
            .fillna(0.0)
            .to_numpy()
        )

    site = item_site(ledger, code).set_index("month").reindex(months)
    received, stock = matrix("received"), matrix("stock")
    net = matrix("net_issue")
    used = site["used"].to_numpy()[:i]
    before = stock[i - 1] if i > 0 else np.zeros(len(working))
    if settings.cover_weeks is None:  # contractors draw as they really drew
        past = np.clip(net[:i], 0.0, None)
        keep = float("nan")
    else:
        past = used[:, None] * allocation_shares(net[:i]) if i > 0 else np.zeros((0, len(working)))
        keep = allowed(used, settings)
    return Position(
        have=before + received[i],
        coming=received[i + 1 : i + lead].sum(axis=0),
        past_total=past.sum(axis=1),
        past_by_godown=past,
        held=float(site["with_contractors"].iloc[i - 1]) if i > 0 else 0.0,
        keep=keep,
        bought=float(received[: i + lead].sum()),
        lead=lead,
    )


def from_sets(ledger: Ledger, sets: pd.DataFrame | None, code: str, i: int, settings: Settings) -> float | None:
    """A child's forecast from its parents, when the bake-off up to this month says it is no worse than its own."""
    if (
        sets is None
        or sets.empty
        or code not in set(sets["child_code"])
        or not adopt_at(ledger, sets, code, i, settings)
    ):
        return None
    return set_forecast(use_series(ledger, code), driver(ledger, sets, code), i, settings)


def item_actions(
    ledger: Ledger,
    code: str,
    month: str,
    settings: Settings = Settings(),
    boq: float | None = None,
    sets: pd.DataFrame | None = None,
) -> list[dict]:
    months = ledger.months
    i = months.index(month)
    p = position(ledger, code, i, settings)
    unit, step = ledger.unit(code), ledger.step(code)
    name = ledger.item_name(code)
    received_total = float(ledger.grid.loc[ledger.grid["item_code"] == code, "received"].sum())
    cap, cap_source = cap_for(settings, received_total, boq)
    room = max(0.0, cap - p.bought)

    # Contractors' stock beyond what the rule allows is used before anything new is issued, so it counts towards the
    # order; a shortfall below it has to be topped up, so it counts against.
    extra = (p.held - p.keep) if not np.isnan(p.keep) else 0.0
    share = p.past_by_godown[-settings.window :].sum(axis=0) if len(p.past_by_godown) else np.ones(len(p.have))
    share = share / share.sum() if share.sum() > 0 else np.full(len(p.have), 1 / len(p.have))
    f_sets = from_sets(ledger, sets, code, i, settings)
    d = decide(
        p.past_total, p.past_by_godown, p.have, p.coming + extra * share, room, p.lead, step, settings, f_total=f_sets
    )

    labels = ledger.labels
    lab = [labels.get(g, str(g).title()) for g in ledger.working]
    q = lambda x: f"{indian(x)} {unit}"  # noqa: E731
    base = {"item_code": code, "item": name, "unit": unit}
    out: list[dict] = []
    for a, b, qty in d.moves:
        out.append(
            {
                **base,
                "kind": "Move",
                "qty": qty,
                "detail": f"{lab[a]} → {lab[b]}",
                "why": f"{lab[b]} has {q(p.have[b])} and needs about {q(d.forecast[b] + d.safety[b])} this month; "
                f"{lab[a]} holds more than its own lead-time need.",
                "from_godown": lab[a],
                "to_godown": lab[b],
                "lands": "",
            }
        )
    if d.order is not None and d.order.sum() > 0:
        lands_month = months[i + p.lead] if i + p.lead < len(months) else ""
        lands = month_label(lands_month) if lands_month else "after the last recorded month"
        split = ", ".join(f"{lab[g]} {indian(x)}" for g, x in enumerate(d.order) if x > 0)
        held_note = f" Contractors hold {q(max(extra, 0.0))} beyond their cover, counted first." if extra > 0 else ""
        if f_sets is not None:
            held_note += (
                " Demand is forecast from its sets (its parents' forecast times the ratio the site has shown so far), "
                "which has done no worse than its own history up to now."
            )
        out.append(
            {
                **base,
                "kind": "Order",
                "qty": float(d.order.sum()),
                "detail": f"lands {lands} ({split})",
                "why": f"Cover for {p.lead + 1} months of forecast demand plus a safety margin is {q(d.target.sum())}; "
                f"the godowns hold {q(d.have.sum())} and {q(p.coming.sum())} is already on order.{held_note}"
                + (
                    f" Room left under the {'BOQ' if cap_source == 'boq' else 'cap'}: {q(room)}."
                    if np.isfinite(cap)
                    else ""
                ),
                "from_godown": "",
                "to_godown": split,
                "lands": lands_month,
            }
        )
    if extra > step and settings.cover_weeks is not None:
        rate = forecast(p.past_total, settings)
        weeks_held = weeks_of_use(p.held, rate)
        out.append(
            {
                **base,
                "kind": "Hold issues",
                "qty": extra,
                "detail": f"contractors hold {q(p.held)}"
                + (f", about {weeks_held:.0f} weeks of use" if rate > 0 else ""),
                "why": f"The issue rule allows {settings.cover_weeks:g} weeks of forecast use ({q(p.keep)}). Issue only "
                f"what the site uses beyond that until contractors' stock comes down.",
                "from_godown": "",
                "to_godown": "",
                "lands": "",
            }
        )
    return out


def week(
    ledger: Ledger,
    month: str,
    settings: Settings = Settings(),
    boq: dict[str, float | None] | None = None,
    sets: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Every item's actions for the Monday of `month`, moves first (vouchers before purchase orders). With `sets`, a
    child whose sets forecast it no worse than its own history so far is planned from its sets."""
    boq = boq or {}
    rows: list[dict] = []
    for code in ledger.items["item_code"]:
        rows.extend(item_actions(ledger, code, month, settings, boq.get(code), sets))
    df = pd.DataFrame(rows, columns=COLUMNS)
    if df.empty:
        return df
    df["_k"] = df["kind"].map(KIND_ORDER)
    return df.sort_values(["_k", "item"], kind="stable").drop(columns="_k").reset_index(drop=True)
