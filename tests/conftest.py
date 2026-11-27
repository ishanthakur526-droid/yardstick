"""Shared fixtures: the sample project, a small project shaped nothing like the original, and the real one if present."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import ingest, synth  # noqa: E402
from core.bundle import find_blob, unpack  # noqa: E402
from core.dataset import Dataset, from_tables  # noqa: E402

BLOB = find_blob()
real = pytest.mark.skipif(BLOB is None, reason="the private project bundle is not available here")


def minimal_raw(months: int = 12) -> dict[str, pd.DataFrame]:
    """Three items, two depots, a year: no sets, no transfers, no meta, different codes, units and store names."""
    items = pd.DataFrame(
        {
            "item_code": ["A100", "B200", "C300"],
            "item_name": ["STEEL BRACKET", "COPPER WIRE 16 SQMM", "DISC INSULATOR"],
            "unit": ["nos", "KM", "NOS"],
            "lead_time_days": [45, 60, 30],
        }
    )
    dates = pd.date_range("2025-01-05", periods=months, freq="MS") + pd.Timedelta(days=4)
    rec, moves = [], []
    for i, d in enumerate(dates):
        for code, base in (("A100", 40.0), ("B200", 1.5), ("C300", 120.0)):
            used = base * (1 + 0.3 * ((i % 4) - 1.5) / 1.5)
            for depot, share in (("EAST DEPOT", 0.6), ("WEST DEPOT", 0.4)):
                if i % 2 == 0:
                    rec.append(
                        {"date": d, "item_code": code, "godown": depot, "qty_received": round(base * 2.6 * share, 3)}
                    )
                moves.append(
                    {
                        "date": d,
                        "type": "ISSUE",
                        "item_code": code,
                        "godown": depot,
                        "qty": round(used * share * 1.1, 3),
                    }
                )
            moves.append({"date": d, "type": "CONSUMPTION", "item_code": code, "godown": None, "qty": round(used, 3)})
    return {"items": items, "receipts": pd.DataFrame(rec), "contractor_moves": pd.DataFrame(moves)}


def shaken_future(raw: dict[str, pd.DataFrame], month: str, ordered_until: str) -> Dataset:
    """The same project with everything from `month` on changed (use, issues, returns, transfers) and every delivery
    from `ordered_until` on (not yet ordered at `month`) changed too. Anything decided as of `month` must not move."""
    tables = ingest.monthly_tables(raw)
    used = tables["used"]
    used.loc[used["month"] >= month, "used"] *= 5
    flows = tables["flows"]
    for col in ("issue", "ret", "tr_in", "tr_out"):
        flows.loc[flows["month"] >= month, col] *= 3
    flows.loc[flows["month"] >= ordered_until, "received"] *= 4
    return from_tables(tables, key="custom")


@pytest.fixture(scope="session")
def sample_raw() -> dict[str, pd.DataFrame]:
    return synth.make_pack()


@pytest.fixture(scope="session")
def sample_ds(sample_raw) -> Dataset:
    return from_tables(ingest.monthly_tables(sample_raw), key="sample")


@pytest.fixture(scope="session")
def minimal_ds() -> Dataset:
    return from_tables(ingest.monthly_tables(minimal_raw()), key="custom")


@pytest.fixture(scope="session")
def real_ds() -> Dataset:
    if BLOB is None:
        pytest.skip("the private project bundle is not available here")
    return from_tables(unpack(BLOB), key="bundle")
