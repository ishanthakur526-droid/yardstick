"""Contractor holdings against the cover rule (lever 2): what contractors held, what the rule allows, and when to hold
issues.

The records do not say which contractor holds what, so contractors are one project-wide pool. At the start of each
month, from the records before it only:

  held     with contractors at the end of last month (issues - returns - recorded use, running total)
  allowed  `cover_weeks` of forecast use (`planner.forecast` on the site's use so far)
  excess   held - allowed. Above zero: issue only what the site uses beyond the excess until it comes down (an
           issue hold). Below zero: they hold less than the rule allows, which is fine.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .ledger import Ledger, item_site
from .planner import WEEKS_PER_MONTH, Settings, forecast

COLUMNS = ["item_code", "month", "held", "rate", "allowed", "excess", "weeks_held", "alert"]


def allowed(used_before: np.ndarray, settings: Settings) -> float:
    """What the issue rule lets contractors hold at the start of a month (nan: no rule, contractors draw freely)."""
    if settings.cover_weeks is None:
        return float("nan")
    return max(0.0, float(settings.cover_weeks)) / WEEKS_PER_MONTH * forecast(used_before, settings)


def weeks_of_use(held: float, rate: float) -> float:
    """How many weeks of use `held` is at a forecast monthly rate."""
    return held / rate * WEEKS_PER_MONTH if rate > 0 else float("nan")


def series(ledger: Ledger, code: str, settings: Settings = Settings()) -> pd.DataFrame:
    """One row per month, as it stood at the start of that month. `alert` is an issue hold: contractors hold more than
    the rule allows by at least one ordering step."""
    s = item_site(ledger, code)
    used = s["used"].to_numpy()
    held_end = s["with_contractors"].to_numpy()
    step = ledger.step(code)
    rows = []
    for i, month in enumerate(s["month"]):
        held = float(held_end[i - 1]) if i > 0 else 0.0
        rate = forecast(used[:i], settings)
        keep = allowed(used[:i], settings)
        excess = held - keep if not math.isnan(keep) else float("nan")
        rows.append(
            {
                "item_code": code,
                "month": str(month),
                "held": held,
                "rate": rate,
                "allowed": keep,
                "excess": excess,
                "weeks_held": weeks_of_use(held, rate),
                "alert": bool(not math.isnan(excess) and excess > step),
            }
        )
    return pd.DataFrame(rows, columns=COLUMNS)


def at(ledger: Ledger, month: str, settings: Settings = Settings()) -> pd.DataFrame:
    """Every item on the Monday of `month`, the furthest over its allowance (in weeks held) first."""
    frames = [series(ledger, c, settings) for c in ledger.items["item_code"]]
    df = pd.concat(frames, ignore_index=True)
    df = df[df["month"] == month].reset_index(drop=True)
    return df.sort_values(["alert", "weeks_held"], ascending=False, kind="stable").reset_index(drop=True)
