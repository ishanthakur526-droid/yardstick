"""Planner tests on small made-up scenarios, plus the real data when the bundle is present.

The one that matters most: the planner must never use the future. Change what happens from month T0 on and every
decision made up to T0 must stay exactly the same.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from conftest import real  # noqa: E402
from core.planner import MAX_SHORTAGE_SHARE, Settings, counts, replay, simulate, summarise  # noqa: E402

MONTHS = [str(p) for p in pd.period_range("2024-01", periods=24, freq="M")]
GODOWNS = ["A", "B", "C"]


def scenario(seed=1, lead=2, takeover=4):
    rng = np.random.default_rng(seed)
    demand = np.round(rng.uniform(20, 120, size=(24, 3)) * np.array([1.0, 0.6, 0.2]))
    inherited = np.round(rng.uniform(100, 400, size=(24, 3)))
    return dict(
        months=MONTHS,
        godowns=GODOWNS,
        demand=demand,
        inherited=inherited,
        requirement=float(inherited.sum()),
        lead=lead,
        step=1.0,
        settings=Settings(takeover=takeover, window=3, sd_window=6, safety=2.0),
    )


def decisions(result):
    monthly, by_godown, moves = result
    keep = ["month", "godown", "forecast_use", "safety", "target", "order"]
    planned = [m for m in moves if m["when"] == "start of month"]
    return by_godown[keep], planned


def test_no_look_ahead():
    base = scenario()
    t0 = 12
    a_g, a_m = decisions(simulate(**base))
    changed = dict(base)
    demand = base["demand"].copy()
    demand[t0:] *= 10  # a very different future
    changed["demand"] = demand
    b_g, b_m = decisions(simulate(**changed))
    upto = [m for m in MONTHS[: t0 + 1]]
    pd.testing.assert_frame_equal(
        a_g[a_g.month.isin(upto)].reset_index(drop=True), b_g[b_g.month.isin(upto)].reset_index(drop=True)
    )
    assert [m for m in a_m if m["month"] in upto] == [m for m in b_m if m["month"] in upto]


def test_stock_before_the_change_is_untouched_too():
    base = scenario(seed=3)
    t0 = 10
    a = simulate(**base)[1]
    changed = dict(base)
    demand = base["demand"].copy()
    demand[t0:] = 0
    changed["demand"] = demand
    b = simulate(**changed)[1]
    early = MONTHS[:t0]
    pd.testing.assert_frame_equal(
        a[a.month.isin(early)].reset_index(drop=True), b[b.month.isin(early)].reset_index(drop=True)
    )


def test_never_buys_more_than_the_requirement():
    sc = scenario()
    window = sc["settings"].takeover + sc["lead"]
    already = float(sc["inherited"][:window].sum())  # arrives on the original plan, before the planner acts
    sc["requirement"] = already + 500.0
    sc["demand"] = sc["demand"] * 3  # heavy use: the planner would love to buy far more
    monthly, _, _ = simulate(**sc)
    assert monthly["plan_arrived"].sum() <= sc["requirement"] + 1e-6
    assert monthly["plan_arrived"].sum() > already  # it did use the room it had


def test_no_orders_when_the_requirement_is_already_met():
    sc = scenario()
    sc["requirement"] = 10.0
    _, by_godown, _ = simulate(**sc)
    assert by_godown["order"].sum() == 0


def test_orders_land_one_lead_time_later():
    sc = scenario(lead=3, takeover=4)
    sc["demand"] = np.full((24, 3), 50.0)
    monthly, by_godown, _ = simulate(**sc)
    ordered = by_godown.groupby("month")["order"].sum()
    arrived = by_godown.groupby("month")["arrived"].sum()
    for i, m in enumerate(MONTHS):
        if i >= 4 and i + 3 < 24:
            assert arrived[MONTHS[i + 3]] == pytest.approx(ordered[m]), m


def test_stock_is_conserved():
    sc = scenario(seed=5)
    monthly, by_godown, moves = simulate(**sc)
    assert monthly["plan_arrived"].sum() - monthly["plan_used"].sum() == pytest.approx(
        monthly["plan_idle"].iloc[-1], abs=1e-6
    )
    assert by_godown["move_in"].sum() == pytest.approx(by_godown["move_out"].sum())
    assert (by_godown["stock_end"] >= -1e-9).all()


def test_idle_stock_is_moved_instead_of_reordered():
    """Godown C holds a pile it never uses; B keeps running short. The planner should move C's stock to B."""
    sc = scenario(lead=2, takeover=3)
    demand = np.zeros((24, 3))
    demand[:, 1] = 80.0  # only B has use
    inherited = np.zeros((24, 3))
    inherited[0, 2] = 5000.0  # a big pile arrives at C and nobody there uses it
    inherited[:6, 1] = 30.0
    sc.update(demand=demand, inherited=inherited, requirement=float(inherited.sum()))
    _, _, moves = simulate(**sc)
    assert any(m["from"] == "C" and m["to"] == "B" for m in moves)


