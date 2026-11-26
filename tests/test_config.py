"""Project settings: given in a meta table, or worked out from the records and said out loud."""

import pandas as pd

from conftest import minimal_raw, real
from core import ingest
from core.config import build_config, infer_anchor, infer_closing, read_meta
from core.dataset import from_tables


def flows(rows):
    return pd.DataFrame(rows, columns=["item_code", "godown", "month", "received", "tr_in", "tr_out", "issue", "ret"])


def test_a_store_that_only_takes_transfers_in_is_the_closing_store():
    f = flows(
        [
            ("P", "A", "2024-01", 100, 0, 30, 50, 0),
            ("P", "B", "2024-01", 50, 0, 0, 40, 0),
            ("P", "END", "2024-02", 0, 30, 5, 0, 0),
        ]
    )
    assert infer_closing(f) == ["END"]
    assert infer_closing(flows([("P", "A", "2024-01", 0, 10, 0, 0, 0)])) == []  # never every location


def test_the_anchor_is_marked_or_the_longest_lead_item_outside_the_sets():
    items = pd.DataFrame(
        {"item_code": ["POLE", "CLAMP", "HOOK"], "lead_time_days": [150, 90, 200], "role": ["", "", ""]}
    )
    sets = pd.DataFrame({"parent_code": ["CLAMP"], "child_code": ["HOOK"]})
    f = flows([("POLE", "A", "2024-01", 1, 0, 0, 1, 0)])
    assert infer_anchor(items, sets, f) == "POLE"  # HOOK waits longer but is a child of a set
    marked = items.assign(role=["", "Anchor", ""])
    assert infer_anchor(marked, sets, f) == "CLAMP"


def test_meta_overrides_and_bad_values_are_ignored():
    meta = pd.DataFrame({"key": ["Project", "min_sets", "anchor_code"], "value": ["Line 7", "-5", "NOPE"]})
    assert read_meta(meta)["project"] == "Line 7"
    t = ingest.monthly_tables({**minimal_raw(), "meta": meta})
    cfg = build_config(t)
    assert cfg.project == "Line 7"
    assert cfg.anchor_code == "B200"  # NOPE is not an item, so the longest-lead item is used instead (and said so)
    assert any("BOQ is assumed" in n for n in cfg.assumed)


def test_a_minimal_project_needs_no_settings(minimal_ds):
    cfg, led = minimal_ds.cfg, minimal_ds.ledger
    assert cfg.project == "Uploaded project" and not cfg.closing_stores
    assert led.working == ["EAST DEPOT", "WEST DEPOT"] and led.label("EAST DEPOT") == "East Depot"
    assert led.decimals("B200") == 2 and led.decimals("A100") == 0
    assert minimal_ds.sets.empty


@real
def test_the_real_project_keeps_its_day_one_settings(real_ds):
    cfg = real_ds.cfg
    assert cfg.anchor_code == "MPO010014" and cfg.closing_stores == ("KANPUR CLOSER",)
    assert (cfg.min_parents, cfg.min_sets) == (2000.0, 300.0) and not cfg.assumed
    inferred = from_tables({k: v for k, v in real_ds.tables.items() if k != "meta"}, key="x").cfg
    assert inferred.anchor_code == "MPO010014" and inferred.closing_stores == ("KANPUR CLOSER",)
    assert abs(inferred.min_parents - 2000) < 50 and abs(inferred.min_sets - 300) < 10  # the scaling reproduces them
