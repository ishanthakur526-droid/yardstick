"""Display helpers shared by the sections."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core.fmt import indian, month_label

ALL_GODOWNS = "All godowns"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def number_columns(df: pd.DataFrame, decimals: int) -> dict:
    """Column formats for a numeric table: the item's own decimals, one decimal for months of cover."""
    cfg = {}
    for col in df.columns:
        if col == "Month":
            continue
        cfg[col] = st.column_config.NumberColumn(format="%.1f" if col == "Months of cover" else f"%.{decimals}f")
    return cfg


def qty(v: float, decimals: int) -> str:
    return indian(v, decimals)


def lever_rows_table(rows: pd.DataFrame, dec: int) -> pd.DataFrame:
    """The lever waterfall as a table: one row per step."""
    return pd.DataFrame(
        {
            "Step": rows["label"],
            "Average idle": [indian(v, dec) for v in rows["avg_idle"]],
            "vs actual": ["" if i == 0 else f"{-c:+.0%}" for i, c in enumerate(rows["cut"])],
            "In godowns (avg)": [indian(v, dec) for v in rows["avg_godowns"]],
            "With contractors (avg)": [indian(v, dec) for v in rows["avg_contractors"]],
            "Peak idle": [
                f"{indian(v, dec)} ({month_label(m)})" for v, m in zip(rows["peak_idle"], rows["peak_month"])
            ],
            "Could not supply": ["–" if i == 0 else f"{v:.1%}" for i, v in enumerate(rows["shortage_pct"])],
            "Counts?": ["–" if i == 0 else ("Yes" if ok else "No: ran short") for i, ok in enumerate(rows["counts"])],
        }
    )
