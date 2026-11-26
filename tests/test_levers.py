"""The lever waterfall: rows in the right order, one baseline, the shortage rule, and the real numbers pinned."""

import numpy as np
import pytest

from conftest import real
from core.levers import ACTUAL, BOQ, BY_USE, COVER, item_waterfall, portfolio, steps
from core.planner import MAX_SHORTAGE_SHARE, Settings, replay


def test_rows_come_in_lever_order_and_share_one_baseline(sample_ds):
    ledger = sample_ds.ledger
    w = item_waterfall(ledger, "SPO001", boq=sample_ds.boq_qty("SPO001"))
    assert list(w["step"]) == [ACTUAL, BY_USE, BOQ, COVER]
    assert w["label"].iloc[-1] == "+ contractor cover (2 weeks)"
    r = replay(ledger, "SPO001", Settings(cover_weeks=None, capped=False))
    assert w["avg_idle"].iloc[0] == pytest.approx(r.summary["avg_real"])
    assert w["peak_idle"].iloc[0] == pytest.approx(r.summary["peak_real"])
    # each step's change adds up to where the last row ends
    assert w["avg_idle"].iloc[0] + w["change"].iloc[1:].sum() == pytest.approx(w["avg_idle"].iloc[-1])
    assert w["cut"].iloc[0] == 0.0
    # idle is godowns plus contractors in every row
    assert np.allclose(w["avg_godowns"] + w["avg_contractors"], w["avg_idle"])
    assert (w["counts"] == (w["shortage_pct"] <= MAX_SHORTAGE_SHARE)).all()
    assert w.loc[w["step"] == BOQ, "cap_source"].iloc[0] == "boq"


def test_switching_the_boq_cap_off_drops_its_row_and_uncaps_the_last(sample_ds):
    w = item_waterfall(sample_ds.ledger, "SPO001", boq=sample_ds.boq_qty("SPO001"), boq_cap=False, cover_weeks=4)
    assert list(w["step"]) == [ACTUAL, BY_USE, COVER]
    assert w["cap_source"].iloc[-1] == "none"
    assert w["label"].iloc[-1] == "+ contractor cover (4 weeks)"
    assert [s.key for s in steps(boq_cap=False)] == [ACTUAL, BY_USE, COVER]


def test_contractors_hold_what_they_really_held_until_the_cover_lever(sample_ds):
    w = item_waterfall(sample_ds.ledger, "SPO001", boq=sample_ds.boq_qty("SPO001")).set_index("step")
    assert w.loc[BY_USE, "avg_contractors"] == pytest.approx(w.loc[ACTUAL, "avg_contractors"])
    assert w.loc[BOQ, "avg_contractors"] == pytest.approx(w.loc[ACTUAL, "avg_contractors"])
    assert w.loc[COVER, "avg_contractors"] < w.loc[ACTUAL, "avg_contractors"]


def test_all_items_at_once(sample_ds):
    ledger = sample_ds.ledger
    tables = {c: item_waterfall(ledger, c, boq=sample_ds.boq_qty(c)) for c in ledger.items["item_code"]}
    p = portfolio(tables).set_index("step")
    assert list(p.index) == [ACTUAL, BY_USE, BOQ, COVER]
    assert (p["items"] == len(ledger.items)).all()
    assert p.loc[ACTUAL, "items_counting"] == len(ledger.items) and p.loc[ACTUAL, "median_cut"] == 0.0
    for step, t in ((s, pd_rows(tables, s)) for s in (BY_USE, BOQ, COVER)):
        ok = t[t["counts"]]
        assert p.loc[step, "items_counting"] == len(ok)
        if len(ok):
            assert p.loc[step, "median_cut"] == pytest.approx(float(np.median(ok["cut"])))
    assert portfolio({}).empty


def pd_rows(tables, step):
    import pandas as pd

    return pd.DataFrame([t[t["step"] == step].iloc[0] for t in tables.values()])


@real
def test_real_poles_waterfall(real_ds):
    """Kanpur poles, average idle stock: what really happened, then each lever added (pinned so it cannot drift)."""
    w = item_waterfall(real_ds.ledger, "MPO010014", boq=real_ds.boq_qty("MPO010014")).set_index("step")
    assert w.loc[ACTUAL, "avg_idle"] == pytest.approx(29572, abs=1)
    assert w.loc[BY_USE, "avg_idle"] == pytest.approx(30497, abs=1)  # a forecast alone saves nothing
    assert w.loc[BOQ, "avg_idle"] == pytest.approx(23688, abs=1)
    assert w.loc[COVER, "avg_idle"] == pytest.approx(17551, abs=1)
    assert w.loc[BOQ, "cap_source"] == "stand-in"  # the data has no BOQ: what was really bought stands in
    assert w["counts"].all()
    zero = item_waterfall(real_ds.ledger, "MPO010014", cover_weeks=0).set_index("step")
    assert zero.loc[COVER, "avg_idle"] == pytest.approx(15347, abs=1)  # the old headline: contractors get only use
