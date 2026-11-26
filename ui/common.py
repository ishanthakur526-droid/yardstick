"""Display helpers shared by the sections."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core.fmt import indian, month_label

ALL_GODOWNS = "All godowns"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# A row of number tiles that wraps onto a second line when the screen is too narrow for all of them, instead of cutting
# a seven-digit quantity off with "…". Static text only (never data) goes into this style block.
TILES_CSS = (
    "<style>"
    "[class*='st-key-tiles'] [data-testid='stHorizontalBlock'] {flex-wrap: wrap; row-gap: 0.75rem;}"
    "[class*='st-key-tiles'] [data-testid='stColumn'] {flex: 1 1 10rem !important; min-width: 10rem !important;}"
    "[class*='st-key-tiles'] [data-testid='stMetricValue'] div "
    "{white-space: normal !important; overflow: visible !important; text-overflow: clip !important;}"
    "[class*='st-key-tiles'] [data-testid='stMetricValue'], [class*='st-key-tiles'] [data-testid='stMetricValue'] * "
    "{font-size: clamp(1.2rem, 1.6vw, 2rem) !important; line-height: 1.25;}"
    "[class*='st-key-tiles'] [data-testid='stMetricLabel'] * {white-space: normal !important;}"
    "</style>"
)


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


def tiles(key: str, n: int = 5) -> list:
    """`n` columns for number tiles, in a keyed container so the tiles wrap rather than cut numbers off."""
    st.markdown(TILES_CSS, unsafe_allow_html=True)
    return list(st.container(key=key).columns(n))
