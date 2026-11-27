"""Your data: run Yardstick on a project's own stores records.

Four steps on one screen: get the format, upload, say what each file holds and match its columns, then check and use
it. What is built lives in this visitor's session only (`st.session_state["custom_ds"]`): nothing is written to disk or
to a cache other visitors share. Every name that came from a file goes through `md()` before it reaches markdown.
"""

from __future__ import annotations

import hashlib
import logging

import streamlit as st

from core import synth, upload
from core.dataset import Dataset
from core.fmt import month_label
from core.safe import md

from . import state
from .common import XLSX

log = logging.getLogger("yardstick")
NOT_USED = "not used"
RESULT = {upload.PASS: "✅", upload.WARN: "⚠️", upload.FAIL: "❌"}


@st.cache_data(show_spinner=False)
def _blank() -> bytes:
    return upload.template_bytes()


@st.cache_data(show_spinner=False)
def _sample_book() -> bytes:
    return upload.template_bytes(synth.make_pack())


def render(ds: Dataset) -> None:
    st.subheader("Run it on your own project")
    st.markdown(
        "Upload a project's stores records: what came in from vendors, what went out to contractors, what came back "
        "and what the site used. Yardstick replays them the same way as the sample. **Nothing is stored:** the records "
        "stay in this browser session and are gone when you close the tab."
    )
    _loaded()
    _formats()
    st.markdown("**2. Upload**")
    files = st.file_uploader(
        "One Excel workbook (.xlsx), or the CSV files",
        type=["xlsx", "csv"],
        accept_multiple_files=True,
        key="data_upload",
        help=f"Up to {upload.MAX_FILE_MB} MB a file. For a big export, CSV is much quicker to read than Excel.",
    )
    sheets = _read(files)
    if not sheets:
        return
    parts = _recognise(sheets)
    if not parts:
        st.info("No file is in use yet: say what each one holds.")
        return
    _check(parts)


def _loaded() -> None:
    custom = st.session_state.get("custom_ds")
    if not isinstance(custom, Dataset):
        return
    months = custom.ledger.months
    st.success(
        f"In use: **{md(custom.cfg.project)}**, {len(custom.ledger.items)} items, {month_label(months[0])} to "
        f"{month_label(months[-1])}. Switch between it and the other projects under *Project data* in the sidebar."
    )
    st.button("Remove my data", on_click=_remove, key="data_remove")


def _remove() -> None:
    st.session_state.pop("custom_ds", None)
    st.session_state.pop("data_report", None)
    if st.session_state.get("dataset") == state.CUSTOM:
        st.session_state.pop("dataset", None)  # the sidebar falls back to the first project


def _formats() -> None:
    st.markdown(
        "**1. Get the format.** One Excel workbook with a sheet per table, or one CSV file per table named after it "
        "(items.csv, receipts.csv, contractor_moves.csv, …). Three tables are needed; the others make more of the "
        "screens work. Column names from your own system are fine: they are matched in step 3."
    )
    a, b = st.columns(2)
    a.download_button(
        "Blank template (Excel)",
        _blank(),
        file_name="yardstick_template.xlsx",
        mime=XLSX,
        key="data_template",
        help="A sheet per table with its column headers (the needed ones highlighted), and a sheet on how to fill it.",
    )
    b.download_button(
        "The sample project in this format (Excel)",
        _sample_book,
        file_name="yardstick_sample_project.xlsx",
        mime=XLSX,
        key="data_sample",
        help="A filled-in example. Upload it back to see every step.",
    )
    with st.expander("What each table holds"):
        st.dataframe(upload.guide(), hide_index=True)


def _read(files) -> list[upload.Sheet]:
    """The uploaded files as sheets, read once per upload and kept for this session only."""
    sig = tuple((f.file_id, f.name, f.size) for f in files or [])
    memo = st.session_state.get("data_sheets")
    if memo is None or memo["sig"] != sig:
        sheets: list[upload.Sheet] = []
        for f in files or []:
            with st.spinner(f"Reading {md(f.name)}…"):
                sheets += upload.read_upload(f.name, f.getvalue())
        memo = {"sig": sig, "sheets": sheets}
        st.session_state["data_sheets"] = memo
        st.session_state.pop("data_report", None)
    return memo["sheets"]


def _id(i: int, sheet: upload.Sheet) -> str:
    return hashlib.sha1(f"{i}|{sheet.source}".encode()).hexdigest()[:10]


def _table_label(name: str) -> str:
    return "Not used" if name == NOT_USED else upload.BY_NAME[name].label


