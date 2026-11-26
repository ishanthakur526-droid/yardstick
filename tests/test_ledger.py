"""Ledger tests: hand-made numbers first, then invariants on Lumino's real data (skipped if the bundle is absent)."""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from conftest import real  # noqa: E402
from core.ledger import build_ledger, godown_labels, item_site  # noqa: E402


def tiny():
    items = pd.DataFrame(
        {"item_code": ["P"], "item_name": ["POLE"], "unit": ["PCS"], "lead_time_days": [60], "role": ["Independent"]}
    )
    flows = pd.DataFrame(
        [
            # month, godown, received, tr_in, tr_out, issue, ret
            ("2024-01", "A", 100, 0, 0, 0, 0),
            ("2024-02", "A", 0, 0, 20, 30, 5),
            ("2024-02", "B", 50, 20, 0, 10, 0),
            ("2024-04", "A", 0, 0, 0, 90, 0),  # issued more than the godown held: late entry
        ],
        columns=["month", "godown", "received", "tr_in", "tr_out", "issue", "ret"],
    ).assign(item_code="P")
    used = pd.DataFrame({"item_code": ["P", "P"], "month": ["2024-02", "2024-04"], "used": [15.0, 40.0]})
    return build_ledger(flows, used, items)


def test_months_have_no_gaps():
    assert tiny().months == ["2024-01", "2024-02", "2024-03", "2024-04"]


def test_godown_stock_follows_the_readme_formula():
    g = tiny().grid
    a = g[g.godown == "A"].set_index("month")["stock"]
    assert a["2024-01"] == 100
    assert a["2024-02"] == 100 - 20 - 30 + 5  # transfer out and issue leave, return comes back
    assert a["2024-03"] == 55  # nothing moved: stock carries forward through a month with no rows
    b = g[g.godown == "B"].set_index("month")["stock"]
    assert b["2024-02"] == 50 + 20 - 10


def test_negative_running_sum_is_shown_as_zero_but_carried_honestly():
    g = tiny().grid
    a = g[g.godown == "A"].set_index("month")
    assert a.loc["2024-04", "stock_raw"] == 55 - 90
    assert a.loc["2024-04", "stock"] == 0


def test_stock_with_contractors_is_project_wide():
    s = item_site(tiny(), "P").set_index("month")
    # issued 30+10 and 5 returned, 15 used in Feb -> 20 still with contractors
    assert s.loc["2024-02", "with_contractors"] == 30 + 10 - 5 - 15
    assert s.loc["2024-02", "godown_stock"] == 55 + 60
    assert s.loc["2024-02", "idle_total"] == 55 + 60 + 20


def test_closing_stores_go_last_and_are_not_working_godowns():
    items = pd.DataFrame(
        {"item_code": ["P"], "item_name": ["POLE"], "unit": ["PCS"], "lead_time_days": [60], "role": ["Independent"]}
    )
    flows = pd.DataFrame(
        [("2024-01", g, 10, 0, 0, 0, 0) for g in ("ZETA YARD", "ALPHA YARD", "BETA YARD")],
        columns=["month", "godown", "received", "tr_in", "tr_out", "issue", "ret"],
    ).assign(item_code="P")
    used = pd.DataFrame({"item_code": ["P"], "month": ["2024-01"], "used": [5.0]})
    led = build_ledger(flows, used, items, closing=["ALPHA YARD"])
    assert led.godowns == ["BETA YARD", "ZETA YARD", "ALPHA YARD"]
    assert led.working == ["BETA YARD", "ZETA YARD"]
    assert led.decimals("P") == 0 and led.step("P") == 1.0


def test_godown_labels_drop_a_shared_first_word_only():
    assert godown_labels(["KANPUR NAGAR", "KANPUR DEHAT", "KANPUR CLOSER"], ["KANPUR CLOSER"]) == {
        "KANPUR NAGAR": "Nagar",
        "KANPUR DEHAT": "Dehat",
        "KANPUR CLOSER": "Closing store",
    }
    assert godown_labels(["NORTH YARD", "SOUTH YARD"]) == {"NORTH YARD": "North Yard", "SOUTH YARD": "South Yard"}
    assert godown_labels(["SITE A", "SITE"]) == {"SITE A": "Site A", "SITE": "Site"}  # never strips a name to nothing
    assert godown_labels(["ONLY STORE"]) == {"ONLY STORE": "Only Store"}


def test_cover_months_uses_recent_use():
    s = item_site(tiny(), "P").set_index("month")
    assert pd.isna(s.loc["2024-01", "cover_months"])  # nothing used yet, so cover is undefined
    assert s.loc["2024-02", "cover_months"] == pytest.approx(s.loc["2024-02", "idle_total"] / (15 / 2))


# ---- real data ------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ledger(real_ds):
    return real_ds.ledger


@real
def test_real_every_item_and_godown_present(ledger):
    assert len(ledger.items) == 16
    assert len(ledger.godowns) == 7 and ledger.godowns[-1] == "KANPUR CLOSER"
    assert ledger.closing == ("KANPUR CLOSER",) and len(ledger.working) == 6
    assert ledger.label("KANPUR NAGAR") == "Nagar" and ledger.label("KANPUR CLOSER") == "Closing store"
    assert len(ledger.grid) == 16 * 7 * len(ledger.months)


@real
def test_real_final_stock_matches_totals(ledger):
    """Last-month stock before clipping must equal the plain sum of the flows (the README's first formula)."""
    g = ledger.grid
    for code, sub in g.groupby("item_code"):
        expect = sub.received.sum() + sub.tr_in.sum() - sub.tr_out.sum() - sub.issue.sum() + sub.ret.sum()
        last = sub[sub.month == ledger.months[-1]].stock_raw.sum()
        assert last == pytest.approx(expect, abs=1e-6), code


@real
def test_real_poles_story(ledger):
    s = item_site(ledger, "MPO010014").set_index("month")
    assert s["received"].sum() == pytest.approx(117050)
    assert s["used"].sum() == pytest.approx(104871)
    peak = s["idle_total"].idxmax()
    assert peak.startswith("2025")  # the yard filled up in early 2025
    assert s.loc[peak, "idle_total"] > 40000
    assert s.loc[peak, "used"] < 5000
