"""Match the sets: a parent item only works with its child items in the right ratio.

A pole clamp is no use without its eye hook and bolts. If one member runs short, the whole set waits, however much of
the rest is lying in the store. Two views, both per godown and month:

  BALANCE  a running account. For each child: sets' worth issued so far (child issued / ratio) against parents issued
           so far. A gap is measured as a share of every set issued so far. Negative: the child is behind, so sets
           wait for it. Positive: the child is ahead and piling up. 15% off is "watch", 30% is "serious". A child that
           several sets share is not judged set by set.
  STUCK    for any month: how many sets' worth of each member is on hand, which member limits the set, and how much of
           the others is waiting on it.

The ratio problem. The master data's `ratio_of_parent` is what the company's file says, and it may not
match what the site really used. It often does not: for the eye hook it agrees (about 1 per clamp), but ring-type lugs
for a three-phase box were used at about 22 times the stated rate. So every child's ratio is also calibrated from what
the site used: the stated numbers keep their shape and are scaled by one factor so project-wide totals match. The
screen shows both and lets the user choose. Which one is right for each item is a question for the site team.

Not every child is issued set by set. How tightly a child's monthly use follows its parent's is shown as the
"pairing" (strong, loose, weak), so a flag on a loose pairing is read as a hint, not a verdict.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .ledger import Ledger

AGREES_BETWEEN = (0.75, 1.33)  # observed / stated inside this band counts as "agrees"
WATCH, SERIOUS = 0.15, 0.30  # how far the account may be off, as a share of every set issued so far
MIN_PARENTS = (
    2000  # ignore a godown until this many parent units have gone out in total (a project's config may scale it)
)
MIN_SETS = 300  # and ignore gaps smaller than this many sets
POOLED = "All godowns"


CALIBRATION_COLUMNS = [
    "set_no",
    "parent_code",
    "parent_name",
    "child_code",
    "child_name",
    "child_unit",
    "stated",
    "observed",
    "factor",
    "agrees",
    "shared",
    "monthly_correlation",
    "pairing",
    "child_used",
    "parent_used",
]


@dataclass
class SetView:
    parent: str
    parent_name: str
    members: list[str]
    names: dict[str, str]
    units: dict[str, str]
    ratios: dict[str, float]  # the ratios in use (parent = 1)
    basis: str
    account: pd.DataFrame  # one row per godown (and all together), month and child
    stock: pd.DataFrame  # one row per godown (and all together) and month
    judged: dict | None = None  # child -> is it tied closely enough to its parent to be judged?


def pairing(correlation: float) -> str:
    if np.isnan(correlation):
        return "unknown"
    return "strong" if correlation >= 0.8 else ("loose" if correlation >= 0.4 else "weak")


def calibrate(ledger: Ledger, sets: pd.DataFrame, before: str | None = None) -> pd.DataFrame:
    """Stated against observed ratio for every parent-child pair, and how tightly the child follows its parent.

    `before`: use only the months before this one ('YYYY-MM'), as the records stood then (the weekly pack is built
    "as of" a month and must not read later months)."""
    if sets is None or sets.empty:
        return pd.DataFrame(columns=CALIBRATION_COLUMNS)
    site = ledger.site if before is None else ledger.site[ledger.site["month"] < before]
    codes = ledger.items["item_code"]
    used = site.groupby("item_code")["used"].sum().reindex(codes).fillna(0.0)
    monthly = site.pivot(index="month", columns="item_code", values="used").reindex(columns=codes).fillna(0.0)
    rows = []
    for child, grp in sets.groupby("child_code", sort=False):
        expected = sum(r.ratio_of_parent * used[r.parent_code] for r in grp.itertuples())
        factor = float(used[child] / expected) if expected > 0 else float("nan")
        for r in grp.itertuples():
            ok = len(monthly) > 1 and monthly[child].std() > 0 and monthly[r.parent_code].std() > 0
            corr = float(monthly[r.parent_code].corr(monthly[child])) if ok else float("nan")
            rows.append(
                {
                    "set_no": r.set_no,
                    "parent_code": r.parent_code,
                    "parent_name": r.parent_name,
                    "child_code": child,
                    "child_name": r.child_name,
                    "child_unit": r.child_unit,
                    "stated": float(r.ratio_of_parent),
                    "observed": float(r.ratio_of_parent) * factor,
                    "factor": factor,
                    "agrees": bool(AGREES_BETWEEN[0] <= factor <= AGREES_BETWEEN[1]),  # nan (no use yet): False
                    "shared": bool(len(grp) > 1),
                    "monthly_correlation": corr,
                    "pairing": pairing(corr),
                    "child_used": float(used[child]),
                    "parent_used": float(used[r.parent_code]),
                }
            )
    return pd.DataFrame(rows, columns=CALIBRATION_COLUMNS)


def _matrix(ledger: Ledger, code: str, col: str, places: list[str]) -> pd.DataFrame:
    g = ledger.grid[ledger.grid["item_code"] == code]
    return g.pivot(index="month", columns="godown", values=col).reindex(index=ledger.months, columns=places).fillna(0.0)


def balance(
    ledger: Ledger,
    sets: pd.DataFrame,
    parent: str,
    basis: str = "observed",
    judge: tuple[str, ...] = ("strong",),
    *,
    min_parents: float = MIN_PARENTS,
    min_sets: float = MIN_SETS,
    cal: pd.DataFrame | None = None,
) -> SetView:
    """`judge` says which pairings are tight enough to flag. A child that is not issued set by set cannot be judged.
    `min_parents` and `min_sets` say when a gap is big enough to matter; `cal` saves recomputing the calibration."""
    cal = calibrate(ledger, sets) if cal is None else cal
    spec = cal[cal["parent_code"] == parent]
    children = list(spec["child_code"])
    # A child that several sets share (the G.I. bolts serve all three) is never judged against one parent: its issues cover every
    # parent at once, so setting them against a single parent's issues would show a surplus that is not there.
    judged = {r.child_code: (r.pairing in judge) and not r.shared for r in spec.itertuples()}
    members = [parent] + children
    ratios = {
        parent: 1.0,
        **{r.child_code: (r.observed if basis == "observed" else r.stated) for r in spec.itertuples()},
    }
    names = {parent: str(spec["parent_name"].iloc[0]), **dict(zip(spec["child_code"], spec["child_name"]))}
    units = {parent: str(ledger.unit(parent)), **dict(zip(spec["child_code"], spec["child_unit"]))}
    places = ledger.working

    issued = {m: _matrix(ledger, m, "net_issue", places).clip(lower=0.0) for m in members}
    stock = {m: _matrix(ledger, m, "stock", places) for m in members}

    rows = []
    for child in children:
        for place in places + [POOLED]:
            if place == POOLED:
                p = issued[parent].sum(axis=1)
                c = issued[child].sum(axis=1)
            else:
                p, c = issued[parent][place], issued[child][place]
            cum_p, cum_c = p.cumsum(), c.cumsum()
            needed = ratios[child] * cum_p
            gap = cum_c / ratios[child] - cum_p  # sets' worth: negative = child behind
            off = np.where(
                cum_p > 0, gap / cum_p.where(cum_p > 0, 1), 0.0
            )  # the gap as a share of every set issued so far
            material = (cum_p >= min_parents) & (np.abs(gap) >= min_sets)
            sev = np.where(
                ~material, "", np.where(np.abs(off) >= SERIOUS, "serious", np.where(np.abs(off) >= WATCH, "watch", ""))
            )
            if not judged[child]:
                sev = np.full(len(sev), "", dtype=object)
            rows.append(
                pd.DataFrame(
                    {
                        "month": ledger.months,
                        "godown": place,
                        "child": child,
                        "judged": judged[child],
                        "parents_out": cum_p.to_numpy(),
                        "child_out": cum_c.to_numpy(),
                        "child_needed": needed.to_numpy(),
                        "gap_sets": gap.to_numpy(),
                        "off": off,
                        "severity": sev,
                        "kind": np.where(sev == "", "", np.where(gap < 0, "behind", "ahead")),
                    }
                )
            )
    account = pd.concat(rows, ignore_index=True)
    account["reason"] = [_reason(r, names, units, ratios) if r["severity"] else "" for r in account.to_dict("records")]

    checked = [parent] + [c for c in children if judged[c]]  # only members that really move with the parent
    stock_rows = []
    for place in places + [POOLED]:
        have = {m: (stock[m].sum(axis=1) if place == POOLED else stock[m][place]) for m in members}
        cover = pd.DataFrame({m: have[m] / ratios[m] for m in checked})
        stock_rows.append(
            pd.DataFrame(
                {
                    "month": ledger.months,
                    "godown": place,
                    "limiting": cover.idxmin(axis=1).to_numpy(),
                    "sets_possible": cover.min(axis=1).to_numpy(),
                    **{f"have_{m}": have[m].to_numpy() for m in members},
                    **{f"sets_{m}": (cover[m].to_numpy() if m in checked else np.nan) for m in members},
                }
            )
        )
    view = SetView(
        parent, names[parent], members, names, units, ratios, basis, account, pd.concat(stock_rows, ignore_index=True)
    )
    view.judged = judged
    return view


def _short(name: str, limit: int = 30) -> str:
    name = " ".join(name.split())
    return name if len(name) <= limit else name[: limit - 1].rstrip() + "…"


def _reason(row: dict, names: dict[str, str], units: dict[str, str], ratios: dict[str, float]) -> str:
    child = _short(names[row["child"]])
    sets = abs(row["gap_sets"])
    share = abs(row["off"])
    if row["kind"] == "behind":
        return (
            f"{row['parents_out']:,.0f} parent units have gone out, which needs {row['child_needed']:,.0f} {units[row['child']]} of {child}, "
            f"but only {row['child_out']:,.0f} has. About {sets:,.0f} sets wait for it ({share:.0%} of everything issued so far)."
        )
    return (
        f"{row['child_out']:,.0f} {units[row['child']]} of {child} has gone out against {row['child_needed']:,.0f} needed for the "
        f"{row['parents_out']:,.0f} parent units issued. About {sets:,.0f} sets' worth is ahead of the parents ({share:.0%} of everything issued so far)."
    )


def flagged(view: SetView) -> pd.DataFrame:
    """Every godown, month and child out of balance (godowns only, not the pooled row), worst first."""
    a = view.account
    a = a[(a["severity"] != "") & (a["godown"] != POOLED)].copy()
    a["child_name"] = a["child"].map(view.names)
    a["rank"] = a["severity"].map({"serious": 1, "watch": 0})
    return a.sort_values(["rank", "off"], key=lambda s: s.abs() if s.name == "off" else s, ascending=False)


def calendar(view: SetView) -> pd.DataFrame:
    """One row per month: how many godown-child accounts are serious or on watch, and the single worst one."""
    f = flagged(view)
    rows = []
    for m in sorted(view.account["month"].unique()):
        fm = f[f["month"] == m]
        worst = fm.iloc[0] if len(fm) else None
        rows.append(
            {
                "month": m,
                "serious": int((fm["severity"] == "serious").sum()),
                "watch": int((fm["severity"] == "watch").sum()),
                "status": "serious" if (fm["severity"] == "serious").any() else ("watch" if len(fm) else "balanced"),
                "worst": "" if worst is None else f"{worst['godown']}: {worst['reason']}",
            }
        )
    return pd.DataFrame(rows)


def stuck(view: SetView, month: str) -> pd.DataFrame:
    """For one month: per godown, the member that limits the set and how much of the others is waiting on it."""
    s = view.stock[(view.stock["month"] == month) & (view.stock["godown"] != POOLED)]
    rows = []
    checked = [m for m in view.members if m == view.parent or (view.judged or {}).get(m)]
    for r in s.to_dict("records"):
        possible = r["sets_possible"]
        rows.append(
            {
                "godown": r["godown"],
                "limiting": r["limiting"],
                "sets_possible": possible,
                **{f"have_{m}": r[f"have_{m}"] for m in view.members},
                **{
                    f"waiting_{m}": (
                        0.0 if m == r["limiting"] else max(0.0, r[f"sets_{m}"] - possible) * view.ratios[m]
                    )
                    for m in checked
                },
            }
        )
    return pd.DataFrame(rows)
