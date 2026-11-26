"""BOQ-to-buy-list tests on small made-up data with known totals, so every expected number can be worked out by hand.

Stock at the end (godowns / with contractors): POLE 600/0, P 2000/200, P2 800/0, C 6000/0, X 400/0.
Use per pole: P 2.0, P2 0.5, X 0.75. Set ratios: P->C stated 2.0, P2->C stated 1.0; the site used the child at twice
the stated rate, so the site-observed ratios are 4.0 and 2.0.
"""

import io
import sys
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.export import requisition_workbook  # noqa: E402
from core.ledger import build_ledger  # noqa: E402
from core.requisition import parse_boq, ratio_check, requisition, summary  # noqa: E402

MONTHS = [str(p) for p in pd.period_range("2025-01", periods=6, freq="M")]
ITEMS = pd.DataFrame(
    {
        "item_code": ["POLE", "P", "P2", "C", "X"],
        "item_name": ["POLE", "PARENT SET", "SECOND PARENT", "CHILD HOOK", "OTHER PART"],
        "unit": ["PCS", "SET", "NOS", "NOS", "NOS"],
        "lead_time_days": [150, 90, 90, 30, 60],
        "role": ["Independent", "Parent", "Parent", "Child", "Independent"],
    }
)
SETS = pd.DataFrame(
    {
        "set_no": [1.0, 1.0],
        "parent_code": ["P", "P2"],
        "parent_name": ["PARENT SET", "SECOND PARENT"],
        "parent_unit": ["SET", "NOS"],
        "child_code": ["C", "C"],
        "child_name": ["CHILD HOOK"] * 2,
        "child_unit": ["NOS"] * 2,
        "ratio_of_parent": [2.0, 1.0],
    }
)
#            received, issued, used
TOTALS = {
    "POLE": (1000, 400, 400),
    "P": (3000, 1000, 800),
    "P2": (1000, 200, 200),
    "C": (8000, 2000, 3600),
    "X": (500, 100, 300),
}
TODAY = date(2026, 11, 21)


def make():
    flows = pd.DataFrame(
        [(c, "G", MONTHS[0], r, 0, 0, 0, 0) for c, (r, _, _) in TOTALS.items()]
        + [(c, "G", MONTHS[1], 0, 0, 0, i, 0) for c, (_, i, _) in TOTALS.items()],
        columns=["item_code", "godown", "month", "received", "tr_in", "tr_out", "issue", "ret"],
    )
    used = pd.DataFrame(
        [(c, m, u / len(MONTHS)) for c, (_, _, u) in TOTALS.items() for m in MONTHS],
        columns=["item_code", "month", "used"],
    )
    return build_ledger(flows, used, ITEMS)


LEDGER = make()


def run(boq, **kw):
    kw.setdefault("today", TODAY)
    kw.setdefault("days_left", 100)
    kw.setdefault("anchor_code", "POLE")
    return requisition(LEDGER, SETS, boq, **kw)


def line(req, code):
    return req.lines.set_index("item_code").loc[code]


def test_a_typed_boq_line_is_the_requirement_and_the_ratios_are_only_a_cross_check():
    c = line(run({"P": 50, "C": 7}), "C")
    assert c["needed"] == 7 and c["source"] == "BOQ line"
    assert c["at_file"] == pytest.approx(100) and c["at_site"] == pytest.approx(
        200
    )  # what the set ratios would have said


def test_children_follow_their_parent_at_the_chosen_ratio():
    site = line(run({"P": 50}), "C")
    assert site["needed"] == 200 and site["source"] == "Set ratio (site)"
    file = line(run({"P": 50}, basis="stated"), "C")
    assert file["needed"] == 100 and file["source"] == "Set ratio (file)"
    assert file["at_site"] == pytest.approx(200)  # both are always shown


def test_a_child_shared_by_two_parents_adds_up():
    assert line(run({"P": 50, "P2": 10}, basis="stated"), "C")["needed"] == 110
    assert line(run({"P": 50, "P2": 10}), "C")["needed"] == 220


