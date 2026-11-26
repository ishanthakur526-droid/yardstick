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
from core.planner import (  # noqa: E402
    DEFAULT_COVER_WEEKS,
    MAX_SHORTAGE_SHARE,
    WEEKS_PER_MONTH,
    Settings,
    counts,
    replay,
    simulate,
    summarise,
)

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
    shaken = demand.copy()
    shaken[t0:] = shaken[t0:][::-1] * 3
    upto = ledger.months[: t0 + 1]
    for cover in (None, 0.0, DEFAULT_COVER_WEEKS / WEEKS_PER_MONTH):  # as given, and with the contractor pool
        a, _ = decisions(simulate(demand=demand, cover=cover, **kw))
        b, _ = decisions(simulate(demand=shaken, cover=cover, **kw))
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
def test_real_poles_saving_under_each_lever(ledger):
    """The poles numbers behind the lever waterfall, so they cannot drift unnoticed. Cover 0 is the old headline
    (basis "use"); as drawn replaced the old basis "issues"."""
    code = "MPO010014"
    zero = replay(ledger, code, Settings(cover_weeks=0)).summary
    nocap = replay(ledger, code, Settings(capped=False, cover_weeks=0)).summary
    drawn = replay(ledger, code, Settings(cover_weeks=None)).summary
    drawn_free = replay(ledger, code, Settings(cover_weeks=None, capped=False)).summary
    assert zero["avg_real"] == pytest.approx(29572, abs=1) and zero["avg_plan"] == pytest.approx(15347, abs=1)
    assert zero["reduction"] == pytest.approx(0.48, abs=0.01)
    assert nocap["reduction"] == pytest.approx(0.37, abs=0.01) and nocap["plan_received"] > zero["plan_received"]
    # as drawn is scored on all idle stock, and contractors' returns go back into the godowns (the old basis dropped them)
    assert drawn["avg_plan"] == pytest.approx(23688, abs=1) and drawn["reduction"] == pytest.approx(0.20, abs=0.01)
    assert drawn_free["reduction"] < 0.0  # ordering on a forecast alone, with no cap and no issue rule, saves nothing
    assert drawn["cap_source"] == "stand-in" and drawn_free["cap_source"] == "none"


@real
def test_as_drawn_keeps_what_contractors_really_held(ledger):
    code = "MPO010014"
    drawn, zero = replay(ledger, code, Settings(cover_weeks=None)), replay(ledger, code, Settings(cover_weeks=0))
    m = drawn.monthly
    assert (m["plan_pool"] == m["real_with_contractors"]).all()
    assert (m["plan_idle"] == m["plan_godown"] + m["plan_pool"]).all()
    assert (m["real_idle"] == m["real_in_godowns"] + m["real_with_contractors"]).all()
    assert (zero.monthly["real_idle"] == m["real_idle"]).all()  # every mode is scored against the same actual
    assert m["used"].sum() >= zero.monthly["used"].sum() - 1e-6  # issues run ahead of use


# ---- lever 2: contractor cover ---------------------------------------------------------------


def pooled(seed=1, cover=0.5, lead=2, takeover=4):
    """The same scenario, with `demand` read as the site's use by godown and contractors held to `cover` months."""
    return dict(scenario(seed=seed, lead=lead, takeover=takeover), cover=cover)


def test_zero_cover_is_the_old_plan_on_site_use():
    """With no cover, contractors get exactly what the site uses: the pool stays empty and every decision, every
    number, matches the planner that was handed site use directly (the old basis "use")."""
    for seed in (1, 2, 3):
        direct = simulate(**scenario(seed=seed))
        zero = simulate(**pooled(seed=seed, cover=0.0))
        pd.testing.assert_frame_equal(direct[1], zero[1])
        assert direct[2] == zero[2]
        cols = ["plan_arrived", "plan_order", "plan_used", "plan_shortage", "plan_idle"]
        pd.testing.assert_frame_equal(direct[0][cols], zero[0][cols])
        assert (zero[0]["plan_pool"] == 0).all()


def test_no_look_ahead_with_contractor_cover():
    """Scramble the site's use from month T0 on: nothing decided or held before T0 may move, the pool included."""
    for cover in (0.25, 0.5, 1.5):
        base = pooled(cover=cover)
        t0 = 12
        a = simulate(**base)
        changed = dict(base)
        demand = base["demand"].copy()
        demand[t0:] = demand[t0:][::-1] * 7
        changed["demand"] = demand
        b = simulate(**changed)
        upto = MONTHS[: t0 + 1]
        a_g, a_m = decisions(a)
        b_g, b_m = decisions(b)
        pd.testing.assert_frame_equal(
            a_g[a_g.month.isin(upto)].reset_index(drop=True), b_g[b_g.month.isin(upto)].reset_index(drop=True)
        )
        assert [m for m in a_m if m["month"] in upto] == [m for m in b_m if m["month"] in upto]
        early = MONTHS[:t0]
        pd.testing.assert_frame_equal(
            a[0][a[0].month.isin(early)].reset_index(drop=True), b[0][b[0].month.isin(early)].reset_index(drop=True)
        )


