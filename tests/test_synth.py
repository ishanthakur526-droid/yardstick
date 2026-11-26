"""The sample project: same layout and traps as a real data pack, deterministic, fast, and calibrated honestly."""

import time

import numpy as np
import pandas as pd

from core import ingest, synth
from core.ledger import data_notes
from core.sets import calibrate

RAW_COLUMNS = {
    "items": ["item_code", "item_name", "unit", "lead_time_days", "role"],
    "sets": [
        "set_no",
        "parent_code",
        "parent_name",
        "parent_unit",
        "child_code",
        "child_name",
        "child_unit",
        "ratio_of_parent",
    ],
    "receipts": ["date", "grn_no", "item_code", "item_name", "unit", "godown", "qty_received"],
    "transfers": [
        "date",
        "voucher_no",
        "voucher_type",
        "direction",
        "item_code",
        "item_name",
        "unit",
        "godown_code",
        "godown",
        "qty",
    ],
    "contractor_moves": ["date", "type", "item_code", "item_name", "unit", "godown", "qty"],
}


def test_same_layout_as_a_real_data_pack(sample_raw):
    for name, cols in RAW_COLUMNS.items():
        assert list(sample_raw[name].columns) == cols, name


def test_deterministic_and_fast():
    t = time.perf_counter()
    a = synth.make_pack(7)
    assert time.perf_counter() - t < 3.0  # under a second on a laptop; generous for a shared CI runner
    b = synth.make_pack(7)
    for name in a:
        pd.testing.assert_frame_equal(a[name], b[name])
    assert not synth.make_pack(8)["contractor_moves"].equals(a["contractor_moves"])


def test_it_has_the_traps_of_a_real_extract(sample_raw, sample_ds):
    cm, tr = sample_raw["contractor_moves"], sample_raw["transfers"]
    assert cm.loc[cm["type"] == "CONSUMPTION", "godown"].isna().all()  # usage has no godown
    assert (cm["qty"] == 0).any() and (sample_raw["receipts"]["qty_received"] == 0).any()  # zero rows
    assert sample_raw["receipts"].duplicated().any()  # identical rows that are both real entries
    assert (tr["direction"] == "OUT").sum() > (tr["direction"] == "IN").sum()  # some transfers never booked in
    assert (tr["voucher_type"] == "Receipt at the closing store").any()
    notes = data_notes(sample_ds.ledger)
    assert 0 < notes["godown_months_below_zero"] < 0.02 * len(sample_ds.ledger.grid)  # late entries, as in real data


def test_it_reads_like_a_real_project(sample_ds):
    ledger, cfg = sample_ds.ledger, sample_ds.cfg
    assert cfg.is_sample and cfg.anchor_code == "SPO001" and ledger.closing == ("RIVERBEND CLOSING STORE",)
    assert ledger.label("RIVERBEND NORTH") == "North"
    assert ledger.decimals("SCA001") == 2 and ledger.decimals("SPO001") == 0  # kilometres keep two decimals
    poles = ledger.site[ledger.site["item_code"] == "SPO001"]
    used, received = poles["used"].to_numpy(), poles["received"].to_numpy()
    assert 1.0 < received.sum() / used.sum() < 1.25  # bought somewhat more than used
    first_half = len(used) // 2
    assert received[:first_half].sum() > used[:first_half].sum() * 1.2  # deliveries ran ahead of the work
    assert poles["with_contractors"].max() > used.mean()  # contractors held more than a month's use


def test_the_planted_wrong_ratio_is_found_and_the_right_ones_agree(sample_ds):
    cal = calibrate(sample_ds.ledger, sample_ds.sets).set_index(["parent_code", "child_code"])
    assert cal.loc[("SDB001", "SLU001"), "factor"] > 10  # the file says 0.25 lugs per box; the site uses about 5
    assert bool(cal.loc[("SCL001", "SEH001"), "agrees"])
    assert cal.loc[("SCL001", "SNB001"), "shared"] and cal.loc[("SDB001", "SNB001"), "shared"]


def test_the_sample_goes_through_the_same_ingest_as_real_data(sample_raw):
    tables = ingest.monthly_tables(sample_raw)
    assert set(tables) >= {"items", "sets", "flows", "used", "meta", "boq"}
    assert np.isfinite(tables["flows"][["received", "tr_in", "tr_out", "issue", "ret"]].to_numpy()).all()
