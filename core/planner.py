"""Plan it: replay the project month by month, deciding only from what was known at the time.

At the start of each month the planner knows the past: what the godowns were asked to hand out so far, what is in
each godown, and what is already on order. Then it does three things:

  ORDER  enough to cover the lead time plus this month of forecast demand, plus a safety margin (never more in total
         than the cap: the BOQ when the data has one, else what the project really bought, as a stand-in);
  MOVE   stock above one godown's own lead-time need goes to a godown that would run short this month;
  HOLD   nothing to do.

Then the month happens (what the site really used, from the records) and the result is scored:

  idle      stock lying around at month end, in the godowns and with contractors -> compared with what really happened
  shortage  use the plan could not have supplied -> a plan that "wins" by starving the site does not count
            (see MAX_SHORTAGE_SHARE)

Two levers the company controls, each switched on or off:

  BOQ CAP          `capped`: never order more in total than the bill of quantities.
  CONTRACTOR COVER `cover_weeks`: contractors are issued only enough to end each month holding N weeks of forecast use.
                   They are one project-wide pool (the records do not say which contractor holds what):
                       issue  = max(0, use + cover x forecast - pool)
                       pool  <- pool + what the godowns handed out - use
                   With 0 weeks they get exactly what the site uses. `None` keeps what they really drew.

Honest limits, all shown on screen:
- Usage is only recorded for the whole project, so it is spread over the godowns by what each godown handed out
  that month. That is an estimate.
- The first `takeover` months run on the original plan (what really arrived), because a forecast needs history.
  Orders placed before then, and landing within one lead time after it, are inherited from the real receipts.
- Stock is allowed to move between godowns within the month (the records show same-day transfer vouchers).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .ledger import Ledger, item_site, unit_decimals

MONTH_DAYS = 30
WEEKS_PER_MONTH = 52 / 12
# A plan that cuts idle stock by letting the site run dry does not count. This is the line we draw.
MAX_SHORTAGE_SHARE = 0.01
# Lever 2's default: issued weekly with about one more week in hand, so contractors end a month holding two weeks of
# forecast use. Fixed before any result with it was looked at.
DEFAULT_COVER_WEEKS = 2.0


@dataclass(frozen=True)
class Settings:
    takeover: int = 9  # months of the original plan before the planner starts ordering (it needs history)
    window: int = 6  # months of recent demand the forecast averages
    sd_window: int = 12  # months of recent demand the safety margin is measured on
    safety: float = 2.33  # safety margin in standard deviations (about a 99% service level on a normal curve)
    capped: bool = True  # lever 1: never order more in total than the BOQ (or the stand-in for it)
    # lever 2: weeks of forecast use contractors may hold at month end. 0: they get exactly what the site uses.
    # None: they draw what they really drew (and hold what they really held).
    cover_weeks: float | None = DEFAULT_COVER_WEEKS


@dataclass
class Replay:
    code: str
    unit: str
    lead_months: int
    settings: Settings
    months: list[str]
    godowns: list[str]
    eval_start: int  # first month index whose supply the planner decided
    monthly: pd.DataFrame  # one row per month
    by_godown: pd.DataFrame  # one row per month x godown
    moves: pd.DataFrame  # one row per move
    summary: dict
    labels: dict[str, str] | None = None  # godown -> short label


def unit_step(unit: str) -> float:
    """The smallest order quantity for a unit (the ledger's per-item `step` takes an explicit `decimals` into account)."""
    return 10.0 ** -unit_decimals(unit)


def lead_months(lead_days: float) -> int:
    return max(1, int(round(lead_days / MONTH_DAYS)))


def allocation_shares(net_issue: np.ndarray) -> np.ndarray:
    """Where the site's use happened: each godown's share of what was handed out that month (known at month end)."""
    pos = np.clip(net_issue, 0.0, None)
    t_len, g_len = pos.shape
    w = np.zeros((t_len, g_len))
    for t in range(t_len):
        total = pos[t].sum()
        if total > 0:
            w[t] = pos[t] / total
            continue
        recent = pos[max(0, t - 6) : t].sum(axis=0)
        w[t] = recent / recent.sum() if recent.sum() > 0 else 1.0 / g_len
    return w


def _up(x: np.ndarray, step: float) -> np.ndarray:
    return np.where(x > 1e-9, np.round(np.ceil(np.round(x / step, 6)) * step, 2), 0.0)


def _down(x: float, step: float) -> float:
    return math.floor(round(x / step, 6)) * step


def _shares(window_demand: np.ndarray) -> np.ndarray:
    total = window_demand.sum()
    return window_demand / total if total > 0 else np.full(len(window_demand), 1.0 / len(window_demand))


def forecast(history: np.ndarray, settings: Settings) -> float:
    """Next month's demand from the months before it only: the higher of the last 3 and last `window` months' average
    (it reacts when the site speeds up). No history, no forecast."""
    if len(history) == 0:
        return 0.0
    return float(max(history[-settings.window :].mean(), history[-3:].mean()))


@dataclass
class Decision:
    """The planner's call at the start of one month."""

    forecast: np.ndarray  # per godown, demand expected this month
    safety: np.ndarray  # per godown
    target: np.ndarray  # per godown: cover for the lead time plus this month, with the safety margin
    have: np.ndarray  # per godown, after the moves
    moves: list[tuple[int, int, float]] = field(default_factory=list)  # (from, to, qty)
    order: np.ndarray | None = None  # per godown, lands one lead time later


def decide(
    past_total: np.ndarray,
    past_by_godown: np.ndarray,
    have: np.ndarray,
    coming: np.ndarray,
    room: float,
    lead: int,
    step: float,
    settings: Settings,
) -> Decision:
    """Order, move or hold, from what is known at the start of the month.

    `past_total` and `past_by_godown` are the demand on the godowns in the months BEFORE this one (the caller passes
    only those, so the future cannot leak in). `have` is stock on hand now, `coming` what lands before a new order could,
    `room` how much more may still be bought in total."""
    g_len = len(have)
    have = have.astype(float).copy()
    f = forecast(past_total, settings)
    sd = float(past_total[-settings.sd_window :].std()) if len(past_total) else 0.0
    share = (
        _shares(past_by_godown[-settings.window :].sum(axis=0)) if len(past_by_godown) else np.full(g_len, 1 / g_len)
    )
    f_g, safe_g = f * share, settings.safety * sd * share
    need_now = f_g + safe_g
    target = f_g * (lead + 1) + safe_g * math.sqrt(lead + 1)
    position = have + coming

    moves: list[tuple[int, int, float]] = []
    surplus = np.maximum(0.0, np.minimum(have - need_now, position - target))
    deficit = np.maximum(0.0, need_now - have)
    for j in np.argsort(-deficit):
        if deficit[j] <= 0:
            break
        for i in np.argsort(-surplus):
            if i == j or surplus[i] <= 0:
                continue
            q = _down(min(deficit[j], surplus[i]), step)
            if q <= 0:
                continue
            have[i] -= q
            have[j] += q
            surplus[i] -= q
            deficit[j] -= q
            moves.append((int(i), int(j), q))
            if deficit[j] <= 0:
                break

    position = have + coming
    wanted = min(max(0.0, float(target.sum() - position.sum())), room)
    order = np.zeros(g_len)
    if wanted > 0:
        gap = np.maximum(0.0, target - position)
        weights = gap / gap.sum() if gap.sum() > 0 else share
        order = _up(wanted * weights, step)
        if order.sum() > room + 1e-9:  # rounding up must not break the cap
            order = np.array([_down(x, step) for x in wanted * weights])
    return Decision(forecast=f_g, safety=safe_g, target=target, have=have, moves=moves, order=order)


def simulate(
    *,
    months: list[str],
    godowns: list[str],
    demand: np.ndarray,
    inherited: np.ndarray,
    requirement: float,
    lead: int,
    step: float,
    settings: Settings,
    cover: float | None = None,
    returned: np.ndarray | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, list[dict]]:
    """The core loop. At month t it only reads `demand[:t]`; tests prove changing later months changes nothing before.

    `cover=None`: `demand` is what each godown hands out each month, as given.
    `cover=c` (months): `demand` is the site's use by godown. Contractors are one pool that ends each month holding
    `c` months of forecast use (the rule runs from the first month; the pool starts empty, as a project does).
    `returned`: stock contractors hand back to each godown, booked at month end (only with `cover=None`, where they
    draw what they really drew and so return what they really returned)."""
    t_len, g_len = demand.shape
    pooled = cover is not None
    site_use = demand.sum(axis=1)
    asked = np.zeros((t_len, g_len))  # what the godowns were asked to hand out
    asked_total = np.zeros(t_len)
    h = settings.takeover
    pipe = np.zeros((t_len + lead + 2, g_len))
    for m in range(0, min(h + lead - 1, t_len - 1) + 1):
        pipe[m] = inherited[m]
    stock = np.zeros(g_len)
    pool = 0.0
    month_rows, godown_rows, moves = [], [], []

    for t in range(t_len):
        arrivals = pipe[t].copy()
        have = stock + arrivals
        f_g = np.zeros(g_len)
        safe_g = np.zeros(g_len)
        target = np.zeros(g_len)
        order = np.zeros(g_len)
        move_in = np.zeros(g_len)
        move_out = np.zeros(g_len)

        if t >= h:
            coming = pipe[t + 1 : t + lead].sum(axis=0)  # lands before a new order could
            room = max(0.0, requirement - float(pipe.sum()))  # never buy more in total than the cap
            d = decide(asked_total[:t], asked[:t], have, coming, room, lead, step, settings)
            f_g, safe_g, target, have, order = d.forecast, d.safety, d.target, d.have, d.order
            for i, j, q in d.moves:
                move_out[i] += q
                move_in[j] += q
                moves.append(
                    {
                        "month": months[t],
                        "when": "start of month",
                        "from": godowns[i],
                        "to": godowns[j],
                        "qty": round(q, 2),
                    }
                )
            pipe[t + lead] += order

        # What the godowns are asked for this month.
        top_up = 0.0
        if pooled:
            keep = cover * forecast(site_use[:t], settings)  # what contractors may hold at month end
            total = max(0.0, site_use[t] + keep - pool)
            top_up = total - max(0.0, site_use[t] - pool)  # the part that only refills their cover
            if site_use[t] > 0:
                need = demand[t] * (total / site_use[t])
            elif total > 0:
                need = total * _shares(asked[max(0, t - settings.window) : t].sum(axis=0))
            else:
                need = np.zeros(g_len)
        else:
            need = demand[t]
        asked[t] = need
        asked_total[t] = need.sum()

        # The month happens. Each godown serves its own demand first; leftovers cover other godowns (same-day transfers).
        own = np.minimum(need, have)
        unmet = need - own
        left = have - own
        for j in np.argsort(-unmet):
            if unmet[j] <= 1e-9:
                break
            for i in np.argsort(-left):
                if i == j or left[i] <= 1e-9:
                    continue
                q = min(unmet[j], left[i])
                if q <= 1e-9:
                    continue
                left[i] -= q
                unmet[j] -= q
                move_out[i] += q
                move_in[j] += q
                moves.append(
                    {
                        "month": months[t],
                        "when": "during the month",
                        "from": godowns[i],
                        "to": godowns[j],
                        "qty": round(q, 2),
                    }
                )
                if unmet[j] <= 1e-9:
                    break
        stock = left
        back = returned[t] if returned is not None else np.zeros(g_len)
        stock = stock + back
        short = np.maximum(unmet, 0.0)
        handed = float(need.sum() - short.sum())

        if pooled:
            # A shortfall eats into the contractors' cover first; only what the pool cannot cover stops the site.
            site_short = max(0.0, float(short.sum()) - top_up)
            used_t = float(site_use[t]) - site_short
            pool = pool + handed - used_t
            if pool < 1e-9 * max(1.0, float(site_use[t])):
                pool = 0.0
            short_g = short * (site_short / short.sum()) if short.sum() > 0 else short
        else:
            site_short = float(short.sum())
            used_t = handed
            short_g = short

        for g in range(g_len):
            if order[g] > 0:
                action = "Order"
            elif move_in[g] > 0:
                action = "Move in"
            elif move_out[g] > 0:
                action = "Move out"
            else:
                action = "Hold"
            godown_rows.append(
                {
                    "month": months[t],
                    "godown": godowns[g],
                    "forecast_use": f_g[g],
                    "safety": safe_g[g],
                    "target": target[g],
                    "arrived": arrivals[g],
                    "move_in": move_in[g],
                    "move_out": move_out[g],
                    "order": order[g],
                    "action": action,
                    "asked": need[g],
                    "shortage": short_g[g],
                    "stock_end": stock[g],
                }
            )
        month_rows.append(
            {
                "month": months[t],
                "plan_arrived": arrivals.sum(),
                "plan_order": order.sum(),
                "plan_asked": need.sum(),  # what the godowns were asked to hand out
                "plan_issued": handed,  # what they did hand out
                "plan_returned": float(back.sum()),  # handed back by contractors (as drawn only)
                "plan_used": used_t,
                "plan_shortage": site_short,
                "plan_godown": stock.sum(),
                "plan_pool": pool,  # held by contractors at month end (0 unless the pool is tracked)
                "plan_idle": stock.sum() + pool,
                "planning": t >= h,
            }
        )
    return pd.DataFrame(month_rows), pd.DataFrame(godown_rows), moves


def cap_for(settings: Settings, received_total: float, boq: float | None) -> tuple[float, str]:
    """Lever 1: the most the planner may buy in total, and where that number comes from."""
    if not settings.capped:
        return float("inf"), "none"
    if boq is not None and math.isfinite(boq) and boq > 0:
        return float(boq), "boq"
    return float(received_total), "stand-in"


def replay(ledger: Ledger, code: str, settings: Settings = Settings(), boq: float | None = None) -> Replay:
    """`boq`: the bill of quantities for this item when the data has one. Without it, what the project really bought
    stands in for it (the one place the future enters: a project knows its BOQ at the start)."""
    unit, lead, step = ledger.unit(code), lead_months(ledger.lead_days(code)), ledger.step(code)
    months = ledger.months
    working = ledger.working
    grid = ledger.grid[ledger.grid["item_code"] == code]

    def matrix(col: str) -> np.ndarray:
        return (
            grid.pivot(index="month", columns="godown", values=col)
            .reindex(index=months, columns=working)
            .fillna(0.0)
            .to_numpy()
        )

    site = item_site(ledger, code).set_index("month").reindex(months)
    used = site["used"].to_numpy()
    received = matrix("received")
    as_drawn = settings.cover_weeks is None
    if as_drawn:
        # Contractors draw what they really drew: the godowns are asked for the real issues, and contractors hold what
        # they really held.
        net = matrix("net_issue")
        demand, cover, back = np.clip(net, 0.0, None), None, np.clip(-net, 0.0, None)
    else:
        demand = used[:, None] * allocation_shares(matrix("net_issue"))
        cover = max(0.0, float(settings.cover_weeks)) / WEEKS_PER_MONTH
        back = None

    cap, cap_source = cap_for(settings, float(received.sum()), boq)
    monthly, by_godown, moves = simulate(
        months=months,
        godowns=working,
        demand=demand,
        inherited=received,
        requirement=cap,
        lead=lead,
        step=step,
        settings=settings,
        cover=cover,
        returned=back,
    )
    held = site["with_contractors"].to_numpy()
    if as_drawn:
        monthly["plan_pool"] = held
        monthly["plan_idle"] = monthly["plan_godown"] + held
        monthly["used"] = demand.sum(axis=1)  # what contractors drew: a shortage is what the godowns could not hand out
    else:
        monthly["used"] = used
    monthly["forecast"] = by_godown.groupby("month", sort=True)["forecast_use"].sum().reindex(months).to_numpy()
    monthly["real_arrived"] = site["received"].to_numpy()
    monthly["real_idle"] = site["idle_total"].to_numpy()  # godowns plus contractors, in every mode
    monthly["real_in_godowns"] = site["godown_stock"].to_numpy()
    monthly["real_with_contractors"] = site["with_contractors"].to_numpy()

    move_df = pd.DataFrame(moves, columns=["month", "when", "from", "to", "qty"])
    eval_start = min(settings.takeover + lead, len(months) - 1)
    summary = summarise(
        monthly, by_godown, move_df, eval_start, cap if math.isfinite(cap) else float(received.sum()), step, lead
    )
    summary["cap_source"] = cap_source
    return Replay(
        code=code,
        unit=unit,
        lead_months=lead,
        settings=settings,
        months=months,
        godowns=working,
        eval_start=eval_start,
        monthly=monthly,
        by_godown=by_godown,
        moves=move_df,
        summary=summary,
        labels=dict(ledger.labels),
    )


def summarise(
    monthly: pd.DataFrame,
    by_godown: pd.DataFrame,
    moves: pd.DataFrame,
    eval_start: int,
    requirement: float,
    step: float = 1.0,
    lead: int = 1,
) -> dict:
    """Scores over the months the planner's own orders could affect (from `eval_start`)."""
    ev = monthly.iloc[eval_start:]
    used_total = float(ev["used"].sum())
    short_total = float(ev["plan_shortage"].sum())
    pr, pp = ev["real_idle"], ev["plan_idle"]
    avg_real, avg_plan = float(pr.mean()), float(pp.mean())
    gb = by_godown[by_godown["month"].isin(ev["month"])]
    # What an order has to get right is the whole span it covers: the lead time plus this month. Score the forecast
    # there, against what the godowns were asked for (with no contractor cover, that is what the site used).
    f = monthly["forecast"].to_numpy()
    u = (monthly["plan_asked"] if "plan_asked" in monthly else monthly["used"]).to_numpy()
    span = lead + 1
    starts = range(eval_start, len(u) - lead)
    guess = np.array([f[t] * span for t in starts])
    real_span = np.array([u[t : t + span].sum() for t in starts])
    wape = (
        float(np.abs(guess - real_span).sum() / real_span.sum())
        if len(real_span) and real_span.sum() > 0
        else float("nan")
    )
    bias = (
        float((guess - real_span).sum() / real_span.sum()) if len(real_span) and real_span.sum() > 0 else float("nan")
    )
    return {
        "eval_from": str(ev["month"].iloc[0]),
        "eval_months": int(len(ev)),
        "span_months": span,
        "forecast_error": wape,  # how far the forecast was from what the next lead-time-plus-one months really asked
        "forecast_bias": bias,  # positive: the forecast ran high on the whole
        "months_served": int(len(ev) - (ev["plan_shortage"] > 0.5 * step).sum()),
        "avg_real": avg_real,
        "avg_plan": avg_plan,
        "reduction": (1 - avg_plan / avg_real) if avg_real > 0 else 0.0,
        "peak_real": float(pr.max()),
        "peak_real_month": str(ev.loc[pr.idxmax(), "month"]),
        "peak_plan": float(pp.max()),
        "peak_plan_month": str(ev.loc[pp.idxmax(), "month"]),
        "end_real": float(pr.iloc[-1]),
        "end_plan": float(pp.iloc[-1]),
        "used_total": used_total,
        "shortage_total": short_total,
        "shortage_pct": short_total / used_total if used_total > 0 else 0.0,
        "months_short": int((ev["plan_shortage"] > 0.5 * step).sum()),
        "orders_n": int((gb["order"] > 0).sum()),
        "orders_qty": float(gb["order"].sum()),
        "moves_n": int(len(moves[moves["month"].isin(ev["month"])])),
        "moves_qty": float(moves.loc[moves["month"].isin(ev["month"]), "qty"].sum()) if len(moves) else 0.0,
        "moves_planned_n": int(((moves["month"].isin(ev["month"])) & (moves["when"] == "start of month")).sum())
        if len(moves)
        else 0,
        "moves_planned_qty": float(
            moves.loc[moves["month"].isin(ev["month"]) & (moves["when"] == "start of month"), "qty"].sum()
        )
        if len(moves)
        else 0.0,
        "requirement": requirement,
        "real_received": float(monthly["real_arrived"].sum()),
        "plan_received": float(monthly["plan_arrived"].sum()),
    }


def counts(summary: dict) -> bool:
    """Does this plan's saving count? Only if it did not starve the site."""
    return summary["shortage_pct"] <= MAX_SHORTAGE_SHARE
