"""Plan children from their parents: the learned ratio is expanding (no look-ahead), and the bake-off adopts a child's
set forecast only when it is no worse than the child's own."""

import numpy as np
import pandas as pd
import pytest

from conftest import real, shaken_future
from core import setplan
from core.actions import from_sets, position, week
from core.planner import Settings, decide, forecast, lead_months


def test_the_ratio_is_learned_from_earlier_months_only(sample_ds):
    led, sets = sample_ds.ledger, sample_ds.sets
    child = setplan.use_series(led, "SLU001")
    drive = setplan.driver(led, sets, "SLU001")
    st = Settings()
    for t in (5, 12, 20):
        by_hand = child[:t].sum() / drive[:t].sum() * forecast(drive[:t], st)
        assert setplan.set_forecast(child, drive, t, st) == pytest.approx(by_hand)
    later = child.copy()
    later[12:] *= 7  # the future changes; the forecast made at month 12 must not
    assert setplan.set_forecast(later, drive, 12, st) == setplan.set_forecast(child, drive, 12, st)
    assert setplan.set_forecast(child, np.zeros_like(drive), 6, st) == forecast(child[:6], st)  # no parents yet


def test_adoption_as_of_a_month_reads_no_later_month(sample_raw, sample_ds):
    months = sample_ds.ledger.months
    t = 16
    shaken = shaken_future(sample_raw, months[t], months[t])
    for child in ("SEH001", "SNB001", "SLU001"):
        a = setplan.adopt_at(sample_ds.ledger, sample_ds.sets, child, t)
        b = setplan.adopt_at(shaken.ledger, shaken.sets, child, t)
        assert a == b


def test_bakeoff_adopts_only_when_no_worse(sample_ds):
    b = setplan.bakeoff(sample_ds.ledger, sample_ds.sets)
    assert set(b["child_code"]) == {"SEH001", "SNB001", "SLU001"}
    assert (b["adopt"] == (b["wape_set"] <= b["wape_item"])).all()
    n = len(sample_ds.ledger.months)
    for r in b.itertuples():
        assert r.months_scored == n - r.lead - min(Settings().takeover + r.lead, n - 1)
    assert setplan.bakeoff(sample_ds.ledger, sample_ds.sets.iloc[0:0]).empty


def test_a_child_that_follows_its_parent_exactly_is_adopted(sample_ds):
    """Build a child that is exactly 3 per parent: planning it from its set must win."""
    led, sets = sample_ds.ledger, sample_ds.sets
    parent = setplan.use_series(led, "SCL001")
    child = 3.0 * parent
    rng = np.random.default_rng(0)
    noisy = child * rng.uniform(0.3, 1.7, size=len(child))  # the child's own history is noisy around the true ratio
    drive = setplan.driver(led, sets[sets["child_code"] == "SEH001"], "SEH001")  # 1 x clamp
    lead = lead_months(led.lead_days("SEH001"))
    starts = range(12, len(child) - lead)
    item, from_sets, actual = setplan._spans(noisy, drive * 3.0, starts, lead + 1, Settings())
    real_ = np.array([child[t : t + lead + 1].sum() for t in starts])
    assert np.abs(from_sets - real_).sum() < np.abs(item - real_).sum()


def test_mondays_list_with_sets_still_reads_no_later_month(sample_raw, sample_ds):
    months = sample_ds.ledger.months
    month = months[15]
    shaken = shaken_future(sample_raw, month, months[20])
    boq = {c: sample_ds.boq_qty(c) for c in sample_ds.ledger.items["item_code"]}
    a = week(sample_ds.ledger, month, Settings(), boq, sample_ds.sets)
    b = week(shaken.ledger, month, Settings(), boq, shaken.sets)
    pd.testing.assert_frame_equal(a, b)


def test_an_adopted_child_is_planned_from_its_sets(sample_ds):
    led, sets = sample_ds.ledger, sample_ds.sets
    st = Settings()
    i = next(i for i in range(12, len(led.months)) if setplan.adopt_at(led, sets, "SLU001", i))
    f = from_sets(led, sets, "SLU001", i, st)
    drive = setplan.driver(led, sets, "SLU001")
    assert f == setplan.set_forecast(setplan.use_series(led, "SLU001"), drive, i, st)
    j = next(j for j in range(12, len(led.months)) if not setplan.adopt_at(led, sets, "SNB001", j))
    assert from_sets(led, sets, "SNB001", j, st) is None  # not adopted then: its own history
    assert from_sets(led, sets, "SPO001", i, st) is None  # not a child
    assert from_sets(led, None, "SLU001", i, st) is None  # no sets table
    drawn = Settings(cover_weeks=None)  # demand is contractors' draws, not site use: the set forecast does not apply
    assert from_sets(led, sets, "SLU001", i, drawn) is None
    p = position(led, "SLU001", i, st)
    d = decide(p.past_total, p.past_by_godown, p.have, p.coming, 1e9, p.lead, 1.0, st, f_total=f)
    assert d.forecast.sum() == pytest.approx(f)


@real
def test_real_project_bakeoff(real_ds):
    b = setplan.bakeoff(real_ds.ledger, real_ds.sets).set_index("child_code")
    assert len(b) == 6 and int(b["adopt"].sum()) == 2
    assert b.loc["MLU020013", "adopt"] and b.loc["MLU020013", "wape_set"] < b.loc["MLU020013", "wape_item"]
    assert not b.loc["MNB010174", "adopt"]  # the bolt line stated in tonnes: its sets cannot forecast it
