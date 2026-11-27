"""The weekly action pack: the same actions as Monday's list, nothing from later months, text never runs as a formula."""

import io

import openpyxl
import pandas as pd
import pytest

from conftest import minimal_raw, real, shaken_future
from core import ingest, pack
from core.actions import monday, week
from core.dataset import from_tables
from core.planner import Settings


def boqs(ds):
    return {c: ds.boq_qty(c) for c in ds.ledger.items["item_code"]}


def test_sheets_follow_mondays_list(sample_ds):
    led = sample_ds.ledger
    month = led.months[15]
    p = pack.build(sample_ds, month)
    assert list(p.sheets) == pack.SHEETS
    w = week(led, month, Settings(), boqs(sample_ds), sample_ds.sets)
    assert p.counts["transfers"] == (w["kind"] == "Move").sum()
    assert p.counts["orders"] == (w["kind"] == "Order").sum()
    assert p.counts["holds"] == (w["kind"] == "Hold issues").sum()
    if p.counts["orders"]:
        o = p.sheets["Orders"]
        assert (o["Order by"] == f"{monday(month):%d %b %Y}").all()
        assert o["Quantity"].sum() == pytest.approx(w.loc[w["kind"] == "Order", "qty"].sum(), abs=1)
    assert "Value (your rates)" not in p.sheets["Orders"]  # no rates, no rupees


def test_the_pack_does_not_use_the_future(sample_raw, sample_ds):
    months = sample_ds.ledger.months
    for i in (12, 20):
        shaken = shaken_future(sample_raw, months[i], months[i + 5])
        a = pack.build(sample_ds, months[i])
        b = pack.build(shaken, months[i])
        for name in pack.SHEETS:
            pd.testing.assert_frame_equal(a.sheets[name], b.sheets[name], obj=name)
        assert a.counts == b.counts


def test_set_alerts_and_bom_fixes_are_as_of_the_month(sample_ds):
    led = sample_ds.ledger
    early = pack.build(sample_ds, led.months[12])
    late = pack.build(sample_ds, led.months[30])
    assert not early.sheets["BOM fixes"]["Ratio from site use"].equals(late.sheets["BOM fixes"]["Ratio from site use"])
    fixes = late.sheets["BOM fixes"]
    assert (fixes["Approved ratio (fill in)"] == "").all()


def test_rates_add_values_only_when_given(sample_ds):
    led = sample_ds.ledger
    for i in range(12, len(led.months) - 1):
        p = pack.build(sample_ds, led.months[i], rates={"SPO001": 1000.0})
        o = p.sheets["Orders"]
        if "Item" in o and (o["Item"] == led.item_name("SPO001")).any():
            row = o[o["Item"] == led.item_name("SPO001")].iloc[0]
            assert row["Value (your rates)"] == pytest.approx(row["Quantity"] * 1000, abs=1000)
            assert "your rates" in p.sheets["Summary"].set_index("What").loc["Purchase orders", "Value"]
            return
    pytest.fail("the sample never ordered poles")


def test_a_project_with_no_sets_still_gets_a_full_pack(minimal_ds):
    p = pack.build(minimal_ds, minimal_ds.ledger.months[6], Settings(takeover=4))
    assert list(p.sheets) == pack.SHEETS
    assert "no sets table" in p.sheets["Set alerts"]["Note"].iloc[0]
    assert p.counts["set_alerts"] == 0 and p.counts["bom_fixes"] == 0


def test_text_from_the_data_stays_text_in_excel():
    raw = minimal_raw()
    raw["items"].loc[0, "item_name"] = '=HYPERLINK("http://example.com","x")'
    ds = from_tables(ingest.monthly_tables(raw), key="custom")
    p = pack.build(ds, ds.ledger.months[6], Settings(takeover=4))
    wb = openpyxl.load_workbook(io.BytesIO(pack.workbook(p)))
    assert wb.sheetnames == pack.SHEETS
    seen = False
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and cell.value.startswith("=HYPERLINK"):
                    assert cell.data_type == "s"  # a string, not a formula
                    seen = True
    assert seen


@real
def test_real_project_pack(real_ds):
    p = pack.build(real_ds, "2025-02")
    assert p.counts["transfers"] == 14 and p.counts["orders"] == 1 and p.counts["holds"] == 16
    assert p.counts["bom_fixes"] >= 1
    assert pack.workbook(p)[:2] == b"PK"