def test_starving_the_site_does_not_count():
    assert counts({"shortage_pct": 0.0})
    assert counts({"shortage_pct": MAX_SHORTAGE_SHARE})
    assert not counts({"shortage_pct": 0.05})


# ---- real data ------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ledger(real_ds):
    return real_ds.ledger


@real
def test_real_poles_cut_idle_without_starving(ledger):
    r = replay(ledger, "MPO010014")
    s = r.summary
    assert s["reduction"] > 0.3
    assert counts(s)
    assert s["peak_plan"] < s["peak_real"]
    assert s["plan_received"] <= s["requirement"] + 1e-6


@real
def test_real_replay_does_not_use_the_future(ledger):
    """Same check as above, on Lumino's own poles: scramble use from month 24 on; decisions up to month 24 hold."""
    from core.ledger import item_site
    from core.planner import allocation_shares, lead_months, unit_step

    code, t0 = "MPO010014", 24
    grid = ledger.grid[ledger.grid.item_code == code]
    working = ledger.working
    mat = lambda col: (
        grid.pivot(index="month", columns="godown", values=col)
        .reindex(index=ledger.months, columns=working)
        .fillna(0.0)
        .to_numpy()
    )
    used = item_site(ledger, code)["used"].to_numpy()
    demand = used[:, None] * allocation_shares(mat("net_issue"))
    kw = dict(
        months=ledger.months,
        godowns=working,
        inherited=mat("received"),
        requirement=float(mat("received").sum()),
        lead=lead_months(150),
        step=unit_step("PCS"),
        settings=Settings(),
    )
    a, _ = decisions(simulate(demand=demand, **kw))
    shaken = demand.copy()
    shaken[t0:] = shaken[t0:][::-1] * 3
    b, _ = decisions(simulate(demand=shaken, **kw))
    upto = ledger.months[: t0 + 1]
    pd.testing.assert_frame_equal(
        a[a.month.isin(upto)].reset_index(drop=True), b[b.month.isin(upto)].reset_index(drop=True)
    )


def forecast_check(demand, lead=2):
    """Run the simulator on a given demand and score the forecast the way the app does (lead time plus one months)."""
    sc = scenario(lead=lead)
    sc["demand"] = demand
    monthly, by_godown, moves = simulate(**sc)
    monthly["used"] = demand.sum(axis=1)
    monthly["forecast"] = by_godown.groupby("month", sort=True)["forecast_use"].sum().reindex(MONTHS).to_numpy()
    monthly["real_arrived"] = sc["inherited"].sum(axis=1)
    monthly["real_idle"] = monthly["plan_idle"]
    move_df = pd.DataFrame(moves, columns=["month", "when", "from", "to", "qty"])
    return summarise(
        monthly, by_godown, move_df, eval_start=4 + lead, requirement=float(sc["inherited"].sum()), step=1.0, lead=lead
    )


