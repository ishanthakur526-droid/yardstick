"""From a bill of quantities to a buy list: what must be bought from outside, and by when.

Head-office procurement starts from the BOQ (how many poles, say, plus any other lines the tender lists), works out
every part that is needed, takes away what is already in the stores, and buys the rest in time for the deadline.
This module does that sum and records where every number came from.

A needed quantity comes from the first of these that applies:

  BOQ line    typed on the BOQ. The BOQ is the requirement; nothing here overrides it.
  Set ratio   a child of a set (eye hook, lugs, bolts): the parent's quantity times the ratio, either as the master
              data file gives it or as the site really used it. The two often disagree, so both are kept and shown
              next to each other.
  Per anchor  any other item, from what this project used per anchor item (per pole), when the anchor is on the BOQ.
              An estimate.

Stock in hand is the last month of the data (godowns and the closing store). Stock still with contractors counts only
when asked for. Quantities that are bought in whole units are rounded up.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

from .ledger import Ledger
from .sets import AGREES_BETWEEN, calibrate

BASES = {"observed": "As the site really used them", "stated": "As in the master data file"}
BOQ, FILE, SITE = "BOQ line", "Set ratio (file)", "Set ratio (site)"


def per_anchor_label(noun: str) -> str:
    return f"Per {noun.strip() or 'unit'} (site)"


COLUMNS = [
    "item_code",
    "item",
    "unit",
    "source",
    "needed",
    "at_file",
    "at_site",
    "factor",
    "check",
    "in_godowns",
    "with_contractors",
    "on_hand",
    "buy",
    "left_over",
    "lead_days",
    "order_by",
    "send_by",
    "late_days",
    "kind",
    "status",
]


@dataclass
class Requisition:
    lines: pd.DataFrame  # one row per item that is needed: late first, then to order, then covered by stock
    stock_month: str  # the month the stock figures are from
    today: date
    needed_by: date
    basis: str
    with_contractors: bool
    unknown: list[str] = field(default_factory=list)  # BOQ lines whose item code is not in the data
    handoff_days: int = 0  # days procurement needs to place an order once it has the list


def _up(x: float, step: float) -> float:
    return math.ceil(round(x / step, 9)) * step


def ratio_check(factor: float) -> str:
    """Plain words for how the site's use compares with the file's ratio (factor = site / file)."""
    if not np.isfinite(factor):
        return "No use recorded to compare."
    if AGREES_BETWEEN[0] <= factor <= AGREES_BETWEEN[1]:
        return "The file and the site agree."
    if factor < 0.01:
        return f"The site used {factor:.3%} of the file's ratio. Check the unit (kg or tonnes?)."
    if factor < 1:
        return f"The site used {factor:.0%} of the file's ratio."
    if factor > 100:
        return f"The site used {factor:,.0f}× the file's ratio. Check the unit (kg or tonnes?)."
    return f"The site used {factor:.1f}× the file's ratio."


def requisition(
    ledger: Ledger,
    sets: pd.DataFrame,
    boq: dict[str, float],
    *,
    basis: str = "observed",
    per_anchor: bool = True,
    with_contractors: bool = False,
    today: date | None = None,
    days_left: int = 90,
    handoff_days: int = 0,
    anchor_code: str | None = None,
    anchor_noun: str = "pole",
) -> Requisition:
    """`boq` maps item code to quantity. `days_left` is how long until the material must be on site; `handoff_days` is how
    long procurement needs to place an order once it has the list (procurement teams typically ask for 7 to 10).
    `anchor_code` is the item the BOQ counts in; other items can be estimated per unit of it."""
    if basis not in BASES:
        raise ValueError(f"basis must be one of {list(BASES)}")
    today = today or date.today()
    needed_by = today + timedelta(days=int(days_left))
    items = ledger.items.set_index("item_code")

    wanted = {c: float(q) for c, q in boq.items() if q is not None and pd.notna(q) and float(q) > 0}
    typed = {c: q for c, q in wanted.items() if c in items.index}
    unknown = [c for c in wanted if c not in items.index]

    cal = (
        calibrate(ledger, sets)
        if len(sets)
        else pd.DataFrame(columns=["parent_code", "child_code", "stated", "observed", "factor"])
    )
    children = set(cal["child_code"])
    used = ledger.site.groupby("item_code")["used"].sum()
    need, source = dict(typed), {c: BOQ for c in typed}

    anchors = typed.get(anchor_code, 0.0) if anchor_code else 0.0
    anchor_used = float(used.get(anchor_code, 0.0)) if anchor_code else 0.0
    if per_anchor and anchors > 0 and anchor_used > 0:
        label = per_anchor_label(anchor_noun)
        for code in items.index:
            if code not in need and code not in children and used.get(code, 0.0) > 0:
                need[code], source[code] = anchors * float(used[code]) / anchor_used, label

    at_file: dict[str, float] = {}
    at_site: dict[str, float] = {}
    factor: dict[str, float] = {}
    for child, grp in cal.groupby("child_code", sort=False):
        grp = grp[grp["parent_code"].isin(need)]
        if grp.empty:
            continue
        qty = grp["parent_code"].map(need)
        at_file[child] = float((qty * grp["stated"]).sum())
        at_site[child] = float((qty * grp["observed"].fillna(grp["stated"])).sum())
        factor[child] = float(grp["factor"].iloc[0])
        if child not in need:
            need[child] = at_file[child] if basis == "stated" else at_site[child]
            source[child] = FILE if basis == "stated" else SITE

    last = ledger.months[-1]
    now = ledger.site[ledger.site["month"] == last].set_index("item_code")
    nan = float("nan")
    rows = []
    for code, qty in need.items():
        unit, step = str(items.loc[code, "unit"]), ledger.step(code)
        in_godowns = float(now.loc[code, "godown_stock"]) if code in now.index else 0.0
        held = float(now.loc[code, "with_contractors"]) if code in now.index else 0.0
        on_hand = in_godowns + (held if with_contractors else 0.0)
        missing = max(0.0, qty - on_hand)
        lead = int(items.loc[code, "lead_time_days"])
        order_by = needed_by - timedelta(days=lead)
        send_by = order_by - timedelta(days=int(handoff_days))
        late = max(0, (today - send_by).days)
        if missing <= 1e-9:
            kind, status = "covered", "In stock"
        elif late > 0:
            kind, status = "late", f"Too late by {late} days"
        else:
            kind, status = (
                "order",
                (f"Send to procurement by {send_by:%d %b}" if handoff_days else f"Order by {order_by:%d %b}"),
            )
        rows.append(
            {
                "item_code": code,
                "item": str(items.loc[code, "item_name"]),
                "unit": unit,
                "source": source[code],
                "needed": _up(qty, step),
                "at_file": at_file.get(code, nan),
                "at_site": at_site.get(code, nan),
                "factor": factor.get(code, nan),
                "check": ratio_check(factor[code]) if code in factor else "",
                "in_godowns": in_godowns,
                "with_contractors": held,
                "on_hand": on_hand,
                "buy": _up(missing, step) if missing > 1e-9 else 0.0,
                "left_over": max(0.0, on_hand - qty),
                "lead_days": lead,
                "order_by": order_by,
                "send_by": send_by,
                "late_days": late,
                "kind": kind,
                "status": status,
            }
        )

    lines = pd.DataFrame(rows, columns=COLUMNS)
    if len(lines):
        rank = {"late": 0, "order": 1, "covered": 2}
        place = {c: i for i, c in enumerate(items.index)}
        lines = (
            lines.assign(_k=lines["kind"].map(rank), _i=lines["item_code"].map(place))
            .sort_values(["_k", "_i"])
            .drop(columns=["_k", "_i"])
            .reset_index(drop=True)
        )
    return Requisition(
        lines=lines,
        stock_month=last,
        today=today,
        needed_by=needed_by,
        basis=basis,
        with_contractors=with_contractors,
        unknown=unknown,
        handoff_days=int(handoff_days),
    )


def summary(req: Requisition) -> dict[str, int]:
    kinds = req.lines["kind"]
    return {
        "lines": int(len(kinds)),
        "covered": int((kinds == "covered").sum()),
        "to_buy": int((kinds != "covered").sum()),
        "late": int((kinds == "late").sum()),
    }


def _column(cols: dict[str, object], *hints: str, avoid: str = ""):
    for key, original in cols.items():
        if any(h in key for h in hints) and not (avoid and avoid in key):
            return original
    return None


def parse_boq(table: pd.DataFrame, items: pd.DataFrame) -> tuple[dict[str, float], list[str]]:
    """Read a BOQ sheet with an item column (code or name) and a quantity column.

    Returns the quantities by item code, and the rows that matched no item. Rows without a positive quantity are skipped.
    """
    cols = {str(c).strip().lower(): c for c in table.columns}
    code_col = _column(cols, "code")
    name_col = _column(cols, "item", "name", "description", "material", avoid="code")
    qty_col = _column(cols, "qty", "quant")
    if qty_col is None or (code_col is None and name_col is None):
        raise ValueError("The sheet needs an item column (code or name) and a quantity column.")
    by_code = {str(c).strip().casefold(): c for c in items["item_code"]}
    by_name = {" ".join(str(n).split()).casefold(): c for c, n in zip(items["item_code"], items["item_name"])}
    got: dict[str, float] = {}
    missed: list[str] = []
    for _, row in table.iterrows():
        raw = row[qty_col]
        qty = pd.to_numeric(raw.replace(",", "").strip() if isinstance(raw, str) else raw, errors="coerce")
        if pd.isna(qty) or qty <= 0:
            continue
        key_code = str(row[code_col]).strip().casefold() if code_col is not None else ""
        key_name = " ".join(str(row[name_col]).split()).casefold() if name_col is not None else ""
        code = by_code.get(key_code) or by_name.get(key_name)
        if code is None:
            missed.append(str(row[code_col] if code_col is not None else row[name_col]))
            continue
        got[code] = got.get(code, 0.0) + float(qty)
    return got, missed


MAX_BOQ_LINES = 20_000  # a BOQ lists items, not transactions: far beyond this, it is some other file


def read_boq(name: str, data: bytes, items: pd.DataFrame) -> tuple[dict[str, float], list[str], str]:
    """A BOQ file from a visitor, read with the same safe reader and limits as the Your data screen.

    Returns the quantities by item code, the lines that matched no item, and why the file could not be used ("" when it
    could). The first sheet with an item column and a quantity column is used. It never raises.
    """
    from . import upload  # here, not at the top: upload -> export -> requisition would be a cycle

    why = "it needs an item column (code or name) and a quantity column"
    for sheet in upload.read_upload(name, data):
        if sheet.problem:
            why = sheet.problem
            continue
        if len(sheet.frame) > MAX_BOQ_LINES:
            why = f"it has more than {MAX_BOQ_LINES:,} lines, far more than a BOQ"
            continue
        try:
            got, missed = parse_boq(sheet.frame, items)
        except ValueError:
            continue
        return got, missed, ""
    return {}, [], why
