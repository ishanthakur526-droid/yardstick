"""Plan children from their parents: is a child's demand better forecast from its sets than from its own history?

  PER ITEM   `planner.forecast` on the child's own use so far (what the planner does today).
  FROM SETS  the parents' forecast times the ratio the site has shown so far. The parents' demand is the "driver":
             each parent's use times its stated ratio, added over every set the child is in. The learned ratio is
             expanding, `cumsum(child[:t]) / cumsum(driver[:t])`: only months before t. Never the whole-project
             calibration (`sets.calibrate`), which would read the future.

  BAKE-OFF   both forecasts at every scored month, over what an order has to get right (the lead time plus this month),
             against what the child really used then. Error is WAPE: the sum of misses over the sum of use. "From sets"
             is adopted for a child only if its error is no worse than per item.

The bake-off scores the whole project, so it is a verdict after the fact. `adopt_at` is the same comparison made with
only the months before t, for use in a plan made at t.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .ledger import Ledger
from .planner import Settings, forecast, lead_months

COLUMNS = [
    "child_code",
    "child_name",
    "unit",
    "parents",
    "lead",
    "months_scored",
    "wape_item",
    "wape_set",
    "bias_item",
    "bias_set",
    "adopt",
]


def use_series(ledger: Ledger, code: str) -> np.ndarray:
    s = ledger.site[ledger.site["item_code"] == code].set_index("month")["used"]
    return s.reindex(ledger.months).fillna(0.0).to_numpy()


def driver(ledger: Ledger, sets: pd.DataFrame, child: str) -> np.ndarray:
    """What the child's parents' use implies at the stated ratios, month by month."""
    out = np.zeros(len(ledger.months))
    for r in sets[sets["child_code"] == child].itertuples():
        out += float(r.ratio_of_parent) * use_series(ledger, r.parent_code)
    return out


def item_forecast(child: np.ndarray, t: int, settings: Settings) -> float:
    return forecast(child[:t], settings)


def set_forecast(child: np.ndarray, drive: np.ndarray, t: int, settings: Settings) -> float:
    """The child's next-month use from its parents: their forecast times the ratio learned from months before t. With
    no parent use yet there is nothing to learn from, so it falls back to the child's own forecast."""
    seen = float(drive[:t].sum())
    if seen <= 0:
        return item_forecast(child, t, settings)
    return float(child[:t].sum()) / seen * forecast(drive[:t], settings)


def _score(guess: np.ndarray, real: np.ndarray) -> tuple[float, float]:
    total = real.sum()
    if len(real) == 0 or total <= 0:
        return float("nan"), float("nan")
    return float(np.abs(guess - real).sum() / total), float((guess - real).sum() / total)


def _spans(
    child: np.ndarray, drive: np.ndarray, starts: range, span: int, settings: Settings
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    item = np.array([item_forecast(child, t, settings) * span for t in starts])
    sets_ = np.array([set_forecast(child, drive, t, settings) * span for t in starts])
    real = np.array([child[t : t + span].sum() for t in starts])
    return item, sets_, real


def bakeoff(ledger: Ledger, sets: pd.DataFrame, settings: Settings = Settings()) -> pd.DataFrame:
    """One row per child, scored over the same months as the replay (from takeover plus one lead time)."""
    if sets is None or sets.empty:
        return pd.DataFrame(columns=COLUMNS)
    n = len(ledger.months)
    rows = []
    for child, grp in sets.groupby("child_code", sort=False):
        lead = lead_months(ledger.lead_days(child))
        span = lead + 1
        starts = range(min(settings.takeover + lead, n - 1), n - lead)
        c, d = use_series(ledger, child), driver(ledger, sets, child)
        item, from_sets, real = _spans(c, d, starts, span, settings)
        wi, bi = _score(item, real)
        ws, bs = _score(from_sets, real)
        rows.append(
            {
                "child_code": child,
                "child_name": ledger.item_name(child),
                "unit": ledger.unit(child),
                "parents": ", ".join(str(p) for p in grp["parent_name"]),
                "lead": lead,
                "months_scored": len(starts),
                "wape_item": wi,
                "wape_set": ws,
                "bias_item": bi,
                "bias_set": bs,
                "adopt": bool(np.isfinite(ws) and np.isfinite(wi) and ws <= wi),
            }
        )
    return pd.DataFrame(rows, columns=COLUMNS)


def adopt_at(ledger: Ledger, sets: pd.DataFrame, child: str, t: int, settings: Settings = Settings()) -> bool:
    """The bake-off as it stood at the start of month t: only spans that had fully happened before t are scored."""
    lead = lead_months(ledger.lead_days(child))
    span = lead + 1
    starts = range(min(settings.takeover, t), max(min(settings.takeover, t), t - span + 1))
    if len(starts) == 0:
        return False
    item, from_sets, real = _spans(use_series(ledger, child), driver(ledger, sets, child), starts, span, settings)
    wi, ws = _score(item, real)[0], _score(from_sets, real)[0]
    return bool(np.isfinite(ws) and np.isfinite(wi) and ws <= wi)