def test_forecast_check_is_exact_when_use_is_flat():
    s = forecast_check(np.tile([50.0, 30.0, 10.0], (24, 1)))
    assert s["span_months"] == 3
    assert s["forecast_error"] == pytest.approx(0.0, abs=1e-9)
    assert s["forecast_bias"] == pytest.approx(0.0, abs=1e-9)


def test_forecast_check_shows_a_forecast_that_runs_low_when_use_rises():
    rising = np.outer(np.linspace(20, 140, 24), [1.0, 0.6, 0.2])
    s = forecast_check(rising)
    assert s["forecast_error"] > 0.05
    assert s["forecast_bias"] < 0  # trailing averages sit below a rising line, so the forecast ran low
    assert s["forecast_error"] >= abs(s["forecast_bias"]) - 1e-9


def test_forecast_check_shows_a_forecast_that_runs_high_when_use_falls():
    falling = np.outer(np.linspace(140, 20, 24), [1.0, 0.6, 0.2])
    s = forecast_check(falling)
    assert s["forecast_bias"] > 0


def test_future_receipts_reach_the_planner_only_through_their_total():
    """The cap on total buying is the one place the future enters (the project's real total stands in for the BOQ total).
    Move future receipts to another month without changing the total and nothing changes; with no cap they do not matter at all."""
    sc = scenario()
    moved = sc["inherited"].copy()
    moved[22] += moved[20]
    moved[20] = 0.0  # same total, later month, well after the inherited months
    pd.testing.assert_frame_equal(decisions(simulate(**sc))[0], decisions(simulate(**{**sc, "inherited": moved}))[0])

    free = dict(sc, requirement=float("inf"))
    bigger = sc["inherited"].copy()
    bigger[22] += 5000.0
    pd.testing.assert_frame_equal(
        decisions(simulate(**free))[0], decisions(simulate(**{**free, "inherited": bigger}))[0]
    )


def test_the_cap_binds_when_it_is_low_and_vanishes_when_removed():
    sc = scenario()
    sc["demand"] = sc["demand"] * 3  # heavy use, so the planner has reason to order
    already = float(sc["inherited"][: sc["settings"].takeover + sc["lead"]].sum())
    capped = simulate(**dict(sc, requirement=already + 500.0))[0]
    free = simulate(**dict(sc, requirement=float("inf")))[0]
    assert capped["plan_arrived"].sum() < free["plan_arrived"].sum()


@real
def test_real_poles_saving_under_each_assumption(ledger):
    """The numbers the Plan it screen shows under 'What the saving depends on' for poles, so they cannot drift unnoticed."""
    code = "MPO010014"
    base = replay(ledger, code).summary
    nocap = replay(ledger, code, Settings(capped=False)).summary
    godown = replay(ledger, code, Settings(basis="issues")).summary
    assert base["avg_real"] == pytest.approx(29572, abs=1) and base["reduction"] == pytest.approx(0.48, abs=0.01)
    assert nocap["reduction"] == pytest.approx(0.37, abs=0.01) and nocap["plan_received"] > base["plan_received"]
    assert godown["avg_real"] == pytest.approx(18390, abs=1) and godown["reduction"] == pytest.approx(0.33, abs=0.01)


@real
def test_godown_basis_compares_like_with_like(ledger):
    code = "MPO010014"
    all_idle, godown = replay(ledger, code), replay(ledger, code, Settings(basis="issues"))
    assert (godown.monthly["real_idle"] == godown.monthly["real_in_godowns"]).all()
    assert (
        all_idle.monthly["real_idle"] >= godown.monthly["real_idle"] - 1e-9
    ).all()  # contractors hold the difference
    assert godown.monthly["used"].sum() >= all_idle.monthly["used"].sum() - 1e-6  # issues run ahead of use
