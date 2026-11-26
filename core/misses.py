"""What the stock report misses: three things the godown stock report does not show, read straight from the records.

CONTRACTOR-HELD STOCK  issued to contractors but not yet used (issues - returns - use, running total). The godown
                       report counts it as gone; it is still stock the project paid for.
SET RATIOS             the master data's parts-per-set against what the site really used (see core/sets.py).
CLOSING STORE          leftovers transferred to the closing store at wind-down: bought, never used on this project.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .ledger import Ledger, item_site


def contractor_stock(ledger: Ledger, code: str) -> dict:
    """How much of one item sat with contractors, averaged over every month of the project, and at its peak."""
    s = item_site(ledger, code)
    held, idle, used = s["with_contractors"], s["idle_total"], s["used"]
    peak_i = int(held.to_numpy().argmax())
    active = used[used > 0]
    per_month = float(active.mean()) if len(active) else 0.0
    recent = used.rolling(3, min_periods=1).mean()
    at_peak_rate = float(recent.iloc[peak_i])
    return {
        "avg_held": float(held.mean()),
        "share_of_idle": float(held.sum() / idle.sum()) if idle.sum() > 0 else 0.0,
        "peak_held": float(held.iloc[peak_i]),
        "peak_month": str(s["month"].iloc[peak_i]),
        "peak_months_of_use": float(held.iloc[peak_i] / at_peak_rate) if at_peak_rate > 0 else float("nan"),
        "avg_months_of_use": float(held.mean() / per_month) if per_month > 0 else float("nan"),
        "months": len(s),
    }


def contractor_stock_all(ledger: Ledger) -> pd.DataFrame:
    rows = [{"item_code": c, **contractor_stock(ledger, c)} for c in ledger.items["item_code"]]
    return pd.DataFrame(rows)


UNIT_SUSPECT = 100.0  # a stated ratio this many times off is more likely kept in another unit than simply wrong


def ratio_gaps(cal: pd.DataFrame) -> dict:
    """Stated ratios that disagree with site use, from `sets.calibrate`, and the furthest off. A ratio more than 100
    times off is counted apart: it more likely states the child in another unit (kg against tonnes) than a wrong count."""
    if cal is None or cal.empty:
        return {"pairs": 0, "disagree": 0, "unit_suspect": 0, "worst": None}
    pairs = cal.drop_duplicates(["parent_code", "child_code"])
    off = pairs[~pairs["agrees"].astype(bool) & pairs["factor"].notna() & (pairs["factor"] > 0)]
    far = off.assign(_far=np.abs(np.log(off["factor"].astype(float)))).sort_values("_far", ascending=False)
    suspect = far["_far"] > math.log(UNIT_SUSPECT)
    worst = None
    plain = far[~suspect]
    if len(plain):
        w = plain.iloc[0]
        worst = {
            "parent_name": str(w["parent_name"]),
            "child_name": str(w["child_name"]),
            "child_unit": str(w["child_unit"]),
            "stated": float(w["stated"]),
            "observed": float(w["observed"]),
            "factor": float(w["factor"]),
        }
    return {"pairs": int(len(pairs)), "disagree": int(len(off)), "unit_suspect": int(suspect.sum()), "worst": worst}


def closing_store(ledger: Ledger) -> pd.DataFrame:
    """Per item: what went into the closing stores, as a quantity and as a share of everything received."""
    cols = ["item_code", "sent", "received", "share", "last_month"]
    if not ledger.closing:
        return pd.DataFrame(columns=cols)
    g = ledger.grid
    into = g[g["godown"].isin(ledger.closing) & (g["tr_in"] > 0)]
    sent = into.groupby("item_code")["tr_in"].sum()
    last = into.groupby("item_code")["month"].max()
    got = g.groupby("item_code")["received"].sum()
    out = pd.DataFrame({"item_code": ledger.items["item_code"]})
    out["sent"] = out["item_code"].map(sent).fillna(0.0)
    out["received"] = out["item_code"].map(got).fillna(0.0)
    out["share"] = [s / r if r > 0 else math.nan for s, r in zip(out["sent"], out["received"])]
    out["last_month"] = out["item_code"].map(last).fillna("")
    return out[cols]
