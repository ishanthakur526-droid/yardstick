"""Set-balance tests on small made-up data: a child that goes out in ratio is balanced; one that lags is flagged."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.ledger import build_ledger  # noqa: E402
from core.sets import POOLED, balance, calendar, calibrate, flagged, pairing  # noqa: E402

MONTHS = [str(p) for p in pd.period_range("2024-01", periods=20, freq="M")]
ITEMS = pd.DataFrame(
    {
        "item_code": ["P", "C"],
        "item_name": ["PARENT SET", "CHILD HOOK"],
        "unit": ["SET", "NOS"],
        "lead_time_days": [60, 60],
        "role": ["Parent", "Child"],
    }
)
SETS = pd.DataFrame(
    {
        "set_no": [1.0],
        "parent_code": ["P"],
        "parent_name": ["PARENT SET"],
        "parent_unit": ["SET"],
        "child_code": ["C"],
        "child_name": ["CHILD HOOK"],
        "child_unit": ["NOS"],
        "ratio_of_parent": [2.0],
    }
)


def make(child_factor=2.0, starve_a_from=None):
    """Parents go out in varying amounts at godowns A and B; the child follows at `child_factor` per parent.
    `starve_a_from`: from this month index on, godown A gets no child at all."""
    rng = np.random.default_rng(7)
    rows, used_p, used_c = [], [], []
    for i, m in enumerate(MONTHS):
        for g in ("A", "B"):
            p = float(rng.integers(800, 1600))
            c = p * child_factor
            if g == "A" and starve_a_from is not None and i >= starve_a_from:
                c = 0.0
            rows.append(("P", g, m, p * 3, 0, 0, p, 0))
            rows.append(("C", g, m, c * 3, 0, 0, c, 0))
        used_p.append(("P", m, sum(r[6] for r in rows if r[0] == "P" and r[2] == m)))
        used_c.append(("C", m, sum(r[6] for r in rows if r[0] == "C" and r[2] == m)))
    flows = pd.DataFrame(rows, columns=["item_code", "godown", "month", "received", "tr_in", "tr_out", "issue", "ret"])
    used = pd.DataFrame(used_p + used_c, columns=["item_code", "month", "used"])
    return build_ledger(flows, used, ITEMS)


def test_pairing_labels():
    assert pairing(0.95) == "strong"
    assert pairing(0.6) == "loose"
    assert pairing(0.2) == "weak"
    assert pairing(float("nan")) == "unknown"


def test_a_child_issued_in_ratio_is_balanced():
    view = balance(make(), SETS, "P", "stated")
    assert len(flagged(view)) == 0
    assert (calendar(view)["status"] == "balanced").all()


def test_the_calibrated_ratio_matches_use():
    cal = calibrate(make(child_factor=2.0), SETS)
    assert cal.loc[0, "observed"] == pytest.approx(2.0, rel=1e-6)
    assert bool(cal.loc[0, "agrees"])


def test_a_stated_ratio_that_disagrees_with_use_is_caught():
    wrong = SETS.assign(ratio_of_parent=0.25)  # the file says 0.25, the site really used 2 per set
    cal = calibrate(make(child_factor=2.0), wrong)
    assert not bool(cal.loc[0, "agrees"])
    assert cal.loc[0, "factor"] == pytest.approx(8.0, rel=1e-6)  # real use is 8x the stated ratio
    assert cal.loc[0, "observed"] == pytest.approx(2.0, rel=1e-6)  # the calibrated ratio recovers it


EVERY_PAIRING = ("strong", "loose", "weak", "unknown")


def test_a_godown_that_stops_getting_the_child_is_flagged_behind():
    view = balance(make(starve_a_from=8), SETS, "P", "stated", judge=EVERY_PAIRING)
    f = flagged(view)
    assert len(f) > 0
    assert set(f["godown"]) == {"A"}
    assert set(f["kind"]) == {"behind"}
    assert f["month"].min() > MONTHS[8]  # nothing is flagged before the child stopped
    assert (calendar(view)["status"] != "balanced").any()


def test_a_child_that_does_not_follow_its_parent_is_not_judged_by_default():
    scenario = make(starve_a_from=8)
    assert calibrate(scenario, SETS).loc[0, "pairing"] != "strong"
    assert len(flagged(balance(scenario, SETS, "P", "stated"))) == 0  # default judges strong pairings only
    assert len(flagged(balance(scenario, SETS, "P", "stated", judge=EVERY_PAIRING))) > 0


def test_the_pooled_row_exists_but_is_not_listed_as_a_godown_flag():
    view = balance(make(starve_a_from=8), SETS, "P", "stated", judge=EVERY_PAIRING)
    assert POOLED in set(view.account["godown"])
    assert POOLED not in set(flagged(view)["godown"])


def test_a_shared_child_is_never_judged_against_one_parent():
    """Two parents share one child that goes out exactly in ratio for both together. Setting its total against a single
    parent would show a surplus that is not there, so a shared child is left out of the set-by-set judgement."""
    items = pd.DataFrame(
        {
            "item_code": ["P1", "P2", "C"],
            "item_name": ["PARENT ONE", "PARENT TWO", "SHARED BOLT"],
            "unit": ["SET", "SET", "NOS"],
            "lead_time_days": [60, 60, 60],
            "role": ["Parent", "Parent", "Child"],
        }
    )
    sets = pd.DataFrame(
        {
            "set_no": [1.0, 2.0],
            "parent_code": ["P1", "P2"],
            "parent_name": ["PARENT ONE", "PARENT TWO"],
            "parent_unit": ["SET", "SET"],
            "child_code": ["C", "C"],
            "child_name": ["SHARED BOLT", "SHARED BOLT"],
            "child_unit": ["NOS", "NOS"],
            "ratio_of_parent": [1.0, 1.0],
        }
    )
    rng = np.random.default_rng(3)
    rows, used = [], []
    for m in MONTHS:
        p1, p2 = float(rng.integers(800, 1600)), float(rng.integers(800, 1600))
        for code, q in (("P1", p1), ("P2", p2), ("C", p1 + p2)):
            rows.append((code, "A", m, q * 3, 0, 0, q, 0))
            used.append((code, m, q))
    flows = pd.DataFrame(rows, columns=["item_code", "godown", "month", "received", "tr_in", "tr_out", "issue", "ret"])
    ledger = build_ledger(flows, pd.DataFrame(used, columns=["item_code", "month", "used"]), items)
    for parent in ("P1", "P2"):
        view = balance(ledger, sets, parent, "stated", judge=("strong", "loose", "weak"))
        assert view.judged["C"] is False
        assert (view.account["severity"] == "").all()
