"""Security review #2: an anonymous visitor's upload must not exhaust the free host, and their results stay theirs.

Each test is one way a few kilobytes used to cost the whole process gigabytes or minutes (found by the review and run
there), now refused or bounded within a second or two.
"""

from __future__ import annotations

import io
import time
import tomllib
import tracemalloc
import zipfile
from pathlib import Path

import openpyxl
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from conftest import minimal_raw
from core import ingest, ledger, upload
from core.dataset import from_tables

ROOT = Path(__file__).resolve().parent.parent
APP = str(ROOT / "app.py")


def quick(fn, *args, limit: float = 2.0, **kwargs):
    t = time.perf_counter()
    out = fn(*args, **kwargs)
    assert time.perf_counter() - t < limit, f"{fn.__name__} took {time.perf_counter() - t:.1f}s"
    return out


def test_a_csv_with_a_very_wide_header_is_cut_not_padded():
    """1,000 header cells and 5,000 one-cell rows used to pad to 5 million cells (162 MB)."""
    data = ("," * 999 + "x\n" + "1\n" * 5000).encode()
    tracemalloc.start()
    try:
        (sheet,) = quick(upload.read_upload, "wide.csv", data)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert len(sheet.frame.columns) <= upload.MAX_COLUMNS
    assert peak < 60 * 2**20, f"{peak / 2**20:.0f} MB"


