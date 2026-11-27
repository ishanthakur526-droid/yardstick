"""Contractor holdings against the cover rule: leak-free, consistent with Monday's list, and silent without a rule."""

import math

import pandas as pd
import pytest

from conftest import real, shaken_future
from core import holdings
from core.actions import week
from core.planner import WEEKS_PER_MONTH, Settings


def test_series_reads_only_earlier_months(sample_raw, sample_ds):
    months = sample_ds.ledger.months
    shaken = shaken_future(sample_raw, months[15], months[15])
    for code in ("SPO001", "SLU001"):
        a = holdings.series(sample_ds.ledger, code)
        b = holdings.series(shaken.ledger, code)
        pd.testing.assert_frame_equal(a.iloc[:16], b.iloc[:16])  # up to and including the start of month 15
        assert not a.equals(b)


def test_the_rule_and_its_numbers(sample_ds):
    s = holdings.series(sample_ds.ledger, "SPO001", Settings(cover_weeks=2.0))
    site = sample_ds.ledger.site[sample_ds.ledger.site["item_code"] == "SPO001"].reset_index(drop=True)
    assert s["held"].iloc[0] == 0 and s["held"].iloc[10] == pytest.approx(site["with_contractors"].iloc[9])
    assert s["allowed"].iloc[10] == pytest.approx(2.0 / WEEKS_PER_MONTH * s["rate"].iloc[10])
    assert (s["alert"] == (s["excess"] > sample_ds.ledger.step("SPO001"))).all()
    assert s["alert"].any()  # the sample's contractors draw ahead, as the real project's did
    zero = holdings.series(sample_ds.ledger, "SPO001", Settings(cover_weeks=0.0))
    assert (zero["allowed"] == 0).all() and (zero["excess"] == zero["held"]).all()


def test_no_rule_no_alerts(sample_ds):
    s = holdings.series(sample_ds.ledger, "SPO001", Settings(cover_weeks=None))
    assert s["allowed"].map(math.isnan).all() and not s["alert"].any()


def test_alerts_match_mondays_issue_holds(sample_ds):
    ledger = sample_ds.ledger
    for month in ledger.months[10::6]:
        alerts = holdings.at(ledger, month)
        holds = week(ledger, month, Settings())
        holds = holds[holds["kind"] == "Hold issues"].set_index("item_code")
        assert set(alerts.loc[alerts["alert"], "item_code"]) == set(holds.index)
        for code, row in alerts[alerts["alert"]].set_index("item_code").iterrows():
            assert holds.loc[code, "qty"] == pytest.approx(row["excess"])


@real
def test_real_project_holdings(real_ds):
    a = holdings.at(real_ds.ledger, "2025-02")
    assert len(a) == 16 and a["alert"].sum() == 16  # Monday's list for Feb 2025 has 16 issue holds
    poles = a.set_index("item_code").loc["MPO010014"]
    assert poles["held"] > 10000 and poles["weeks_held"] > 8
