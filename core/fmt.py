"""Small display helpers: Indian digit grouping, month and godown labels."""

from __future__ import annotations

import pandas as pd


def indian(n: float | None, decimals: int | None = None) -> str:
    """12,34,567 style grouping. Small quantities (tonnes, km) keep two decimals."""
    if n is None or pd.isna(n):
        return "–"
    if decimals is None:
        decimals = 2 if abs(n) < 100 and abs(n - round(n)) > 1e-9 else 0
    s = f"{abs(n):.{decimals}f}"
    whole, _, frac = s.partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts: list[str] = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    sign = "-" if n < 0 and float(s) != 0 else ""
    return sign + whole + ("." + frac if frac else "")


def month_label(m: str) -> str:
    """'2025-02' -> 'Feb 2025'."""
    return pd.Timestamp(m + "-01").strftime("%b %Y")


def short_item(name: str, limit: int = 58) -> str:
    name = " ".join(name.split())
    return name if len(name) <= limit else name[: limit - 1].rstrip() + "…"
