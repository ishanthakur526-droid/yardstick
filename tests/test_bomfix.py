"""BOM fix sheet: proposes, never applies; flags likely unit mismatches; as-of calibration reads no later month."""

import pandas as pd
import pytest

from conftest import real, shaken_future
from core.bomfix import CHANGE, KEEP, UNIT, fix_sheet, sheet_for_people, three_figures
from core.sets import calibrate


def test_sample_fix_sheet(sample_ds):
    cal = calibrate(sample_ds.ledger, sample_ds.sets)
    before = sample_ds.sets.copy()
    fixes = fix_sheet(cal)
    pd.testing.assert_frame_equal(sample_ds.sets, before)  # nothing is applied to the master data
    assert len(fixes) == len(cal)
    first = fixes.iloc[0]
    assert first["action"] == CHANGE and first["child_name"].startswith("ALUMINIUM LUG")
    assert first["suggested"] == pytest.approx(first["observed"], rel=5e-3)  # three significant figures
    assert set(fixes.loc[fixes["child_code"] != "SLU001", "action"]) == {KEEP}
    kept = fixes[fixes["action"] == KEEP]
    assert (kept["suggested"] == kept["stated"]).all()
    people = sheet_for_people(fixes)
    assert (people["Approved ratio (fill in)"] == "").all() and (people["Approved by"] == "").all()


def test_beyond_100x_says_check_the_unit():
    cal = pd.DataFrame(
        {
            "set_no": [1, 1],
            "parent_code": ["P", "P"],
            "parent_name": ["PARENT", "PARENT"],
            "child_code": ["A", "B"],
            "child_name": ["BOLTS", "LUGS"],
            "child_unit": ["MT", "NOS"],
            "stated": [1.0, 0.25],
            "observed": [0.0008, 5.0],
            "factor": [0.0008, 20.0],
            "agrees": [False, False],
            "shared": [False, False],
            "pairing": ["strong", "weak"],
        }
    )
    fixes = fix_sheet(cal).set_index("child_code")
    assert fixes.loc["A", "action"] == UNIT and "another unit" in fixes.loc["A", "note"]
    assert fixes.loc["B", "action"] == CHANGE and "average" in fixes.loc["B", "note"]  # a weak pairing is a hint
    assert list(fix_sheet(cal)["child_code"]) == ["A", "B"]  # the unit check comes first


def test_three_figures():
    assert three_figures(22.62776) == 22.6 and three_figures(0.000086123) == 0.0000861 and three_figures(0) == 0


def test_as_of_calibration_reads_no_later_month(sample_raw, sample_ds):
    months = sample_ds.ledger.months
    month = months[15]
    shaken = shaken_future(sample_raw, month, months[20])
    a = fix_sheet(calibrate(sample_ds.ledger, sample_ds.sets, before=month))
    b = fix_sheet(calibrate(shaken.ledger, shaken.sets, before=month))
    pd.testing.assert_frame_equal(a, b)
    whole = fix_sheet(calibrate(sample_ds.ledger, sample_ds.sets))
    assert not a["observed"].equals(whole["observed"])  # it really is a different, earlier view


def test_no_use_yet_keeps_the_stated_ratio(sample_ds):
    fixes = fix_sheet(calibrate(sample_ds.ledger, sample_ds.sets, before=sample_ds.ledger.months[0]))
    assert (fixes["action"] == KEEP).all() and fixes["note"].str.contains("not used").all()


@real
def test_real_fix_sheet(real_ds):
    fixes = fix_sheet(calibrate(real_ds.ledger, real_ds.sets))
    assert (fixes["action"] == UNIT).sum() == 3 and set(fixes.loc[fixes["action"] == UNIT, "child_unit"]) == {"MT"}
    assert (fixes["action"] == CHANGE).sum() == 4 and (fixes["action"] == KEEP).sum() == 1
    ring = fixes[fixes["action"] == CHANGE].iloc[0]
    assert "RING TYPE" in ring["child_name"] and 20 < ring["suggested"] / ring["stated"] < 25
