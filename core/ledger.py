"""The monthly ledger: for every item and every godown, what came in, went out, was used and sat idle.

Two sums from the data pack's README drive everything:

    godown stock      = receipts + transfers IN - transfers OUT - ISSUE + RETURN      (cumulative, per godown)
    with contractors  = ISSUE - RETURN - CONSUMPTION                                  (cumulative, whole project)

Usage (CONSUMPTION) has no godown, so "used" exists per item for the whole project only.
Where a running sum dips below zero (a late entry), it is shown as zero, as the README says.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

FLOW_COLS = ["received", "tr_in", "tr_out", "issue", "ret"]
# Units counted in fractions (tonnes, kilometres...) keep two decimals; everything else is bought in whole units.
# An optional `decimals` column on the items table overrides this per item.
CONTINUOUS_UNITS = {"MT", "KM", "TON", "TONS", "TONNE", "TONNES", "KL", "M3", "CUM"}


def unit_decimals(unit: str) -> int:
    return 2 if str(unit).strip().upper() in CONTINUOUS_UNITS else 0


@dataclass
class Ledger:
    items: pd.DataFrame  # item_code, item_name, unit, lead_time_days, role, decimals
    months: list[str]  # every month of the project, 'YYYY-MM', no gaps
    godowns: list[str]  # locations in the data, closing stores last
    grid: pd.DataFrame  # item x godown x month
    site: pd.DataFrame  # item x month, whole project
    closing: tuple[str, ...] = ()  # locations that only take leftovers at wind-down: never planned for
    labels: dict[str, str] = field(default_factory=dict)  # godown -> short label for the screen
    _by_code: dict = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self._by_code = self.items.set_index("item_code").to_dict("index")

    @property
    def working(self) -> list[str]:
        """The godowns that serve the site (closing stores left out)."""
        return [g for g in self.godowns if g not in self.closing]

    def item_name(self, code: str) -> str:
        return str(self._by_code[code]["item_name"])

    def unit(self, code: str) -> str:
        return str(self._by_code[code]["unit"])

    def lead_days(self, code: str) -> float:
        return float(self._by_code[code]["lead_time_days"])

    def decimals(self, code: str) -> int:
        return int(self._by_code[code]["decimals"])

    def step(self, code: str) -> float:
        """The smallest quantity this item is ordered in: 1 for whole units, 0.01 for two decimals."""
        return 10.0 ** -self.decimals(code)

    def label(self, godown: str) -> str:
        return self.labels.get(godown, str(godown).title())


def godown_labels(godowns: list[str], closing: Iterable[str] = ()) -> dict[str, str]:
    """Short names for the screen: a word every working godown starts with (the city, say) is dropped."""
    closing = [g for g in godowns if g in set(closing)]
    working = [g for g in godowns if g not in closing]
    words = [str(g).split() for g in working]
    keep = 0
    if len(words) >= 2 and all(words):
        for i in range(min(len(w) for w in words) - 1):  # always leave at least one word
            if len({w[i].upper() for w in words}) != 1:
                break
            keep = i + 1
    labels = {g: (" ".join(w[keep:]) or str(g)).title() for g, w in zip(working, words)}
    for g in closing:
        labels[g] = "Closing store" if len(closing) == 1 else " ".join(str(g).split()[keep:] or [str(g)]).title()
    if len(set(labels.values())) < len(labels):
        labels = {g: str(g).title() for g in godowns}
    return labels


def month_range(*month_columns: pd.Series) -> list[str]:
    seen = pd.concat(month_columns).dropna().unique()
    periods = pd.PeriodIndex(seen, freq="M")
    return [str(p) for p in pd.period_range(periods.min(), periods.max(), freq="M")]


def build_ledger(flows: pd.DataFrame, used: pd.DataFrame, items: pd.DataFrame, closing: Iterable[str] = ()) -> Ledger:
    closing = set(closing)
    months = month_range(flows["month"], used["month"])
    places = sorted(flows["godown"].unique(), key=lambda g: (g in closing, g))

    idx = pd.MultiIndex.from_product([items["item_code"], places, months], names=["item_code", "godown", "month"])
    grid = flows.set_index(["item_code", "godown", "month"])[FLOW_COLS].reindex(idx).fillna(0.0).reset_index()
    grid["came_in"] = grid["received"] + grid["tr_in"]
    grid["net_issue"] = grid["issue"] - grid["ret"]
    delta = grid["received"] + grid["tr_in"] - grid["tr_out"] - grid["issue"] + grid["ret"]
    grid["stock_raw"] = delta.groupby([grid["item_code"], grid["godown"]]).cumsum()
    grid["stock"] = grid["stock_raw"].clip(lower=0.0)

    site = (
        grid.groupby(["item_code", "month"])[FLOW_COLS + ["came_in", "stock"]]
        .sum()
        .rename(columns={"stock": "godown_stock"})
    )
    used_m = used.set_index(["item_code", "month"])["used"].reindex(site.index).fillna(0.0)
    site["used"] = used_m
    site["with_contractors_raw"] = (site["issue"] - site["ret"] - site["used"]).groupby(level=0).cumsum()
    site["with_contractors"] = site["with_contractors_raw"].clip(lower=0.0)
    site["idle_total"] = site["godown_stock"] + site["with_contractors"]
    recent = site["used"].groupby(level=0).transform(lambda s: s.rolling(3, min_periods=1).mean())
    site["cover_months"] = np.where(recent > 0, site["idle_total"] / recent.where(recent > 0), np.nan)
    site = site.reset_index()

    ordered = items.copy()
    given = (
        pd.to_numeric(ordered["decimals"], errors="coerce")
        if "decimals" in ordered
        else pd.Series(np.nan, index=ordered.index)
    )
    ordered["decimals"] = [
        int(min(max(d, 0), 4)) if pd.notna(d) else unit_decimals(u) for d, u in zip(given, ordered["unit"])
    ]
    shut = tuple(g for g in places if g in closing)
    return Ledger(
        items=ordered,
        months=months,
        godowns=places,
        grid=grid,
        site=site,
        closing=shut,
        labels=godown_labels(places, shut),
    )


def item_site(ledger: Ledger, code: str) -> pd.DataFrame:
    return ledger.site[ledger.site["item_code"] == code].reset_index(drop=True)


def item_grid(ledger: Ledger, code: str, godown: str | None = None) -> pd.DataFrame:
    g = ledger.grid[ledger.grid["item_code"] == code]
    if godown:
        g = g[g["godown"] == godown]
    return g.reset_index(drop=True)


def peak_idle(ledger: Ledger, code: str) -> tuple[str, float]:
    s = item_site(ledger, code)
    row = s.loc[s["idle_total"].idxmax()]
    return str(row["month"]), float(row["idle_total"])


def overview(ledger: Ledger) -> pd.DataFrame:
    """One row per item: the whole story at a glance."""
    rows = []
    for _, it in ledger.items.iterrows():
        s = item_site(ledger, it["item_code"])
        peak_month, peak = peak_idle(ledger, it["item_code"])
        last = s.iloc[-1]
        rows.append(
            {
                "item_code": it["item_code"],
                "item": it["item_name"],
                "unit": it["unit"],
                "lead_time_days": it["lead_time_days"],
                "role": it["role"],
                "received": s["received"].sum(),
                "used": s["used"].sum(),
                "idle_now": last["idle_total"],
                "peak_idle": peak,
                "peak_month": peak_month,
                "used_that_month": float(s.loc[s["month"] == peak_month, "used"].iloc[0]),
            }
        )
    return pd.DataFrame(rows)


def ledger_table(ledger: Ledger, code: str, godown: str | None = None) -> pd.DataFrame:
    """The month-by-month table the screen shows: whole project, or one godown."""
    if godown is None:
        s = item_site(ledger, code)
        return pd.DataFrame(
            {
                "Month": s["month"],
                "Arrived from vendors": s["received"],
                "Issued to contractors": s["issue"],
                "Returned by contractors": s["ret"],
                "Actually used": s["used"],
                "Idle in godowns": s["godown_stock"],
                "Held by contractors": s["with_contractors"],
                "Total idle": s["idle_total"],
                "Months of cover": s["cover_months"],
            }
        )
    g = item_grid(ledger, code, godown)
    return pd.DataFrame(
        {
            "Month": g["month"],
            "Arrived from vendors": g["received"],
            "Transfers in": g["tr_in"],
            "Transfers out": g["tr_out"],
            "Issued to contractors": g["issue"],
            "Returned by contractors": g["ret"],
            "Idle in this godown": g["stock"],
        }
    )


def data_notes(ledger: Ledger) -> dict[str, object]:
    """Things a careful reader should know about the data (shown on the About tab)."""
    g = ledger.grid
    dipped = g[g["stock_raw"] < -1e-9]
    return {
        "godown_months_below_zero": int(len(dipped)),
        "worst_dip": float(dipped["stock_raw"].min()) if len(dipped) else 0.0,
        "months": f"{ledger.months[0]} to {ledger.months[-1]}",
        "locations": len(ledger.godowns),
        "items": len(ledger.items),
    }
