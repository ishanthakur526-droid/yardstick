"""Display helpers shared by the sections."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core.fmt import indian

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
