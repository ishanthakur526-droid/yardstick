"""Names from a data file are text, never markup: every screen on a project whose names carry links, images and HTML.

Streamlit renders Markdown in far more places than `st.markdown`: alerts, captions, widget labels, help tooltips,
metric values, radio and segmented-control options, expander and tab labels. A name that reached any of them
unescaped could show a remote image (a tracking pixel) or a link the project never had. The screens are run on such a
project and every string that Streamlit will render as Markdown is collected and checked.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from core import ingest, synth
from core.dataset import from_tables

APP = str(Path(__file__).resolve().parent.parent / "app.py")
SECTIONS = ["Home", "See it", "Plan it", "Requisition", "Match the sets", "About the data", "Your data"]
BAD = "![i](https://evil.example/i.png)[l](https://evil.example)<img src=x><a href=https://evil.example>a</a>"
LIVE = re.compile(r"(?<!\\)\]\(|(?<!\\)<(?:img|a|b|script|iframe)\b", re.I)  # an unescaped link, image or tag
TAG = re.compile(r"<(?:img|a|script|iframe)[ >]", re.I)  # Plotly reads <a href> (and <b>, <br>) in chart text
# Shown as plain text by Streamlit (dataframes, choice lists, typed values), so not Markdown
PLAIN = {"ArrowNamedDataSet", "Arrow", "VegaLiteChart", "ArrowTable"}
PLAIN_FIELDS = {("Selectbox", "options"), ("MultiSelect", "options"), ("TextInput", "value"), ("TextInput", "default")}


def hostile_raw() -> dict[str, pd.DataFrame]:
    """The sample project with every code, name, unit, godown and project field carrying links, an image and a tag."""
    raw = {name: df.copy() for name, df in synth.make_pack().items()}
    code_cols = ("item_code", "parent_code", "child_code")
    name_cols = ("item_name", "parent_name", "child_name")
    unit_cols = ("unit", "parent_unit", "child_unit")
    for df in raw.values():
        for col in df.columns:
            if col in code_cols:
                df[col] = df[col].astype(str) + "[c](https://evil.example)"
            elif col in name_cols or col in unit_cols or col == "godown":
                df[col] = df[col].where(df[col].isna(), df[col].astype(str) + " " + BAD)
    meta = raw["meta"]
    for key in ("project", "company", "source", "about"):
        meta.loc[meta["key"] == key, "value"] = meta.loc[meta["key"] == key, "value"] + " " + BAD
    meta.loc[meta["key"] == "anchor_code", "value"] += "[c](https://evil.example)"
    meta.loc[meta["key"] == "closing_stores", "value"] += " " + BAD
    return raw


@pytest.fixture(scope="module")
def hostile_ds():
    return from_tables(ingest.monthly_tables(hostile_raw()), key="custom")


def markdown_strings(at: AppTest) -> list[tuple[str, str, str]]:
    """(proto type, field, text) for every string Streamlit will render as Markdown on this run."""
    found: list[tuple[str, str, str]] = []

    def walk_proto(msg, where: str) -> None:
        kind = type(msg).__name__
        if kind in PLAIN:
            return
        if kind == "PlotlyChart":  # chart text is not Markdown, but Plotly has markup of its own
            found.extend(("PlotlyChart", "text", text) for text in _strings(json.loads(msg.spec)))
            return
        for field, value in msg.ListFields():
            if (kind, field.name) in PLAIN_FIELDS:
                continue
            many = list(value) if field.is_repeated else [value]
            if field.type == field.TYPE_STRING:
                found.extend((kind, field.name, text) for text in many)
            elif field.type == field.TYPE_MESSAGE:
                for sub in many:
                    walk_proto(sub, where)

    def walk(node) -> None:
        proto = getattr(node, "proto", None)
        if proto is not None:
            walk_proto(proto, type(node).__name__)
        for child in getattr(node, "children", {}).values():
            walk(child)

    walk(at._tree)
    return found


def _strings(obj) -> list[str]:
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in _strings(v)]
    if isinstance(obj, list):
        return [s for v in obj for s in _strings(v)]
    return []


def live(strings: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    return [
        (kind, field, text[:160])
        for kind, field, text in strings
        if (TAG if kind == "PlotlyChart" else LIVE).search(text)
    ]


def test_the_hostile_project_really_carries_markup(hostile_ds):
    assert BAD in hostile_ds.cfg.project and BAD in hostile_ds.ledger.item_name(hostile_ds.ledger.items["item_code"][0])


@pytest.mark.parametrize("section", SECTIONS)
def test_no_name_from_the_data_renders_as_markup(section, hostile_ds):
    at = AppTest.from_file(APP, default_timeout=300)
    at.session_state["custom_ds"] = hostile_ds
    at.session_state["dataset"] = "custom"
    at.session_state["section"] = section
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    strings = markdown_strings(at)
    assert any("evil" in text for _, _, text in strings), "the names should be on screen (escaped)"
    assert not live(strings), live(strings)


def test_an_upload_with_markup_in_its_names_shows_it_as_text():
    """The Your data screen: file names, sheet names, column headers and every value the check quotes."""
    from core import upload

    sheets = upload.read_upload(BAD + ".xlsx", upload.template_bytes(hostile_raw()))
    sheets += upload.read_upload(BAD + ".csv", f"item_code,{BAD},qty\nX1,{BAD},1\n".encode())
    at = AppTest.from_file(APP, default_timeout=300)
    at.session_state["section"] = "Your data"
    at.session_state["data_sheets"] = {"sig": (), "sheets": sheets}
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    strings = markdown_strings(at)
    assert sum("evil" in text for _, _, text in strings) > 5, "the names should be on screen (escaped)"
    assert not live(strings), live(strings)
