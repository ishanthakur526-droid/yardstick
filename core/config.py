"""What differs from one project to the next, in one place.

A project's data may carry a small `meta` table (two columns: key, value). Anything it does not say is worked out from
the records, and each guess is kept as a plain-English note so the screen can show it as an assumption to check.

Keys a `meta` table may hold:
    project, company, source, about    labels for the screen and the exports
    anchor_code                        the item a BOQ counts in (poles on a distribution project)
    anchor_noun                        what to call one of it ("pole")
    closing_stores                     locations that only take leftovers at wind-down, separated by ";"
    min_parents, min_sets              when a set imbalance is big enough to flag (see core/sets.py)
    requisition_example                the example BOQ quantity the buy list starts from
    sample                             "1" for made-up data
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

# A set imbalance is only flagged once a godown has issued enough parents, and only when the gap is big enough to
# matter. On the project the defaults were set on, they were 2,000 parents and 300 sets: about 0.84% and 0.126% of the
# biggest set's total issue. A project of another size gets the same shares of its own biggest set.
MIN_PARENTS_SHARE = 0.0084
MIN_SETS_SHARE = 0.00126
ANCHOR_ROLES = {"anchor", "primary"}


@dataclass(frozen=True)
class ProjectConfig:
    project: str = "Project"
    company: str = ""
    source: str = ""
    about: str = ""
    anchor_code: str | None = None
    anchor_noun: str = "unit"
    closing_stores: tuple[str, ...] = ()
    min_parents: float = 2000.0
    min_sets: float = 300.0
    requisition_example: float | None = None
    is_sample: bool = False
    assumed: tuple[str, ...] = ()  # what was worked out rather than given

    @property
    def anchor_plural(self) -> str:
        noun = self.anchor_noun.strip() or "unit"
        return noun if noun.endswith("s") else noun + "s"

    @property
    def title(self) -> str:
        return f"{self.project} ({self.company})" if self.company else self.project


def read_meta(meta: pd.DataFrame | None) -> dict[str, str]:
    if meta is None or meta.empty or not {"key", "value"} <= set(meta.columns):
        return {}
    out = {}
    for k, v in zip(meta["key"], meta["value"]):
        if pd.notna(k) and pd.notna(v) and str(v).strip():
            out[str(k).strip().lower()] = str(v).strip()
    return out


def _number(text: str | None) -> float | None:
    try:
        x = float(str(text).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) and x >= 0 else None


def infer_closing(flows: pd.DataFrame) -> list[str]:
    """A closing store takes stock in by transfer and never receives from vendors or issues to contractors."""
    by = flows.groupby("godown")[["received", "tr_in", "issue"]].sum()
    found = by[(by["received"].abs() < 1e-9) & (by["issue"].abs() < 1e-9) & (by["tr_in"] > 0)]
    working = len(by) - len(found)
    return sorted(found.index.astype(str)) if working >= 1 else []


def infer_anchor(items: pd.DataFrame, sets: pd.DataFrame, flows: pd.DataFrame) -> str | None:
    """The item a BOQ counts in: one marked as anchor/primary, else the longest-lead item that is not a set's child
    (on a distribution project, the pole), ties broken by how much of it went out."""
    if items.empty:
        return None
    roles = items["role"].astype(str).str.strip().str.lower() if "role" in items else pd.Series("", index=items.index)
    marked = items.loc[roles.isin(ANCHOR_ROLES), "item_code"]
    if len(marked):
        return str(marked.iloc[0])
    children = set(sets["child_code"]) if len(sets) else set()
    pool = items[~items["item_code"].isin(children)]
    if pool.empty:
        pool = items
    out = flows.assign(net=flows["issue"] - flows["ret"]).groupby("item_code")["net"].sum()
    ranked = pool.assign(_out=pool["item_code"].map(out).fillna(0.0)).sort_values(
        ["lead_time_days", "_out"], ascending=False
    )
    return str(ranked["item_code"].iloc[0])


def build_config(tables: dict[str, pd.DataFrame]) -> ProjectConfig:
    meta = read_meta(tables.get("meta"))
    items, flows = tables["items"], tables["flows"]
    sets = tables.get("sets")
    sets = sets if sets is not None else pd.DataFrame(columns=["parent_code", "child_code"])
    notes: list[str] = []
    codes = set(items["item_code"].astype(str))
    places = set(flows["godown"].astype(str))

    if "closing_stores" in meta:
        closing = [s.strip() for s in meta["closing_stores"].split(";") if s.strip() in places]
    else:
        closing = infer_closing(flows)
        if closing:
            notes.append(
                f"Treated as the closing store (it only takes stock in by transfer): {', '.join(closing)}. "
                "The planner does not plan for it."
            )

    anchor = meta.get("anchor_code")
    if anchor not in codes:
        anchor = infer_anchor(items, sets, flows)
        if anchor:
            name = str(items.loc[items["item_code"] == anchor, "item_name"].iloc[0])
            notes.append(
                f"The BOQ is assumed to count in {name} ({anchor}), the longest-lead item that is not part of a set."
            )

    biggest = 0.0
    if len(sets):
        out = flows.assign(net=flows["issue"] - flows["ret"]).groupby("item_code")["net"].sum()
        biggest = float(out.reindex(sets["parent_code"].unique()).fillna(0.0).max())
    min_parents = _number(meta.get("min_parents"))
    min_sets = _number(meta.get("min_sets"))
    if min_parents is None or min_sets is None:
        scaled_p = max(20.0, round(biggest * MIN_PARENTS_SHARE)) if biggest else 2000.0
        scaled_s = max(5.0, round(biggest * MIN_SETS_SHARE)) if biggest else 300.0
        min_parents = scaled_p if min_parents is None else min_parents
        min_sets = scaled_s if min_sets is None else min_sets
        if len(sets):
            notes.append(
                f"A set imbalance is flagged once a godown has issued {min_parents:,.0f} parents and the gap is "
                f"{min_sets:,.0f} sets or more (scaled to this project's size)."
            )

    return ProjectConfig(
        project=meta.get("project", "Uploaded project"),
        company=meta.get("company", ""),
        source=meta.get("source", ""),
        about=meta.get("about", ""),
        anchor_code=anchor,
        anchor_noun=meta.get("anchor_noun", "unit"),
        closing_stores=tuple(closing),
        min_parents=float(min_parents),
        min_sets=float(min_sets),
        requisition_example=_number(meta.get("requisition_example")),
        is_sample=meta.get("sample", "0") in {"1", "true", "yes"},
        assumed=tuple(notes),
    )
