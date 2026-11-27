"""Money layer: hidden without rates, linear in the rates, and a saving that starves the site adds nothing.

Rates here are made up, on the made-up sample project only. No rupee figure for the real project is ever written into
a test."""

import pandas as pd
import pytest

from core import levers, money
from core.planner import Settings


@pytest.fixture(scope="module")
def tables(sample_ds):
    led = sample_ds.ledger
    return {c: levers.item_waterfall(led, c, sample_ds.boq_qty(c), Settings()) for c in ["SPO001", "SLU001", "SCA001"]}


def test_no_rates_no_rupees(tables):
    assert money.portfolio(tables, {}, 0.12).empty
    assert money.portfolio(tables, {"SPO001": "", "SLU001": 0, "SCA001": "abc"}, 0.12).empty
    assert money.clean_rates({"A": "12.5", "B": -1, "C": None, "D": float("nan")}) == {"A": 12.5}


def test_values_are_quantity_times_rate(tables):
    rows = tables["SPO001"]
    v = money.item_value(rows, 1000.0, 0.12)
    assert v["idle_value"].tolist() == pytest.approx((rows["avg_idle"] * 1000).tolist())
    assert v["carrying"].tolist() == pytest.approx((rows["avg_idle"] * 120).tolist())
    assert v["saving"].iloc[0] == 0
    double = money.portfolio(tables, {"SPO001": 2000.0}, 0.12)
    single = money.portfolio(tables, {"SPO001": 1000.0}, 0.12)
    assert double["idle_value"].tolist() == pytest.approx((2 * single["idle_value"]).tolist())


def test_a_saving_that_starves_the_site_adds_nothing(tables):
    starved = {c: t.copy() for c, t in tables.items()}
    starved["SLU001"].loc[starved["SLU001"].index[1:], "counts"] = False
    rates = {"SPO001": 1000.0, "SLU001": 5.0}
    p = money.portfolio(starved, rates, 0.12).set_index("step")
    honest = money.item_value(starved["SPO001"], 1000.0, 0.12).set_index("step")
    last = p.index[-1]
    assert p.loc[last, "items_counting"] == 1 and p.loc[last, "items_priced"] == 2
    assert p.loc[last, "saving_counts"] == pytest.approx(honest.loc[last, "saving"])


def test_rupees_reads_like_a_person_would_write_it():
    assert money.rupees(12345) == "₹12,345"
    assert money.rupees(4_56_789) == "₹4.57 lakh"
    assert money.rupees(-2_34_56_789) == "-₹2.35 crore"
    assert money.rupees(float("nan")) == "–"


def test_rates_never_change_the_quantities(tables):
    before = {c: t.copy() for c, t in tables.items()}
    money.portfolio(tables, {"SPO001": 999.0}, 0.2)
    for c in tables:
        pd.testing.assert_frame_equal(tables[c], before[c])
