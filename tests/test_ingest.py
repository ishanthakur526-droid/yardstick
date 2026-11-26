"""From raw records to monthly tables: the data pack's rules hold for any project."""

import pandas as pd
import pytest

from conftest import minimal_raw
from core.ingest import clean_sets, monthly_tables, read_folder

ITEMS = pd.DataFrame({"item_code": ["P"], "item_name": ["POLE"], "unit": ["pcs"], "lead_time_days": [90]})


def raw(moves, receipts=None, transfers=None):
    rec = receipts if receipts is not None else [("2024-01-03", "P", "A", 100)]
    out = {
        "items": ITEMS,
        "receipts": pd.DataFrame(rec, columns=["date", "item_code", "godown", "qty_received"]),
        "contractor_moves": pd.DataFrame(moves, columns=["date", "type", "item_code", "godown", "qty"]),
    }
    if transfers is not None:
        out["transfers"] = pd.DataFrame(transfers, columns=["date", "direction", "item_code", "godown", "qty"])
    return out


def test_identical_rows_are_all_counted_and_zero_rows_add_nothing():
    t = monthly_tables(
        raw(
            [
                ("2024-01-10", "ISSUE", "P", "A", 10),
                ("2024-01-10", "ISSUE", "P", "A", 10),  # looks like a duplicate; it is a separate entry
                ("2024-01-11", "ISSUE", "P", "A", 0),
                ("2024-01-20", "consumption", "P", None, 15),
            ]
        )
    )
    f = t["flows"].set_index(["item_code", "godown", "month"])
    assert f.loc[("P", "A", "2024-01"), "issue"] == 20
    assert t["used"].set_index(["item_code", "month"]).loc[("P", "2024-01"), "used"] == 15


def test_usage_is_project_wide_and_issues_without_a_godown_are_left_out():
    t = monthly_tables(raw([("2024-02-01", "CONSUMPTION", "P", None, 7), ("2024-02-02", "ISSUE", "P", None, 99)]))
    assert t["flows"]["issue"].sum() == 0  # an issue must say which godown it left
    assert t["used"]["used"].sum() == 7


def test_transfers_and_sets_are_optional():
    t = monthly_tables(raw([("2024-01-10", "ISSUE", "P", "A", 5)]))
    assert t["sets"].empty and (t["flows"][["tr_in", "tr_out"]].to_numpy() == 0).all()
    t2 = monthly_tables(
        raw(
            [("2024-01-10", "ISSUE", "P", "A", 5)],
            transfers=[("2024-01-05", "out", "P", "A", 30), ("2024-01-06", "IN", "P", "B", 30)],
        )
    )
    f = t2["flows"].set_index(["godown", "month"])
    assert f.loc[("A", "2024-01"), "tr_out"] == 30 and f.loc[("B", "2024-01"), "tr_in"] == 30


def test_rows_without_a_date_or_quantity_cannot_be_placed():
    t = monthly_tables(raw([("not a date", "ISSUE", "P", "A", 5), ("2024-03-01", "ISSUE", "P", "A", "x")]))
    assert t["flows"]["issue"].sum() == 0


def test_units_and_codes_are_tidied():
    t = monthly_tables(raw([("2024-01-10", "ISSUE", " P ", "A", 5)]))
    assert t["items"]["unit"].tolist() == ["PCS"]
    assert t["flows"]["item_code"].unique().tolist() == ["P"]


def test_sets_naming_unknown_items_are_dropped_and_names_filled_in():
    items = pd.DataFrame(
        {"item_code": ["P", "C"], "item_name": ["PARENT", "CHILD"], "unit": ["SET", "NOS"], "lead_time_days": [9, 9]}
    )
    sets = pd.DataFrame(
        {
            "parent_code": ["P", "P", "X"],
            "child_code": ["C", "Y", "C"],
            "ratio_of_parent": [2, 1, 1],
            "set_no": [1, 1, 2],
        }
    )
    out = clean_sets(sets, items)
    assert len(out) == 1 and out.iloc[0]["child_name"] == "CHILD" and out.iloc[0]["parent_unit"] == "SET"


def test_read_folder_needs_the_three_core_tables(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_folder(tmp_path)
    for name, df in minimal_raw().items():
        df.to_csv(tmp_path / f"{name}.csv", index=False)
    assert set(read_folder(tmp_path)) == {"items", "receipts", "contractor_moves"}
