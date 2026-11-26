"""One project's data, ready for every screen: the monthly ledger, the sets, the settings and a fingerprint.

The fingerprint is a hash of the tables. Every cached result is keyed by it, so two different projects (or a user's
upload and the built-in data) can never be served each other's numbers.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import pandas as pd

from .config import ProjectConfig, build_config
from .ingest import SETS_COLUMNS, clean_items, clean_sets
from .ledger import Ledger, build_ledger


@dataclass
class Dataset:
    key: str  # where it came from: "bundle", "sample" or "custom"
    cfg: ProjectConfig
    ledger: Ledger
    sets: pd.DataFrame
    boq: pd.DataFrame  # item_code, qty: the tender's quantities, when the data has them
    fingerprint: str
    tables: dict[str, pd.DataFrame] = field(default_factory=dict, repr=False)


def fingerprint(tables: dict[str, pd.DataFrame]) -> str:
    h = hashlib.sha256()
    for name in sorted(tables):
        h.update(name.encode())
        h.update(tables[name].to_csv(index=False).encode())
    return h.hexdigest()[:16]


def from_tables(tables: dict[str, pd.DataFrame], key: str) -> Dataset:
    """`tables` holds items, flows and used, and optionally sets, meta and boq (see core/ingest.py)."""
    items = clean_items(tables["items"])
    sets = (
        clean_sets(tables.get("sets"), items) if tables.get("sets") is not None else pd.DataFrame(columns=SETS_COLUMNS)
    )
    flows, used = tables["flows"].copy(), tables["used"].copy()
    for df in (flows, used):
        df["item_code"] = df["item_code"].astype(str).str.strip()
        df["month"] = df["month"].astype(str).str.strip()
    flows["godown"] = flows["godown"].astype(str).str.strip()
    flows = flows[flows["item_code"].isin(set(items["item_code"]))]
    used = used[used["item_code"].isin(set(items["item_code"]))]
    if flows.empty:
        raise ValueError("no stock movements for any item in the items table")

    clean = {"items": items, "sets": sets, "flows": flows, "used": used}
    for extra in ("meta", "boq"):
        if tables.get(extra) is not None:
            clean[extra] = tables[extra]
    cfg = build_config(clean)
    ledger = build_ledger(flows, used, items, closing=cfg.closing_stores)
    boq = tables.get("boq")
    if boq is None or not {"item_code", "qty"} <= set(boq.columns):
        boq = pd.DataFrame(columns=["item_code", "qty"])
    else:
        boq = boq[["item_code", "qty"]].assign(
            item_code=lambda d: d["item_code"].astype(str).str.strip(),
            qty=lambda d: pd.to_numeric(d["qty"], errors="coerce"),
        )
        boq = boq[boq["item_code"].isin(set(items["item_code"])) & (boq["qty"] > 0)]
    return Dataset(
        key=key,
        cfg=cfg,
        ledger=ledger,
        sets=sets,
        boq=boq.reset_index(drop=True),
        fingerprint=fingerprint(clean),
        tables=clean,
    )