def _recognise(sheets: list[upload.Sheet]) -> list[upload.Part]:
    st.markdown(
        "**3. Say what each file holds, and match its columns.** Both are suggested from the names; change any that "
        "are wrong."
    )
    options = [t.name for t in upload.TABLES] + [NOT_USED]
    parts = []
    for i, sheet in enumerate(sheets):
        if sheet.problem:
            st.error(f"{md(sheet.source)} cannot be used: {sheet.problem}.")
            continue
        headers = list(sheet.frame.columns)
        guess = upload.guess_table(sheet.name, headers) or NOT_USED
        table = st.selectbox(
            f"{md(sheet.source)} · {len(sheet.frame):,} rows",
            options,
            index=options.index(guess),
            format_func=_table_label,
            key=f"data_table_{_id(i, sheet)}",
        )
        if sheet.header_row > 1:
            st.caption(f"Its header is on line {sheet.header_row}; the lines above it are skipped.")
        if table == NOT_USED:
            continue
        t = upload.BY_NAME[table]
        suggested = upload.suggest_mapping(t, headers)
        missing = [c for c in t.needed if suggested[c] is None]
        title = "Match the columns" + (
            f": {len(missing)} needed column{'s' if len(missing) > 1 else ''} not found" if missing else ""
        )
        mapping = {}
        with st.expander(title, expanded=bool(missing)):
            for c in t.columns:
                choices = [None, *headers]
                mapping[c.name] = st.selectbox(
                    c.name + (" (needed)" if c.required else ""),
                    choices,
                    index=choices.index(suggested[c.name]),
                    format_func=lambda h: "(none)" if h is None else str(h),
                    key=f"data_col_{_id(i, sheet)}_{table}_{c.name}",
                    help=c.meaning,
                )
        parts.append(upload.Part(table, sheet, mapping))
    return parts


def _check(parts: list[upload.Part]) -> None:
    st.markdown("**4. Check, then use it**")
    sig = repr(
        (
            st.session_state["data_sheets"]["sig"],
            [(p.table, p.sheet.source, sorted(p.mapping.items(), key=lambda kv: kv[0])) for p in parts],
        )
    )
    memo = st.session_state.get("data_report")
    if memo is None or memo["sig"] != sig:
        with st.spinner("Checking every row…"):
            memo = {"sig": sig, "report": upload.check(parts)}
        st.session_state["data_report"] = memo
    report: upload.Report = memo["report"]
    if report.verdict == upload.FAIL:
        st.error(
            "Not ready yet: fix the lines marked Fail, by changing a choice above or by fixing the file and uploading "
            "it again."
        )
    elif report.verdict == upload.WARN:
        st.warning(
            "Ready, with warnings: some rows are left out or read a particular way. Check that is what you expect."
        )
    else:
        st.success("Ready: everything was read.")
    lines = report.frame()  # failures first, then warnings, then what passed; wrapped so it reads on a phone
    st.markdown(
        "\n".join(f"- {RESULT[r.Result]} **{md(r.Table)}**: {md(r.text)}" for r in lines.itertuples(index=False))
    )
    if not report.ok:
        return
    st.text_input("Project name", value=_default_name(report), max_chars=80, key="data_project")
    st.button("Use this data", type="primary", on_click=_use, key="data_use")
    problem = st.session_state.pop("data_error", None)
    if problem:
        st.error(problem)


def _default_name(report: upload.Report) -> str:
    meta = report.tables.get("meta")
    if meta is not None:
        named = meta.loc[meta["key"].astype(str).str.strip().str.lower() == "project", "value"].dropna()
        if len(named):
            return str(named.iloc[0])[:80]
    return "Your project"


def _use() -> None:
    memo = st.session_state.get("data_report")
    if not memo or not memo["report"].ok:
        return
    try:
        built = upload.build(memo["report"].tables, str(st.session_state.get("data_project", "")))
    except Exception:  # noqa: BLE001 - records from a visitor: say so, never take the page down
        log.exception("building an uploaded project failed")
        st.session_state["data_error"] = (
            "The project could not be built from these records, although each table passed its checks. Check the "
            "dates and quantities, or start from the template."
        )
        return
    st.session_state["custom_ds"] = built
    # The files as text and the checked tables are not needed once the project is built, and they are big: Lumino's
    # 231,000-row export holds ~150 MB in them against ~1 MB for the built project. Opening Home also clears the
    # uploader, so nothing re-reads the files.
    st.session_state.pop("data_sheets", None)
    st.session_state.pop("data_report", None)
    st.session_state["dataset"] = state.CUSTOM
    st.session_state["section"] = "Home"
