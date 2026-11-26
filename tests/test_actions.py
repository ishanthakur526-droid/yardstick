"""Monday's list and the "what the stock report misses" panel."""

import math

import numpy as np
import pandas as pd
import pytest

from conftest import real
from core import ingest
from core.actions import item_actions, monday, position, week
from core.dataset import from_tables
from core.misses import closing_store, contractor_stock, contractor_stock_all, ratio_gaps
from core.planner import Settings
from core.sets import calibrate


def boqs(ds):
    return {c: ds.boq_qty(c) for c in ds.ledger.items["item_code"]}


def test_monday_is_the_first_monday_of_the_month():
    assert monday("2025-02") == pd.Timestamp("2025-02-03")
    assert monday("2024-07") == pd.Timestamp("2024-07-01")


def test_the_list_does_not_use_the_future(sample_raw, sample_ds):
    """Change everything from the as-of month on (use, issues, transfers) and every delivery that was not yet on
    order: the Monday list for that month must not move."""
    month = sample_ds.ledger.months[15]
    tables = ingest.monthly_tables(sample_raw)
    later = tables["used"]["month"] >= month
    tables["used"].loc[later, "used"] *= 5
    flows = tables["flows"]
    f_later = flows["month"] >= month
    for col in ("issue", "ret", "tr_in", "tr_out"):
        flows.loc[f_later, col] *= 3
    not_ordered = flows["month"] >= sample_ds.ledger.months[15 + 5]  # beyond the longest lead time (150 days)
    flows.loc[not_ordered, "received"] *= 4
    shaken = from_tables(tables, key="custom")
    assert shaken.ledger.months == sample_ds.ledger.months
    a = week(sample_ds.ledger, month, Settings(), boqs(sample_ds))
    b = week(shaken.ledger, month, Settings(), boqs(sample_ds))
    assert len(a) > 0
    pd.testing.assert_frame_equal(a, b)


def test_moves_come_before_orders_and_orders_stay_under_the_boq(sample_ds):
    ledger = sample_ds.ledger
    for i in range(9, len(ledger.months) - 1, 3):
        w = week(ledger, ledger.months[i], Settings(), boqs(sample_ds))
        kinds = list(w["kind"])
        assert kinds == sorted(kinds, key={"Move": 0, "Order": 1, "Hold issues": 2}.get)
        for code, grp in w[w["kind"] == "Order"].groupby("item_code"):
            p = position(ledger, code, i, Settings())
            assert grp["qty"].sum() <= sample_ds.boq_qty(code) - p.bought + 1e-6
        assert (w["qty"] > 0).all()


def test_contractors_holding_too_much_get_an_issue_hold(sample_ds):
    ledger = sample_ds.ledger
    held = ledger.site[ledger.site["item_code"] == "SPO001"].reset_index(drop=True)
    i = int(held["with_contractors"].to_numpy()[9:].argmax()) + 10  # the month after the biggest pile
    month = ledger.months[i]
    rows = item_actions(ledger, "SPO001", month, Settings(), sample_ds.boq_qty("SPO001"))
    hold = [r for r in rows if r["kind"] == "Hold issues"]
    assert hold and hold[0]["qty"] > 0
    drawn = item_actions(ledger, "SPO001", month, Settings(cover_weeks=None), sample_ds.boq_qty("SPO001"))
    assert not [r for r in drawn if r["kind"] == "Hold issues"]


def test_a_small_project_with_no_sets_or_closing_store(minimal_ds):
    ledger = minimal_ds.ledger
    w = week(ledger, ledger.months[6], Settings(takeover=4))
    assert set(w["kind"]) <= {"Move", "Order", "Hold issues"}
    assert closing_store(ledger).empty
    assert ratio_gaps(calibrate(ledger, minimal_ds.sets)) == {
        "pairs": 0,
        "disagree": 0,
        "unit_suspect": 0,
        "worst": None,
    }


def test_what_the_stock_report_misses_on_the_sample(sample_ds):
    ledger = sample_ds.ledger
    c = contractor_stock(ledger, "SPO001")
    held = ledger.site[ledger.site["item_code"] == "SPO001"]["with_contractors"]
    assert c["avg_held"] == pytest.approx(held.mean()) and c["peak_held"] == pytest.approx(held.max())
    assert 0 < c["share_of_idle"] < 1 and c["peak_months_of_use"] > 1
    assert len(contractor_stock_all(ledger)) == len(ledger.items)
    gaps = ratio_gaps(calibrate(ledger, sample_ds.sets))
    assert gaps["disagree"] >= 1 and gaps["worst"]["child_name"].startswith("ALUMINIUM LUG")
    assert gaps["worst"]["factor"] > 10
    cl = closing_store(ledger).set_index("item_code")
    assert cl.loc["SPO001", "sent"] > 0 and 0 < cl.loc["SPO001", "share"] < 1
    assert not math.isnan(cl.loc["SPO001", "share"])


@real
def test_real_project_monday_list_and_misses(real_ds):
    ledger = real_ds.ledger
    w = week(ledger, "2025-02", Settings())
    assert len(w) > 0 and np.isfinite(w["qty"]).all()
    gaps = ratio_gaps(calibrate(ledger, real_ds.sets))
    assert gaps["pairs"] == 8 and gaps["disagree"] == 7  # most of the master data's ratios disagree with the site
    assert gaps["unit_suspect"] == 3  # one bolt line, shared by three sets, stated in another unit (MT)
    assert "RING TYPE" in gaps["worst"]["child_name"] and 15 < gaps["worst"]["factor"] < 30
    c = contractor_stock(ledger, "MPO010014")
    assert c["peak_held"] > 10000
