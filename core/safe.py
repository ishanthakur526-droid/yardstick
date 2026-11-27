"""Small guards for text that came from a data file: names shown as markdown, and cells written to a CSV download."""

from __future__ import annotations

import html
import re

import pandas as pd

_MD = re.compile(r"([\\`*_\[\]<>#|~$])")
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
_NUMBERISH = re.compile(r"^[+-]?[\d.,]+%?$")  # "+12%" or "-3.5" is a number to a spreadsheet, not a formula


def md(text: object) -> str:
    """Show a name as plain text inside markdown: no links, images, HTML, maths or formatting can come from data."""
    return _MD.sub(r"\\\1", str(text))


def chart_text(text: object) -> str:
    """A name shown in a Plotly chart: Plotly reads <a href>, <b> and the like in its text as markup, and shows
    `&lt;` as a plain `<`, so escaping keeps a name from a file as the text it is."""
    return html.escape(str(text), quote=False)


def _cell(v: object) -> object:
    if isinstance(v, str) and v.startswith(_FORMULA_START) and not _NUMBERISH.match(v):
        return "'" + v  # a spreadsheet would otherwise run it as a formula
    return v


def csv_bytes(df: pd.DataFrame) -> bytes:
    """CSV for download, with text that a spreadsheet would read as a formula made inert."""
    safe = df.copy()
    for col in safe.columns:
        if safe[col].dtype == object or pd.api.types.is_string_dtype(safe[col]):
            safe[col] = safe[col].map(_cell)
    safe.columns = [_cell(str(c)) for c in safe.columns]
    return safe.to_csv(index=False).encode("utf-8")


def file_part(text: object, limit: int = 40) -> str:
    """A safe fragment for a download's file name."""
    return re.sub(r"[^A-Za-z0-9]+", "_", str(text)).strip("_").lower()[:limit] or "x"
