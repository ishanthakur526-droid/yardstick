"""From a project's raw records to the monthly tables the app runs on.

The raw pack is the layout of the data pack Yardstick was first built on, which is what an ERP extract of stores
transactions looks like:

  items              item_code, item_name, unit, lead_time_days          [role, decimals]
  sets (optional)    set_no, parent_code, child_code, ratio_of_parent     [parent_name, parent_unit, child_name, child_unit]
  receipts           date, item_code, godown, qty_received                [grn_no, item_name, unit]
  transfers (opt.)   date, direction (IN/OUT), item_code, godown, qty     [voucher_no, voucher_type, godown_code, ...]
  contractor_moves   date, type (ISSUE/RETURN/CONSUMPTION), item_code, godown, qty   (CONSUMPTION has no godown)

plus, if given, `meta` (key, value: see core/config.py) and `boq` (item_code, qty).

Rules, from the original data pack's README and sound for any stores extract:
- identical-looking rows are separate entries, so rows are never de-duplicated;
- zero-quantity rows add nothing, so they drop out of the sums;
- CONSUMPTION rows have no godown, so usage is summed per item for the whole project.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

TABLES = ("items", "sets", "receipts", "transfers", "contractor_moves", "meta", "boq")
REQUIRED_TABLES = ("items", "receipts", "contractor_moves")
SETS_COLUMNS = [
    "set_no",
    "parent_code",
    "parent_name",
    "parent_unit",
    "child_code",
    "child_name",
    "child_unit",
    "ratio_of_parent",
]
FLOW_KEYS = ["item_code", "godown", "month"]
FLOW_COLS = ["received", "tr_in", "tr_out", "issue", "ret"]


TEXT_COLUMNS = (
    "item_code",
    "parent_code",
    "child_code",
    "godown",
    "godown_code",
    "grn_no",
    "voucher_no",
    "key",
    "value",
)


def read_csv_text(source, **kw) -> pd.DataFrame:
    """Read a CSV with codes and names as text, so a code like 0100 keeps its leading zero."""
    header = pd.read_csv(source, nrows=0, **kw).columns
    if hasattr(source, "seek"):
        source.seek(0)
    return pd.read_csv(source, dtype={c: str for c in TEXT_COLUMNS if c in header}, **kw)


def read_folder(folder: str | Path) -> dict[str, pd.DataFrame]:
    """Every known table found as <name>.csv in `folder` (for the command line)."""
    d = Path(folder)
    found = {name: read_csv_text(d / f"{name}.csv") for name in TABLES if (d / f"{name}.csv").exists()}
    missing = [t for t in REQUIRED_TABLES if t not in found]
    if missing:
        raise FileNotFoundError(f"{d} has no {', '.join(m + '.csv' for m in missing)}")
    return found


def _text(s: pd.Series) -> pd.Series:
    return s.where(s.isna(), s.astype(str).str.strip())


EARLIEST, LATEST = pd.Timestamp("1990-01-01"), pd.Timestamp("2100-12-31")  # a date outside is a typo, not a project


def parse_dates(s: pd.Series) -> pd.Series:
    """Dates as timestamps; NaT where a value is not a date, or is outside 1990 to 2100 (a mistyped year such as 0202
    or 9024 would otherwise stretch the project over centuries of empty months)."""
    d = _read_dates(s)
    if not pd.api.types.is_datetime64_any_dtype(d):
        return d
    naive = d.dt.tz_localize(None) if d.dt.tz is not None else d
    return d.where(naive.between(EARLIEST, LATEST))


def _read_dates(s: pd.Series) -> pd.Series:
    """ISO dates first (2024-01-31); if most do not read that way, day-first dates as Indian ERPs write them (31-01-2024),
    or Excel's day numbers (45322) when a spreadsheet stored the dates as plain numbers."""
    iso = pd.to_datetime(s, errors="coerce", format="ISO8601")
    if iso.isna().mean() <= 0.5:
        return iso
    day_first = pd.to_datetime(s, errors="coerce", dayfirst=True, format="mixed")
    best = day_first if day_first.notna().sum() > iso.notna().sum() else iso
    numbers = pd.to_numeric(s, errors="coerce")
    serial = numbers.between(20_000, 80_000)  # Excel's day numbers for the years 1954 to 2119
    if serial.mean() > 0.5 and serial.sum() > best.notna().sum():
        return pd.to_datetime(numbers.where(serial), unit="D", origin="1899-12-30")
    return best


