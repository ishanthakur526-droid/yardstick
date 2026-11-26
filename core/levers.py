"""The lever waterfall: what each decision the company controls would have been worth on this project.

The planner replays the project month by month, seeing only what was known at the time. Each row switches one more
lever on, so the change from one row to the next is what that lever is worth:

  Actual               what really sat idle: in the godowns and with contractors
  Order by use         the planner orders on a forecast of what the godowns hand out; no cap on total buying;
                       contractors keep drawing what they really drew
  + BOQ cap            ...and never buys more in total than the bill of quantities (or a stand-in for it)
  + contractor cover   ...and contractors are issued only enough to hold N weeks of forecast use

Every row is scored the same way, over the same months: average and peak idle stock (godowns plus contractors), the
share of demand the plan could not supply, and whether its saving counts (no more than 1% short).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from .ledger import Ledger
from .planner import DEFAULT_COVER_WEEKS, MAX_SHORTAGE_SHARE, Replay, Settings, counts, replay

ACTUAL, BY_USE, BOQ, COVER = "actual", "by_use", "boq", "cover"
COLUMNS = [
    "step",
    "label",
    "avg_idle",
    "peak_idle",
    "peak_month",
    "avg_godowns",
    "avg_contractors",
    "shortage_pct",
    "counts",
    "cut",
    "change",
    "bought",
    "cap_source",
]


@dataclass(frozen=True)
class Step:
    key: str
    label: str
    settings: Settings | None  # None: what really happened


def cover_label(weeks: float) -> str:
    return f"+ contractor cover ({weeks:g} week{'' if weeks == 1 else 's'})"


def steps(base: Settings = Settings(), cover_weeks: float = DEFAULT_COVER_WEEKS, boq_cap: bool = True) -> list[Step]:
    """The rows, in order. `base` carries the planner's own settings (takeover, window, safety). With `boq_cap` off the
    cap row is left out and the cover row runs uncapped."""
    out = [
        Step(ACTUAL, "Actual", None),
        Step(BY_USE, "Order by use", replace(base, capped=False, cover_weeks=None)),
    ]
    if boq_cap:
        out.append(Step(BOQ, "+ BOQ cap", replace(base, capped=True, cover_weeks=None)))
    out.append(Step(COVER, cover_label(cover_weeks), replace(base, capped=boq_cap, cover_weeks=float(cover_weeks))))
    return out


def _window(r: Replay) -> pd.DataFrame:
    return r.monthly.iloc[r.eval_start :]


def rows(run: Callable[[Settings], Replay], plan: list[Step]) -> pd.DataFrame:
    """One row per step. `run(settings)` returns the replay for one item (the app passes its cached runner)."""
    out = []
    first = None
    for step in plan:
        if step.settings is None:
            continue
        r = run(step.settings)
        if first is None:
            first = r
        ev, s = _window(r), r.summary
        out.append(
            {
                "step": step.key,
                "label": step.label,
                "avg_idle": s["avg_plan"],
                "peak_idle": s["peak_plan"],
                "peak_month": s["peak_plan_month"],
                "avg_godowns": float(ev["plan_godown"].mean()),
                "avg_contractors": float(ev["plan_pool"].mean()),
                "shortage_pct": s["shortage_pct"],
                "counts": counts(s),
                "bought": s["plan_received"],
                "cap_source": s["cap_source"],
            }
        )
    if first is None:
        raise ValueError("a waterfall needs at least one planned step")
    ev, s = _window(first), first.summary
    actual = {
        "step": ACTUAL,
        "label": "Actual",
        "avg_idle": s["avg_real"],
        "peak_idle": s["peak_real"],
        "peak_month": s["peak_real_month"],
        "avg_godowns": float(ev["real_in_godowns"].mean()),
        "avg_contractors": float(ev["real_with_contractors"].mean()),
        "shortage_pct": 0.0,  # recorded use was, by definition, supplied
        "counts": True,
        "bought": s["real_received"],
        "cap_source": "",
    }
    df = pd.DataFrame([actual] + out)
    base = actual["avg_idle"]
    df["cut"] = 1 - df["avg_idle"] / base if base > 0 else 0.0
    df["change"] = df["avg_idle"].diff().fillna(0.0)
    return df[COLUMNS]


def item_waterfall(
    ledger: Ledger,
    code: str,
    boq: float | None = None,
    base: Settings = Settings(),
    cover_weeks: float = DEFAULT_COVER_WEEKS,
    boq_cap: bool = True,
) -> pd.DataFrame:
    """The waterfall for one item, computed directly (the app uses `rows` with its cached runner instead)."""
    return rows(lambda st: replay(ledger, code, st, boq=boq), steps(base, cover_weeks, boq_cap))


def portfolio(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """All items at once, one row per step: how many items' savings count, and the median cut in average idle stock
    among those (units differ, so quantities are never added across items)."""
    if not tables:
        return pd.DataFrame(columns=["step", "label", "items", "items_counting", "median_cut", "worst_shortage"])
    order = next(iter(tables.values()))[["step", "label"]].to_dict("records")
    out = []
    for st in order:
        picked = pd.DataFrame([t[t["step"] == st["step"]].iloc[0] for t in tables.values()])
        ok = picked[picked["counts"].astype(bool)]
        out.append(
            {
                "step": st["step"],
                "label": st["label"],
                "items": len(picked),
                "items_counting": len(ok),
                "median_cut": float(np.median(ok["cut"])) if len(ok) else float("nan"),
                "worst_shortage": float(picked["shortage_pct"].max()),
            }
        )
    return pd.DataFrame(out)


SHORTAGE_RULE = f"A saving counts only if the plan left no more than {MAX_SHORTAGE_SHARE:.0%} of demand unsupplied."