def test_an_xlsx_with_cells_in_the_last_column_is_cut_not_padded():
    """300 rows each with one cell in column XFD took 50 s and 237 MB through pandas."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["item_code", "qty"])
    for r in range(2, 302):
        ws.cell(row=r, column=16_384, value="x")
    buf = io.BytesIO()
    wb.save(buf)
    sheets = quick(upload.read_upload, "far.xlsx", buf.getvalue())
    assert all(len(s.frame.columns) <= upload.MAX_COLUMNS for s in sheets)


def test_many_short_lines_stop_at_the_row_limit_without_splitting_the_whole_file(monkeypatch):
    monkeypatch.setattr(upload, "MAX_ROWS", 1000)
    data = b"ab\n" * 2_000_000
    tracemalloc.start()
    try:
        (sheet,) = quick(upload.read_upload, "lines.csv", data)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert "more than 1,000 rows" in sheet.problem
    assert peak < 60 * 2**20, f"{peak / 2**20:.0f} MB"  # splitting all 2 million lines cost about twice this


def test_the_cell_budget_is_shared_by_the_files_of_one_upload():
    first = upload.read_upload("a.csv", b"item_code,qty\n" + b"A,1\n" * 30)
    assert upload.cells(first) == 60
    (second,) = upload.read_upload("b.csv", b"item_code,qty\n" + b"B,2\n" * 30, budget=100 - upload.cells(first))
    assert "cells" in second.problem
    assert "cells" in upload.read_upload("c.csv", b"x,y\n1,2\n", budget=0)[0].problem


def test_matching_very_long_headers_is_quick():
    """200 headers of 1,000 characters took 23 s per sheet, on every click."""
    headers = [f"h{i}" + "y" * 1000 for i in range(200)]
    quick(upload.guess_table, "sheet", headers, limit=1.0)
    quick(upload.suggest_mapping, upload.BY_NAME["contractor_moves"], headers, limit=1.0)
    rows = [[f"{c}{i}" * 300 for c in "abcdefghij"] for i in range(20)]
    quick(upload._header_at, rows, limit=1.0)


def _workbook_with(extra: dict[str, bytes]) -> bytes:
    wb = openpyxl.Workbook()
    wb.active.append(["item_code", "qty"])
    buf = io.BytesIO()
    wb.save(buf)
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            dst.writestr(item, src.read(item.filename))
        for name, data in extra.items():
            dst.writestr(name, data)
    return out.getvalue()


def test_a_workbook_part_that_unpacks_hundreds_of_times_its_size_is_refused():
    """The shared-strings bomb: 82 KB that unpacked to 32 MB of XML took 48 s to parse."""
    (sheet,) = quick(upload.read_upload, "bomb.xlsx", _workbook_with({"xl/media/pad.bin": b"\0" * 3 * 2**20}))
    assert "times its size" in sheet.problem


def test_a_workbook_with_too_much_shared_text_is_refused(monkeypatch):
    monkeypatch.setattr(upload, "MAX_SHARED_TEXT_MB", 0.0001)
    data = _workbook_with({"xl/sharedStrings.xml": b"<sst>" + b"<si><t>a</t></si>" * 50 + b"</sst>"})
    assert "MB of text" in upload.read_upload("text.xlsx", data)[0].problem


def test_dates_outside_1990_to_2100_are_not_dates():
    d = ingest.parse_dates(pd.Series(["2024-01-05", "1000-01-01", "9999-12-31", "0202-03-04", "2100-12-31"]))
    assert d.notna().tolist() == [True, False, False, False, True]


def test_a_mistyped_year_is_left_out_not_stretched_over_centuries():
    raw = minimal_raw()
    raw["receipts"].loc[0, "date"] = "1000-01-01"
    raw["receipts"].loc[1, "date"] = "9999-12-31"
    checks = upload.validate(raw)
    assert not [c for c in checks if c.level == upload.FAIL]
    assert [c for c in checks if c.level == upload.WARN and "outside 1990 to 2100" in c.text]
    assert len(from_tables(ingest.monthly_tables(raw), key="custom").ledger.months) == 12


def test_records_spanning_more_than_ten_years_fail_the_check():
    raw = minimal_raw()
    raw["receipts"].loc[0, "date"] = "1991-01-01"
    raw["receipts"].loc[1, "date"] = "2099-12-31"
    fails = [c.text for c in upload.validate(raw) if c.level == upload.FAIL]
    assert any("mistyped year" in t for t in fails), fails
    with pytest.raises(ValueError, match="span"):
        quick(from_tables, ingest.monthly_tables(raw), key="custom")


def test_a_project_too_big_to_replay_fails_the_check_and_the_build(monkeypatch):
    monkeypatch.setattr(ledger, "MAX_GRID", 50)  # the small project: 3 items x 2 depots x 12 months = 72
    raw = minimal_raw()
    fails = [c.text for c in upload.validate(raw) if c.level == upload.FAIL]
    assert any("too big" in t and "72" in t for t in fails), fails
    with pytest.raises(ValueError, match="too big"):
        quick(from_tables, ingest.monthly_tables(raw), key="custom")


def test_visitors_never_see_error_details():
    config = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    assert config["client"]["showErrorDetails"] == "none"


def test_results_for_an_upload_stay_in_that_session(minimal_ds):
    """Results for the built-in projects are shared (they are the same for everyone); an upload's are not."""
    from ui import state

    at = AppTest.from_file(APP, default_timeout=300)
    at.session_state["custom_ds"] = minimal_ds
    at.session_state["dataset"] = "custom"
    at.session_state["section"] = "Home"
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    memo = at.session_state[state.SESSION_RESULTS]
    assert memo and {key[1] for key in memo} == {minimal_ds.fingerprint}
    assert {key[0] for key in memo} >= {"run_replay", "run_levers", "run_week"}

    at = AppTest.from_file(APP, default_timeout=300)
    at.session_state["dataset"] = "sample"
    at.session_state["section"] = "Home"
    at.run()
    assert state.SESSION_RESULTS not in at.session_state


def test_a_result_from_the_session_memo_is_a_copy(minimal_ds):
    """As with st.cache_data, a caller that changes what it gets must not change the next caller's result."""
    from ui import state

    store: dict = {}

    class FakeSession(dict):
        def setdefault(self, key, default=None):
            return store.setdefault(key, default)

    import streamlit as st

    real = st.session_state
    try:
        state.st.session_state = FakeSession()
        first = state.run_calibration(minimal_ds.fingerprint, minimal_ds)
        first["changed"] = 1
        again = state.run_calibration(minimal_ds.fingerprint, minimal_ds)
        assert "changed" not in again.columns
    finally:
        state.st.session_state = real