def test_other_items_are_estimated_from_use_per_pole():
    req = run({"POLE": 100})
    assert line(req, "X")["needed"] == 75 and line(req, "X")["source"] == "Per pole (site)"
    assert line(req, "P")["needed"] == 200 and line(req, "P2")["needed"] == 50
    assert line(req, "C")["needed"] == 200 * 4.0 + 50 * 2.0  # through the set ratios, not a second estimate
    assert list(run({"POLE": 100}, per_anchor=False).lines["item_code"]) == ["POLE"]


def test_units_bought_whole_are_rounded_up():
    assert line(run({"POLE": 101}), "X")["needed"] == 76  # 75.75


def test_only_what_is_missing_is_bought():
    req = run({"POLE": 700})  # 600 in stock
    assert line(req, "POLE")["buy"] == 100
    p = line(req, "P")  # needs 1,400, has 2,000
    assert p["buy"] == 0 and p["kind"] == "covered" and p["left_over"] == 600


def test_contractor_stock_counts_only_when_asked():
    assert line(run({"POLE": 1050}), "P")["buy"] == 100  # needs 2,100; 2,000 in godowns
    assert line(run({"POLE": 1050}, with_contractors=True), "P")["buy"] == 0  # plus 200 with contractors


def test_the_deadline_decides_what_is_too_late():
    req = run({"POLE": 1000}, days_left=100)
    pole = line(req, "POLE")  # lead time 150 days, needed in 100: the order date has passed
    assert pole["kind"] == "late" and pole["late_days"] == 50 and pole["status"] == "Too late by 50 days"
    x = line(req, "X")  # needs 750, has 400; lead time 60 <= 100
    assert x["kind"] == "order" and x["buy"] == 350 and x["order_by"] == TODAY + timedelta(days=40)
    assert req.lines["kind"].iloc[0] == "late"  # the urgent lines come first
    s = summary(req)
    assert s["lines"] == len(req.lines) and s["late"] == 1 and s["to_buy"] + s["covered"] == s["lines"]


def test_procurement_needs_days_before_the_order_date():
    req = run({"POLE": 1000}, days_left=100, handoff_days=7)
    x = line(req, "X")  # order by day 40, so the list is due on day 33
    assert x["send_by"] == TODAY + timedelta(days=33) and x["status"] == f"Send to procurement by {x['send_by']:%d %b}"
    assert line(req, "POLE")["late_days"] == 57  # 50 days past the order date, plus the 7 procurement days
    assert line(run({"POLE": 1000}, days_left=66), "X")["kind"] == "order"  # order by day 6
    assert line(run({"POLE": 1000}, days_left=66, handoff_days=7), "X")["kind"] == "late"  # list was due yesterday


def test_an_item_code_not_in_the_data_is_reported_not_guessed():
    req = run({"NOPE": 5, "POLE": 10})
    assert req.unknown == ["NOPE"] and "NOPE" not in set(req.lines["item_code"])


def test_an_empty_boq_gives_an_empty_buy_list():
    req = run({})
    assert req.lines.empty and summary(req) == {"lines": 0, "covered": 0, "to_buy": 0, "late": 0}


def test_the_excel_buy_list_has_its_three_sheets():
    data = requisition_workbook(run({"POLE": 1000}))
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.read("xl/workbook.xml").decode()
    assert all(n in names for n in ("Buy list", "Ratio check", "Assumptions"))
    assert requisition_workbook(run({}))[:2] == b"PK"  # even an empty BOQ gives a valid file


def test_ratio_check_in_plain_words():
    assert "agree" in ratio_check(0.95)
    assert "unit" in ratio_check(0.000086)
    assert "22.6×" in ratio_check(22.6)
    assert "unit" in ratio_check(500.0)


def test_a_boq_sheet_is_read_by_name_or_by_code():
    sheet = pd.DataFrame({"Item": ["pole", "PARENT  SET", "mystery", "OTHER PART"], "Quantity": [100, 5, 3, 0]})
    got, missed = parse_boq(sheet, ITEMS)
    assert got == {"POLE": 100.0, "P": 5.0} and missed == ["mystery"]
    assert parse_boq(pd.DataFrame({"Item code": ["x"], "Qty": [4]}), ITEMS)[0] == {"X": 4.0}
    with pytest.raises(ValueError):
        parse_boq(pd.DataFrame({"foo": [1]}), ITEMS)