def _dated(df: pd.DataFrame | None, qty: str) -> pd.DataFrame:
    """Parse dates and quantities; rows without a usable date or quantity cannot be placed in a month and are dropped."""
    if df is None or df.empty:
        return pd.DataFrame(columns=["month", "item_code", "godown", qty])
    out = df.copy()
    out["item_code"] = _text(out["item_code"])
    if "godown" in out:
        out["godown"] = _text(out["godown"])
    out[qty] = pd.to_numeric(out[qty], errors="coerce")
    dates = parse_dates(out["date"])
    out["month"] = dates.dt.strftime("%Y-%m")
    return out[dates.notna() & out[qty].notna()]


def clean_items(items: pd.DataFrame) -> pd.DataFrame:
    out = items.copy()
    out["item_code"] = _text(out["item_code"])
    out["item_name"] = _text(out["item_name"]).fillna(out["item_code"])
    out["unit"] = _text(out["unit"]).fillna("").str.upper()
    out["lead_time_days"] = pd.to_numeric(out["lead_time_days"], errors="coerce")
    if "role" not in out:
        out["role"] = "Independent"
    out["role"] = _text(out["role"]).fillna("Independent")
    return out.drop_duplicates("item_code").reset_index(drop=True)


def clean_sets(sets: pd.DataFrame | None, items: pd.DataFrame) -> pd.DataFrame:
    """Sets whose parent and child are both items of this project, names and units filled in from the items table."""
    if sets is None or sets.empty:
        return pd.DataFrame(columns=SETS_COLUMNS)
    out = sets.copy()
    for col in ("parent_code", "child_code"):
        out[col] = _text(out[col])
    out["ratio_of_parent"] = pd.to_numeric(out["ratio_of_parent"], errors="coerce")
    known = set(items["item_code"])
    out = out[
        out["parent_code"].isin(known)
        & out["child_code"].isin(known)
        & (out["ratio_of_parent"] > 0)
        & (out["parent_code"] != out["child_code"])
    ].copy()
    by = items.set_index("item_code")
    for side in ("parent", "child"):
        for field, src in (("name", "item_name"), ("unit", "unit")):
            col = f"{side}_{field}"
            filled = out[f"{side}_code"].map(by[src])
            out[col] = filled if col not in out else _text(out[col]).fillna(filled)
    if "set_no" not in out:
        out["set_no"] = pd.factorize(out["parent_code"])[0] + 1.0
    return out[SETS_COLUMNS].reset_index(drop=True)


def monthly_tables(raw: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Sum the raw records to month x item x godown (`flows`) and month x item (`used`)."""
    items = clean_items(raw["items"])
    sets = clean_sets(raw.get("sets"), items)
    rec = _dated(raw["receipts"], "qty_received")
    tr = _dated(raw.get("transfers"), "qty")
    cm = _dated(raw["contractor_moves"], "qty")
    if "direction" in tr:
        tr["direction"] = tr["direction"].astype(str).str.strip().str.upper()
    cm["type"] = cm["type"].astype(str).str.strip().str.upper()

    parts = [rec.groupby(FLOW_KEYS)["qty_received"].sum().rename("received")]
    if "direction" in tr:
        for way, col in (("IN", "tr_in"), ("OUT", "tr_out")):
            parts.append(tr[tr["direction"] == way].groupby(FLOW_KEYS)["qty"].sum().rename(col))
    for kind, col in (("ISSUE", "issue"), ("RETURN", "ret")):
        sub = cm[(cm["type"] == kind) & cm["godown"].notna()]
        parts.append(sub.groupby(FLOW_KEYS)["qty"].sum().rename(col))
    flows = pd.concat(parts, axis=1).fillna(0.0).reset_index()
    for col in FLOW_COLS:
        if col not in flows:
            flows[col] = 0.0
    flows = flows[FLOW_KEYS + FLOW_COLS]

    use = cm[cm["type"] == "CONSUMPTION"]
    used = use.groupby(["item_code", "month"])["qty"].sum().rename("used").reset_index()

    out = {"items": items, "sets": sets, "flows": flows, "used": used}
    for extra in ("meta", "boq"):
        if raw.get(extra) is not None and not raw[extra].empty:
            out[extra] = raw[extra].copy()
    return out
