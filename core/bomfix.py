"""BOM fix sheet: the master data's parts-per-set ratios against what the site really used, and what to change.

Nothing here changes the master data. The sheet proposes; a person approves every line (the "approved" columns are
left blank for them). For each parent-child pair, from `sets.calibrate`:

  KEEP                  the stated ratio agrees with site use (0.75x to 1.33x)
  CHANGE THE RATIO      it disagrees; the suggested ratio is what the site's use implies
  CHECK THE UNIT FIRST  it is more than 100 times off (`misses.UNIT_SUSPECT`): the child is more likely counted in
                        another unit (kilograms against tonnes) than wrongly counted. Fix the unit, not the ratio.

How the observed ratio is found: what the child really used, against what the stated ratios predict from its parents'
use. A child several sets share gets one factor for all its ratios (the records cannot tell which set used which part).
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .misses import UNIT_SUSPECT

KEEP, CHANGE, UNIT = "Keep", "Change the ratio", "Check the unit first"
COLUMNS = [
    "set_no",
    "parent_code",
    "parent_name",
    "child_code",
    "child_name",
    "child_unit",
    "stated",
    "observed",
    "factor",
    "suggested",
    "action",
    "note",
    "pairing",
    "shared",
]


def three_figures(x: float) -> float:
    """A ratio rounded to three significant figures: a master-data value, not a measurement to the last digit."""
    if not math.isfinite(x) or x == 0:
        return x
    return float(f"{x:.3g}")


def off_by(factor: float) -> str:
    if not math.isfinite(factor) or factor <= 0:
        return "no use yet"
    return f"{factor:,.1f}× too low" if factor >= 1 else f"{1 / factor:,.1f}× too high"


def fix_sheet(cal: pd.DataFrame) -> pd.DataFrame:
    """One line per parent-child pair, worst first. `cal` is `sets.calibrate` (whole project or as of a month)."""
    if cal is None or cal.empty:
        return pd.DataFrame(columns=COLUMNS)
    rows = []
    for r in cal.itertuples():
        factor = float(r.factor)
        unit = str(r.child_unit)
        known = math.isfinite(factor) and factor > 0
        if not known:
            action, suggested = KEEP, float(r.stated)
            note = "The site has not used this child yet, so its ratio cannot be checked."
        elif r.agrees:
            action, suggested = KEEP, float(r.stated)
            note = "Agrees with what the site used."
        elif abs(math.log(factor)) > math.log(UNIT_SUSPECT):
            action, suggested = UNIT, three_figures(float(r.observed))
            note = (
                f"{off_by(factor)}: more likely the child is counted in another unit than in the wrong number (the "
                f"file says {unit}). Check the unit first; if {unit} is right, the ratio would be "
                f"{three_figures(float(r.observed)):g}."
            )
        else:
            action, suggested = CHANGE, three_figures(float(r.observed))
            note = f"The site used about {three_figures(float(r.observed)):g} {unit} per set ({off_by(factor)})."
        if r.shared:
            note += " Several sets share this child, so one factor scales all its ratios."
        if r.pairing in ("weak", "loose") and known and action != KEEP:
            note += (
                f" Its monthly use follows the parent only {r.pairing}ly: it may not be issued set by set, so read the "
                "suggested ratio as an average."
            )
        rows.append(
            {
                "set_no": r.set_no,
                "parent_code": r.parent_code,
                "parent_name": r.parent_name,
                "child_code": r.child_code,
                "child_name": r.child_name,
                "child_unit": unit,
                "stated": float(r.stated),
                "observed": float(r.observed),
                "factor": factor,
                "suggested": suggested,
                "action": action,
                "note": note,
                "pairing": r.pairing,
                "shared": bool(r.shared),
            }
        )
    out = pd.DataFrame(rows, columns=COLUMNS)
    rank = out["action"].map({UNIT: 0, CHANGE: 1, KEEP: 2})
    far = np.abs(np.log(out["factor"].where(out["factor"] > 0)))
    return (
        out.assign(_r=rank, _f=-far.fillna(0.0))
        .sort_values(["_r", "_f"], kind="stable")
        .drop(columns=["_r", "_f"])
        .reset_index(drop=True)
    )


def sheet_for_people(fixes: pd.DataFrame) -> pd.DataFrame:
    """The fix sheet as a person reads and signs it: blank columns for the approved ratio, who and when."""
    return pd.DataFrame(
        {
            "Set": fixes["parent_name"],
            "Child": fixes["child_name"],
            "Unit": fixes["child_unit"],
            "Ratio in the master data": fixes["stated"],
            "Ratio from site use": [three_figures(v) if math.isfinite(v) else None for v in fixes["observed"]],
            "Off by": [
                "agrees" if a == KEEP and math.isfinite(f) and f > 0 else off_by(f)
                for a, f in zip(fixes["action"], fixes["factor"])
            ],
            "Suggested ratio": fixes["suggested"],
            "What to do": fixes["action"],
            "Note": fixes["note"],
            "Approved ratio (fill in)": "",
            "Approved by": "",
            "Date": "",
        }
    )
