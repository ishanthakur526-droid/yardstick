"""Sum Lumino's raw files to month x item x godown and pack them (run locally; output is git-ignored).

    python tools/make_bundle.py <folder with the csv files> [out_file]

Rules from the data pack's README that this follows:
- identical-looking rows are separate entries, so rows are never de-duplicated;
- zero-quantity rows add nothing, so they drop out of the sums;
- CONSUMPTION rows have no godown, so usage is summed per item for the whole project.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.bundle import LOCAL_FILE, pack  # noqa: E402


def monthly_tables(csv_dir: str | Path) -> dict[str, pd.DataFrame]:
    d = Path(csv_dir)
    items = pd.read_csv(d / "items.csv")
    sets = pd.read_csv(d / "sets.csv")
    rec = pd.read_csv(d / "receipts.csv", parse_dates=["date"])
    tr = pd.read_csv(d / "transfers.csv", parse_dates=["date"])
    cm = pd.read_csv(d / "contractor_moves.csv", parse_dates=["date"])

    def month(df: pd.DataFrame) -> pd.Series:
        return df["date"].dt.strftime("%Y-%m")

    keys = ["item_code", "godown", "month"]
    parts = [
        rec.assign(month=month(rec)).groupby(keys)["qty_received"].sum().rename("received"),
        tr[tr.direction == "IN"]
        .assign(month=month(tr[tr.direction == "IN"]))
        .groupby(keys)["qty"]
        .sum()
        .rename("tr_in"),
        tr[tr.direction == "OUT"]
        .assign(month=month(tr[tr.direction == "OUT"]))
        .groupby(keys)["qty"]
        .sum()
        .rename("tr_out"),
    ]
    for kind, col in (("ISSUE", "issue"), ("RETURN", "ret")):
        sub = cm[cm["type"] == kind]
        parts.append(sub.assign(month=month(sub)).groupby(keys)["qty"].sum().rename(col))
    flows = pd.concat(parts, axis=1).fillna(0.0).reset_index()

    use = cm[cm["type"] == "CONSUMPTION"]
    used = use.assign(month=month(use)).groupby(["item_code", "month"])["qty"].sum().rename("used").reset_index()
    return {"items": items, "sets": sets, "flows": flows, "used": used}


if __name__ == "__main__":
    src = sys.argv[1]
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else LOCAL_FILE
    tables = monthly_tables(src)
    out.parent.mkdir(parents=True, exist_ok=True)
    blob = pack(tables)
    out.write_text(blob, encoding="utf-8")
    print(f"wrote {out} ({len(blob) / 1024:.0f} KB) | " + ", ".join(f"{k}: {len(v)} rows" for k, v in tables.items()))
