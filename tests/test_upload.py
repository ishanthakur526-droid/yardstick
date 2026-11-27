"""Uploads: the template, reading files safely, recognising tables and columns, and the Pass / Warn / Fail check."""

import io
import os
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from conftest import ROOT, minimal_raw
from core import ingest, upload


def parts_of(sheets: list[upload.Sheet]) -> list[upload.Part]:
    """Every sheet as the screen first suggests it: its table guessed, its columns matched."""
    out = []
    for sh in sheets:
        assert not sh.problem, sh.problem
        table = upload.guess_table(sh.name, list(sh.frame.columns))
        if table:
            out.append(upload.Part(table, sh, upload.suggest_mapping(upload.BY_NAME[table], list(sh.frame.columns))))
    return out


def csv_file(df: pd.DataFrame, sep: str = ",", title_lines: tuple[str, ...] = ()) -> bytes:
    return ("".join(line + "\n" for line in title_lines) + df.to_csv(index=False, sep=sep)).encode("utf-8")


def said(checks: list[upload.Check], level: str) -> list[str]:
    return [c.text for c in checks if c.level == level]


def as_text(raw: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Raw tables the way an upload hands them on: every cell text, blanks as None."""
    return {k: v.astype(object).map(lambda x: None if pd.isna(x) else str(x)) for k, v in raw.items()}


def test_the_blank_template_has_every_table_and_comes_back_not_ready():
    sheets = upload.read_upload("yardstick_template.xlsx", upload.template_bytes())
    assert [s.name for s in sheets] == [t.name for t in upload.TABLES]  # its 'How to fill' sheet is skipped
    for s in sheets:
        assert list(s.frame.columns) == [c.name for c in upload.BY_NAME[s.name].columns]
    report = upload.check(parts_of(sheets))
    assert report.verdict == upload.FAIL and "No rows." in said(report.checks, upload.FAIL)


def test_the_sample_project_round_trips_through_its_workbook(sample_raw, sample_ds):
    report = upload.check(parts_of(upload.read_upload("sample.xlsx", upload.template_bytes(sample_raw))))
    assert report.verdict == upload.PASS, report.frame()
    ds = upload.build(report.tables)
    pd.testing.assert_frame_equal(ds.ledger.site, sample_ds.ledger.site)
    assert (ds.cfg.anchor_code, ds.cfg.closing_stores) == (sample_ds.cfg.anchor_code, sample_ds.cfg.closing_stores)
    assert len(ds.sets) == len(sample_ds.sets) and len(ds.boq) == len(sample_ds.boq)
    assert ds.key == "custom" and ds.cfg.is_sample  # the sample's own project details say it is made up


def test_an_erp_export_with_its_own_headers_reads_the_same(minimal_ds):
    raw = minimal_raw()
    items = raw["items"].rename(
        columns={
            "item_code": "Material Code",
            "item_name": "Material Description",
            "unit": "UOM",
            "lead_time_days": "Lead Time (Days)",
        }
    )
    receipts = raw["receipts"].assign(date=raw["receipts"]["date"].dt.strftime("%d-%m-%Y"))  # day-first dates
    receipts = receipts.rename(
        columns={"date": "GRN Date", "item_code": "Material Code", "godown": "Store", "qty_received": "Qty Recd"}
    )
    moves = raw["contractor_moves"].assign(
        type=raw["contractor_moves"]["type"].map({"ISSUE": "Issued", "CONSUMPTION": "Consumed"})
    )
    moves = moves.rename(
        columns={
            "date": "Posting Date",
            "type": "Movement Type",
            "item_code": "Material Code",
            "godown": "Store",
            "qty": "Quantity",
        }
    )
    files = {
        "Item Master.csv": csv_file(items, title_lines=("Stores ledger export", "Printed on 01-12-2026")),
        "export (1).csv": csv_file(receipts, sep=";"),
        "export (2).csv": csv_file(moves),
    }
    sheets = [s for name, data in files.items() for s in upload.read_upload(name, data)]
    assert sheets[0].header_row == 3  # the title lines above the header are skipped
    guessed = [upload.guess_table(s.name, list(s.frame.columns)) for s in sheets]
    assert guessed == ["items", "receipts", "contractor_moves"]  # by its name, then by the columns alone
    report = upload.check(parts_of(sheets))
    assert report.ok, report.frame()
    assert any("'Issued' as ISSUE" in t for t in said(report.checks, upload.PASS))
    ds = upload.build(report.tables, project="Depot test")
    pd.testing.assert_frame_equal(ds.ledger.site, minimal_ds.ledger.site)
    assert ds.cfg.project == "Depot test"


def test_the_check_says_what_is_left_out():
    raw = as_text(minimal_raw(15))
    moves = raw["contractor_moves"]
    moves.loc[0, "date"] = "31/13/2024"
    moves.loc[1, "qty"] = "twelve"
    moves.loc[2, "item_code"] = "Z999"
    moves.loc[3, "qty"] = "-5"
    raw["items"].loc[3] = ["A100", "THE SAME CODE AGAIN", "NOS", "10"]
    checks = upload.validate(raw)
    warned = " | ".join(said(checks, upload.WARN))
    assert "could not be read (for example '31/13/2024')" in warned
    assert "not a number (for example 'twelve')" in warned
    assert "not in the items table (for example 'Z999')" in warned
    assert "1 row with a negative quantity" in warned
    assert "more than once (for example 'A100')" in warned
    assert not said(checks, upload.FAIL)


def test_a_clean_project_passes():
    checks = upload.validate(as_text(minimal_raw(15)))
    assert {c.level for c in checks} == {upload.PASS}


def _no_lead_times(raw):
    raw["items"]["lead_time_days"] = None


def _no_consumption(raw):
    moves = raw["contractor_moves"]
    raw["contractor_moves"] = moves[moves["type"] != "CONSUMPTION"]


def _no_receipts_table(raw):
    raw.pop("receipts")


@pytest.mark.parametrize(
    "break_it, says",
    [
        (_no_lead_times, "no usable lead time"),
        (_no_consumption, "No CONSUMPTION rows"),
        (_no_receipts_table, "Missing."),
    ],
)
def test_what_cannot_work_fails(break_it, says):
    raw = as_text(minimal_raw(15))
    break_it(raw)
    assert any(says in t for t in said(upload.validate(raw), upload.FAIL))


def test_too_short_a_history_fails_and_a_short_one_warns():
    assert any("needs at least 7" in t for t in said(upload.validate(as_text(minimal_raw(5))), upload.FAIL))
    assert any("only 10 months" in t for t in said(upload.validate(as_text(minimal_raw(10))), upload.WARN))


def test_a_needed_column_that_is_not_picked_stops_the_check():
    sheets = upload.read_upload("items.csv", csv_file(minimal_raw()["items"].drop(columns="lead_time_days")))
    part = upload.Part("items", sheets[0], upload.suggest_mapping(upload.BY_NAME["items"], list(sheets[0].frame)))
    report = upload.check([part])
    assert not report.ok
    assert any("no column picked for lead_time_days" in t for t in said(report.checks, upload.FAIL))
    assert any(t.startswith("Missing.") for t in said(report.checks, upload.FAIL))  # receipts, contractor moves


def test_two_files_for_one_table_are_added_together_with_a_warning():
    raw = minimal_raw()
    rec = raw["receipts"]
    files = {"receipts 2025 a.csv": csv_file(rec.iloc[:10]), "receipts 2025 b.csv": csv_file(rec.iloc[10:])}
    files |= {f"{k}.csv": csv_file(v) for k, v in raw.items() if k != "receipts"}
    sheets = [s for name, data in files.items() for s in upload.read_upload(name, data)]
    report = upload.check(parts_of(sheets))
    assert len(report.tables["receipts"]) == len(rec)
    assert any("added together" in t for t in said(report.checks, upload.WARN))


@pytest.mark.parametrize(
    "name, headers, table",
    [
        ("GRN register", ["Date", "Item", "Store", "Qty"], "receipts"),  # by its name
        ("Issues & Returns", ["date", "type", "item_code", "godown", "qty"], "contractor_moves"),
        ("export", ["date", "direction", "item_code", "godown", "qty"], "transfers"),  # by its columns
        ("export", ["Posting Date", "Movement Type", "Material Code", "Store", "Quantity"], "contractor_moves"),
        ("export", ["item_code", "qty"], "boq"),
        ("export", ["key", "value"], "meta"),
        ("data dictionary", ["file", "column", "what_it_means", "example"], None),
    ],
)
def test_tables_are_recognised_by_name_then_by_columns(name, headers, table):
    assert upload.guess_table(name, headers) == table


def test_exact_column_names_win_over_close_ones():
    headers = ["date", "voucher_no", "godown_code", "godown", "qty", "direction", "item_code"]
    m = upload.suggest_mapping(upload.BY_NAME["transfers"], headers)
    assert m["godown"] == "godown" and m["voucher_no"] == "voucher_no" and m["date"] == "date"


@pytest.mark.parametrize(
    "text, kind",
    [
        ("ISSUE", "ISSUE"),
        ("Issued", "ISSUE"),
        ("Return from contractor", "RETURN"),
        ("Consumed", "CONSUMPTION"),
        ("used on site", "CONSUMPTION"),
        ("Issue and return", None),
        ("Adjustment", None),
    ],
)
def test_movement_types_are_read_from_common_words(text, kind):
    assert upload.move_type(text) == kind


@pytest.mark.parametrize(
    "text, way",
    [
        ("IN", "IN"),
        ("out", "OUT"),
        ("Receipt from another store", "IN"),
        ("Issue to another store", "OUT"),
        ("Transfer In", "IN"),
        ("sideways", None),
    ],
)
def test_transfer_directions_are_read_from_common_words(text, way):
    assert upload.direction(text) == way


def test_quantities_with_thousands_separators_are_numbers():
    out = upload.apply_mapping(
        upload.BY_NAME["boq"], pd.DataFrame({"Qty": ["1,200", "35.5", " 2,000.25 "]}), {"qty": "Qty"}
    )
    assert pd.to_numeric(out["qty"]).tolist() == [1200, 35.5, 2000.25]


def test_dates_stored_as_excel_day_numbers_are_read():
    got = ingest.parse_dates(pd.Series(["45292", "45323", "45352"]))
    assert got.dt.strftime("%Y-%m-%d").tolist() == ["2024-01-01", "2024-02-01", "2024-03-01"]


def test_files_over_the_limits_are_refused(monkeypatch):
    big = csv_file(pd.DataFrame({"item_code": range(60)}))
    monkeypatch.setattr(upload, "MAX_ROWS", 10)
    assert "more than 10 rows" in upload.read_upload("a.csv", big)[0].problem
    book = upload.template_bytes({"receipts": pd.DataFrame({"date": ["2024-01-01"] * 60, "item_code": "P"})})
    assert any("more than 10 rows" in s.problem for s in upload.read_upload("b.xlsx", book))
    monkeypatch.setattr(upload, "MAX_UNPACKED_MB", 0.001)
    assert "unpack to more than" in upload.read_upload("c.xlsx", upload.template_bytes())[0].problem
    monkeypatch.setattr(upload, "MAX_FILE_MB", 0.0001)
    assert "bigger than" in upload.read_upload("d.csv", big)[0].problem


def test_files_that_are_not_what_they_say_are_refused_politely():
    assert "only .xlsx and .csv" in upload.read_upload("old.xls", b"\xd0\xcf\x11\xe0")[0].problem
    assert "not a valid .xlsx" in upload.read_upload("fake.xlsx", b"plain text")[0].problem
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("hello.txt", "a zip, but not a workbook")
    assert "could not be read as an Excel workbook" in upload.read_upload("zip.xlsx", buf.getvalue())[0].problem
    assert upload.read_upload("empty.csv", b"")[0].problem == "it is empty"


RAW_EXPORT = Path(
    os.environ.get("YARDSTICK_RAW_CSV", ROOT.parent / "data-lumino" / "pack" / "lumino-vch3-data" / "csv")
)


@pytest.mark.skipif(
    not (RAW_EXPORT / "contractor_moves.csv").exists(), reason="the real project's raw export is not here"
)
def test_the_real_export_reads_the_same_through_an_upload_as_from_the_command_line():
    sheets = [s for f in sorted(RAW_EXPORT.glob("*.csv")) for s in upload.read_upload(f.name, f.read_bytes())]
    report = upload.check(parts_of(sheets))
    assert report.verdict == upload.PASS, report.frame()
    cli, up = ingest.monthly_tables(ingest.read_folder(RAW_EXPORT)), ingest.monthly_tables(report.tables)
    for name in ("flows", "used"):
        a = cli[name].sort_values(list(cli[name].columns[:3])).reset_index(drop=True)
        b = up[name].sort_values(list(up[name].columns[:3])).reset_index(drop=True)
        pd.testing.assert_frame_equal(a, b, check_dtype=False)
