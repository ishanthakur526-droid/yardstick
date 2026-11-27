"""The bundle and the dataset: round trips, fingerprints, and refusing what is not a bundle."""

import base64
import json
import zlib

import pandas as pd
import pytest

from conftest import minimal_raw
from core import bundle, ingest
from core.bundle import BundleError, pack, unpack
from core.dataset import fingerprint, from_tables
from core.safe import csv_bytes, file_part, md


def test_pack_and_unpack_round_trip(sample_ds):
    back = unpack(pack(sample_ds.tables))
    for name, df in sample_ds.tables.items():
        assert len(back[name]) == len(df), name
    assert from_tables(back, key="x").fingerprint == sample_ds.fingerprint


def test_item_codes_that_look_like_numbers_stay_text():
    raw = minimal_raw()
    raw["items"] = raw["items"].assign(item_code=["0100", "0200", "0300"])
    for k in ("receipts", "contractor_moves"):
        raw[k] = raw[k].assign(item_code=raw[k]["item_code"].map({"A100": "0100", "B200": "0200", "C300": "0300"}))
    ds = from_tables(unpack(pack(ingest.monthly_tables(raw))), key="x")
    assert list(ds.ledger.items["item_code"]) == ["0100", "0200", "0300"]


def test_the_fingerprint_changes_with_the_data(minimal_ds, sample_ds):
    assert minimal_ds.fingerprint != sample_ds.fingerprint
    t = dict(minimal_ds.tables)
    t["used"] = t["used"].assign(used=t["used"]["used"] * 2)
    assert fingerprint(t) != minimal_ds.fingerprint


@pytest.mark.parametrize(
    "blob",
    [
        "not base64 at all!!",
        base64.b64encode(b"plain text, not compressed").decode(),
        base64.b64encode(zlib.compress(b"[1, 2, 3]")).decode(),
        base64.b64encode(zlib.compress(json.dumps({"v": 99, "tables": {}}).encode())).decode(),
    ],
)
def test_anything_that_is_not_a_bundle_is_refused(blob):
    with pytest.raises(BundleError):
        unpack(blob)


def test_a_bundle_that_inflates_too_far_is_refused(monkeypatch):
    monkeypatch.setattr(bundle, "MAX_UNPACKED_BYTES", 1000)
    big = pack({"t": pd.DataFrame({"a": range(5000)})})
    with pytest.raises(BundleError):
        unpack(big)


def test_a_bundle_cut_short_when_pasted_is_refused_as_incomplete(sample_ds):
    whole = pack(sample_ds.tables)
    cut = whole[: len(whole) * 2 // 3]
    cut = cut[: len(cut) - len(cut) % 4]  # still valid base64, so only the inflater can tell it is short
    with pytest.raises(BundleError, match="incomplete"):
        unpack(cut)


@pytest.mark.parametrize(
    "payload",
    [
        {"v": 1, "tables": {"items": 'a,b\n"unclosed,1\n'}},  # CSV the reader cannot parse
        {"v": 1, "tables": {"items": ""}},  # no columns at all
        {"v": 1, "tables": {f"t{i}": "a\n1\n" for i in range(50)}},  # far more tables than a project has
    ],
)
def test_a_bundle_whose_tables_cannot_be_read_says_so(payload):
    blob = base64.b64encode(zlib.compress(json.dumps(payload).encode())).decode()
    with pytest.raises(BundleError):
        unpack(blob)


def test_a_deeply_nested_bundle_is_refused_not_crashed():
    blob = base64.b64encode(zlib.compress(b"[" * 200_000 + b"]" * 200_000)).decode()
    with pytest.raises(BundleError):
        unpack(blob)


@pytest.mark.parametrize("blob", ["", "   \n"])
def test_an_empty_bundle_is_refused(blob):
    with pytest.raises(BundleError, match="empty"):
        unpack(blob)


def test_a_damaged_bundle_falls_back_to_the_sample(monkeypatch):
    """The kill switch must hold even for a bundle pasted wrong: the app shows the sample and says why."""
    from ui import state

    monkeypatch.setattr(state, "load_tables", lambda: unpack("not base64 at all!!"))
    state.bundled.clear()
    try:
        ds, problem = state.bundled()
        assert ds is None and "could not be read" in problem and "sample project" in problem
        monkeypatch.setattr(state, "load_tables", lambda: {"items": pd.DataFrame({"x": [1]})})
        state.bundled.clear()
        ds, problem = state.bundled()
        assert ds is None and "not in the expected shape" in problem
    finally:
        state.bundled.clear()


def test_data_with_no_movements_is_refused():
    raw = minimal_raw()
    t = ingest.monthly_tables(raw)
    t["flows"] = t["flows"].iloc[0:0]
    with pytest.raises(ValueError):
        from_tables(t, key="x")


def test_names_from_data_cannot_inject_markdown_or_formulas():
    assert md("[click](http://x)") == "\\[click\\](http://x)"
    assert md("<img src=x>") == "\\<img src=x\\>"
    out = csv_bytes(pd.DataFrame({"name": ["=HYPERLINK(1)", "+12%", "-3.5", "@SUM(A1)", "ok"], "n": [1, 2, 3, 4, 5]}))
    text = out.decode()
    assert "'=HYPERLINK" in text and "'@SUM" in text
    assert "\n+12%," in text and "\n-3.5," in text  # numbers stay numbers
    assert file_part("../../etc/passwd") == "etc_passwd"
