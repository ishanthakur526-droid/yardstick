"""Money layer: rupees appear only when the visitor types their own rates. Without rates, nothing is shown in rupees.

The data has no prices. A visitor may type a rate per unit for any item, and an annual cost of capital; both live in
their browser session only and are never stored. From them:

  idle value         average idle stock x rate (godowns and contractors, over the scored months)
  carrying cost      idle value x the annual cost of capital: what the money tied up in idle stock costs a year
                     (cost of capital only; storage, theft and damage would add to it)
  saving that counts the drop in carrying cost from Actual, added up only over items whose plan kept the site
                     supplied (no more than 1% short); an item that ran short adds nothing

Quantities in different units are never added; rupees can be, which is why this layer exists.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

import pandas as pd

DEFAULT_COST_OF_CAPITAL = 0.12  # a year: a placeholder the visitor replaces with their own
PORTFOLIO_COLUMNS = ["step", "label", "items_priced", "items_counting", "idle_value", "carrying", "saving_counts"]


def clean_rates(rates: Mapping[str, object] | None) -> dict[str, float]:
    """Only positive, finite numbers count as a rate; anything else (blank, text, zero) means no rate for that item."""
    out: dict[str, float] = {}
    for code, v in (rates or {}).items():
        try:
            x = float(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if math.isfinite(x) and x > 0:
            out[str(code)] = x
    return out


def rupees(x: float) -> str:
    """Indian style: crore and lakh for big sums."""
    if not math.isfinite(x):
        return "–"
    sign, a = ("-" if x < 0 else ""), abs(x)
    if a >= 1e7:
        return f"{sign}₹{a / 1e7:,.2f} crore"
    if a >= 1e5:
        return f"{sign}₹{a / 1e5:,.2f} lakh"
    return f"{sign}₹{a:,.0f}"


def item_value(rows: pd.DataFrame, rate: float, annual: float) -> pd.DataFrame:
    """One item's lever waterfall (`levers.rows`) in rupees."""
    out = rows[["step", "label", "counts", "shortage_pct"]].copy()
    out["idle_value"] = rows["avg_idle"].to_numpy() * rate
    out["peak_value"] = rows["peak_idle"].to_numpy() * rate
    out["carrying"] = out["idle_value"] * annual
    out["saving"] = out["carrying"].iloc[0] - out["carrying"]
    return out


def portfolio(tables: Mapping[str, pd.DataFrame], rates: Mapping[str, float], annual: float) -> pd.DataFrame:
    """Every priced item, one row per lever step. Empty when there are no rates."""
    rates = clean_rates(rates)
    priced = {c: t for c, t in tables.items() if c in rates}
    if not priced:
        return pd.DataFrame(columns=PORTFOLIO_COLUMNS)
    values = {c: item_value(t, rates[c], annual) for c, t in priced.items()}
    first = next(iter(values.values()))
    out = []
    for i, (step, label) in enumerate(zip(first["step"], first["label"])):
        rows = [v.iloc[i] for v in values.values()]
        ok = [r for r in rows if bool(r["counts"])]
        out.append(
            {
                "step": step,
                "label": label,
                "items_priced": len(rows),
                "items_counting": len(ok),
                "idle_value": float(sum(r["idle_value"] for r in rows)),
                "carrying": float(sum(r["carrying"] for r in rows)),
                "saving_counts": float(sum(r["saving"] for r in ok)),
            }
        )
    return pd.DataFrame(out, columns=PORTFOLIO_COLUMNS)