def test_the_pool_is_never_negative_and_stock_is_conserved():
    for seed, cover in ((1, 0.5), (4, 1.0), (5, 2.0)):
        sc = pooled(seed=seed, cover=cover)
        monthly, by_godown, _ = simulate(**sc)
        assert (monthly["plan_pool"] >= 0).all()
        # what arrived, less what the site used, is in the godowns or with contractors
        assert monthly["plan_arrived"].sum() - monthly["plan_used"].sum() == pytest.approx(
            monthly["plan_godown"].iloc[-1] + monthly["plan_pool"].iloc[-1], abs=1e-6
        )
        # month by month: the pool moves by what was handed out less what was used
        pool = monthly["plan_pool"].to_numpy()
        before = np.concatenate([[0.0], pool[:-1]])
        assert np.allclose(pool, before + monthly["plan_issued"] - monthly["plan_used"], atol=1e-6)
        assert (by_godown["stock_end"] >= -1e-9).all()


def test_contractors_hold_about_the_cover_they_are_allowed():
    sc = pooled(cover=1.0)
    sc["demand"] = np.full((24, 3), 30.0)  # steady use: 90 a month across the site
    sc["requirement"] = float("inf")
    monthly, _, _ = simulate(**sc)
    assert monthly["plan_pool"].iloc[8:].to_numpy() == pytest.approx(90.0, abs=1e-6)
    thin = simulate(**dict(sc, cover=0.25))[0]
    assert thin["plan_pool"].iloc[8:].mean() < monthly["plan_pool"].iloc[8:].mean()


def test_a_shortfall_eats_the_contractors_cover_before_it_stops_the_site():
    """When the godowns cannot top contractors up, the site keeps working on what contractors hold; only use the pool
    cannot cover counts as a shortage."""
    sc = pooled(cover=1.0, takeover=4)
    sc["demand"] = np.full((24, 3), 30.0)
    inherited = np.zeros((24, 3))
    inherited[0] = 400.0  # one early delivery, then nothing: the cap allows no more
    sc.update(inherited=inherited, requirement=float(inherited.sum()))
    monthly, _, _ = simulate(**sc)
    asked_not_given = (monthly["plan_asked"] - monthly["plan_issued"]).sum()
    assert asked_not_given > 0
    assert monthly["plan_shortage"].sum() < asked_not_given
    assert monthly["plan_used"].sum() + monthly["plan_shortage"].sum() == pytest.approx(sc["demand"].sum())


def test_the_boq_caps_buying_when_the_data_has_one(sample_ds):
    """Lever 1 on the sample project, which carries a BOQ: the planner never buys past it, and says where its cap came
    from. Without a BOQ, what was really bought stands in, and it is labelled as a stand-in."""
    ledger = sample_ds.ledger
    for code in ledger.items["item_code"]:
        boq = sample_ds.boq_qty(code)
        assert boq is not None and boq > 0
        r = replay(ledger, code, Settings(), boq=boq)
        assert r.summary["cap_source"] == "boq"
        assert r.summary["requirement"] == boq
        planned = r.monthly["plan_arrived"].iloc[r.settings.takeover + r.lead_months :].sum()
        inherited = r.monthly["plan_arrived"].iloc[: r.settings.takeover + r.lead_months].sum()
        assert planned + inherited <= max(boq, inherited) + 1e-6
    assert replay(ledger, "SPO001").summary["cap_source"] == "stand-in"
    assert replay(ledger, "SPO001", Settings(capped=False), boq=1.0).summary["cap_source"] == "none"


def test_as_drawn_books_contractors_returns_back_into_the_godowns():
    """Contractors who really drew stock also really returned some. Those returns refill the godown they went to."""
    sc = scenario(seed=2)
    back = np.zeros((24, 3))
    back[5, 1] = 70.0
    with_back = simulate(**sc, returned=back)[0]
    without = simulate(**sc)[0]
    assert with_back["plan_returned"].sum() == pytest.approx(70.0)
    assert with_back["plan_godown"].iloc[5] == pytest.approx(without["plan_godown"].iloc[5] + 70.0)
    assert with_back["plan_arrived"].sum() + 70.0 - with_back["plan_issued"].sum() == pytest.approx(
        with_back["plan_godown"].iloc[-1], abs=1e-6
    )
