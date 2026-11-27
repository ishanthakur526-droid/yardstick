"""Bring a project's own stores records into Yardstick.

  template_bytes   the upload format as an Excel workbook: blank, or filled in (the sample project)
  read_upload      one uploaded file as sheets of text, read safely: size, row and unpacked-size limits, Excel read
                   read-only, and the header line found even when an export puts a title above it
  guess_table      which table a file or sheet holds, from its name and its columns
  suggest_mapping  which of its columns is which: exact names first, then common alternatives and close spellings
  check            the mapped tables, with a Pass / Warn / Fail line for everything that was read, left out or doubtful
  build            the project, from tables that passed

Nothing here keeps the data: the app holds what is built in the visitor's own session only.
"""

from __future__ import annotations

import csv
import difflib
import io
import logging
import re
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, replace

import openpyxl
import pandas as pd
from openpyxl.cell.cell import ERROR_CODES

from . import ingest
from .config import META_KEYS
from .dataset import Dataset, from_tables
from .export import XLSX_OPTIONS
from .fmt import month_label
from .ledger import MAX_MONTHS, check_size

log = logging.getLogger("yardstick")

MAX_FILE_MB = 50  # keep in step with server.maxUploadSize in .streamlit/config.toml
MAX_ROWS = 500_000  # per file or sheet (the largest real export seen: 231,077 rows)
MAX_TOTAL_ROWS = 1_000_000  # across everything uploaded at once
MAX_UNPACKED_MB = 200  # an .xlsx is a zip: refuse one that would unpack to more than this
MAX_SHEETS = 20
MAX_COLUMNS = 100  # a stores export has a few dozen at most; each row is cut to this as it is read
MAX_HEADER_CHARS = 100  # a column name longer than this is cut (no real header is; matching cost grows with length)
MAX_CELLS = 4_000_000  # rows x columns across one upload: ~2.5x Lumino's real export (231,077 rows x 7 columns)
MAX_FILES = 12  # files read from one upload (a project has 7 tables)
MAX_ZIP_RATIO = 100  # a real .xlsx part unpacks to ~7-8x its packed size; a bomb to thousands
MAX_SHARED_TEXT_MB = 40  # the workbook's table of text cells, parsed whole when it is opened
HEADER_SCAN = 20  # how many lines from the top may hold the header line
MATCH = 0.8  # how close a column name must be to a known name to be suggested
MIN_MONTHS = 7  # the replay learns from at least 3 months and scores at least 4 (see ui/state.planner_limits)
SHORT_MONTHS = 13
EXAMPLES = 3
GUIDE_SHEET = "How to fill"  # the template's own instructions: skipped when the workbook comes back
PASS, WARN, FAIL = "Pass", "Warn", "Fail"


# The format ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Col:
    name: str
    required: bool
    kind: str  # "text", "number" or "date"
    meaning: str
    example: str
    also: tuple[str, ...] = ()  # other names the column often has in an ERP export


@dataclass(frozen=True)
class Table:
    name: str
    label: str
    required: bool
    meaning: str
    columns: tuple[Col, ...]
    also: tuple[str, ...] = ()  # other names a file or sheet holding it often has

    @property
    def needed(self) -> list[str]:
        return [c.name for c in self.columns if c.required]


CODE = Col(
    "item_code",
    True,
    "text",
    "Your code for the material",
    "MPO010014",
    (
        "code",
        "item code",
        "material code",
        "material",
        "item no",
        "item number",
        "material no",
        "material number",
        "part no",
        "part number",
        "sku",
        "item id",
    ),
)
DATE = Col(
    "date",
    True,
    "date",
    "Date of the entry: 2024-01-31 or 31-01-2024",
    "2024-01-31",
    (
        "posting date",
        "document date",
        "doc date",
        "txn date",
        "transaction date",
        "entry date",
        "voucher date",
        "trans date",
    ),
)
GODOWN = Col(
    "godown",
    True,
    "text",
    "The store or godown",
    "NORTH STORE",
    (
        "store",
        "store name",
        "godown name",
        "warehouse",
        "location",
        "depot",
        "plant",
        "sub store",
        "store location",
        "storage location",
    ),
)
TABLES: tuple[Table, ...] = (
    Table(
        "items",
        "Items",
        True,
        "One row per material: its code, name, unit and lead time.",
        (
            CODE,
            Col(
                "item_name",
                False,
                "text",
                "Its description",
                "PCC POLE 8.5 MTR",
                (
                    "name",
                    "description",
                    "item name",
                    "item description",
                    "material description",
                    "material name",
                    "desc",
                ),
            ),
            Col(
                "unit",
                False,
                "text",
                "Unit of measure",
                "NOS",
                ("uom", "unit of measure", "units", "base unit", "base uom"),
            ),
            Col(
                "lead_time_days",
                True,
                "number",
                "Days from placing an order to it arriving",
                "90",
                (
                    "lead time",
                    "lead days",
                    "lead time days",
                    "lead time in days",
                    "procurement lead time",
                    "delivery days",
                    "lt days",
                ),
            ),
            Col(
                "role",
                False,
                "text",
                "Write anchor against the one item a BOQ counts in (on a line project, the pole); leave the rest blank",
                "anchor",
                ("item role",),
            ),
            Col(
                "decimals",
                False,
                "number",
                "Decimal places it is ordered in; 0 means whole units (worked out from the unit when blank)",
                "0",
                ("decimal places", "precision"),
            ),
        ),
        ("item master", "items master", "material master", "materials", "item list"),
    ),
    Table(
        "receipts",
        "Goods receipts",
        True,
        "What came in from vendors: one row per receipt line.",
        (
            replace(DATE, also=DATE.also + ("grn date", "receipt date", "received date", "mrn date")),
            CODE,
            GODOWN,
            Col(
                "qty_received",
                True,
                "number",
                "Quantity received",
                "120",
                (
                    "qty",
                    "quantity",
                    "received qty",
                    "receipt qty",
                    "grn qty",
                    "accepted qty",
                    "qty recd",
                    "qty received",
                    "received quantity",
                    "rcvd qty",
                ),
            ),
            Col(
                "grn_no",
                False,
                "text",
                "Goods receipt number",
                "GRN/2401/00001",
                ("grn", "grn number", "receipt no", "receipt number", "mrn no", "document no", "doc no"),
            ),
        ),
        ("grn", "grns", "goods receipt", "goods receipts", "receipt", "inward", "purchases", "material receipts"),
    ),
    Table(
        "contractor_moves",
        "Contractor issues, returns and use",
        True,
        "Material issued to contractors (ISSUE), handed back (RETURN) and used on site (CONSUMPTION). "
        "CONSUMPTION rows need no godown.",
        (
            DATE,
            Col(
                "type",
                True,
                "text",
                "ISSUE, RETURN or CONSUMPTION",
                "ISSUE",
                (
                    "movement type",
                    "transaction type",
                    "txn type",
                    "move type",
                    "entry type",
                    "issue type",
                    "voucher type",
                ),
            ),
            CODE,
            replace(GODOWN, meaning="The store it left or came back to; blank for CONSUMPTION"),
            Col("qty", True, "number", "Quantity", "40", ("quantity", "issued qty", "qty issued", "issue qty")),
        ),
        (
            "contractor moves",
            "issues",
            "issue",
            "site issues",
            "contractor issues",
            "consumption",
            "material issues",
            "movements",
            "issues and returns",
        ),
    ),
    Table(
        "sets",
        "Sets (parts per set)",
        False,
        "How many of each part one parent needs, for example 4 clamps per pole.",
        (
            Col(
                "parent_code",
                True,
                "text",
                "The parent item's code",
                "MPO010014",
                ("parent", "parent item", "parent item code", "main item", "assembly"),
            ),
            Col(
                "child_code",
                True,
                "text",
                "The part's code",
                "MLU020013",
                ("child", "child item", "child item code", "component", "component code", "part", "part code"),
            ),
            Col(
                "ratio_of_parent",
                True,
                "number",
                "How many of the part one parent needs",
                "4",
                (
                    "ratio",
                    "qty per set",
                    "quantity per set",
                    "qty per parent",
                    "per set",
                    "bom qty",
                    "child qty",
                    "usage per parent",
                ),
            ),
            Col(
                "set_no",
                False,
                "text",
                "Set number, when one parent has several sets",
                "1",
                ("set", "set number", "kit no"),
            ),
        ),
        ("bom", "bill of materials", "kits", "kit", "set", "set ratios"),
    ),
    Table(
        "transfers",
        "Transfers between godowns",
        False,
        "Stock moved from one godown to another: an OUT row where it left and an IN row where it arrived.",
        (
            DATE,
            Col(
                "direction",
                True,
                "text",
                "IN or OUT",
                "OUT",
                ("in out", "in or out", "transfer type", "way", "inward outward"),
            ),
            CODE,
            GODOWN,
            Col("qty", True, "number", "Quantity moved", "30", ("quantity", "transfer qty", "qty moved")),
            Col(
                "voucher_no",
                False,
                "text",
                "Voucher number",
                "TO/2401/00001",
                ("voucher", "voucher number", "document no", "doc no", "challan no"),
            ),
        ),
        ("transfer", "stock transfer", "stock transfers", "store transfers", "inter store transfers"),
    ),
    Table(
        "boq",
        "Bill of quantities (BOQ)",
        False,
        "What the tender says each item needs in total. It caps total buying in the replay.",
        (
            CODE,
            Col(
                "qty",
                True,
                "number",
                "Total quantity in the BOQ",
                "1700",
                ("boq qty", "quantity", "tender qty", "required qty", "estimated qty", "total qty"),
            ),
        ),
        ("bill of quantities", "tender", "estimate"),
    ),
    Table(
        "meta",
        "Project details",
        False,
        "Optional, two columns: key and value. Keys: project, company, anchor_code, anchor_noun, closing_stores "
        "(separated by ;), requisition_example.",
        (
            Col("key", True, "text", "Which detail", "project", ("setting", "field", "detail")),
            Col("value", True, "text", "Its value", "Riverbend 33 kV lines", ("setting value",)),
        ),
        ("project", "settings", "details", "project details"),
    ),
)
BY_NAME = {t.name: t for t in TABLES}


def guide() -> pd.DataFrame:
    """The format, one row per table and column: what it holds, whether it is needed, an example."""
    rows = []
    for t in TABLES:
        rows.append((t.label, t.name, "(the whole table)", "Yes" if t.required else "No", t.meaning, ""))
        for c in t.columns:
            rows.append((t.label, t.name, c.name, "Yes" if c.required else "No", c.meaning, c.example))
    return pd.DataFrame(rows, columns=["Table", "Sheet or file", "Column", "Needed", "What it holds", "Example"])


def template_bytes(filled: dict[str, pd.DataFrame] | None = None) -> bytes:
    """The upload format as an Excel workbook: a 'How to fill' sheet, then one sheet per table with its column headers
    (needed ones highlighted). `filled` puts rows in (the sample project); columns not in the format are left out."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter", engine_kwargs=XLSX_OPTIONS) as xl:
        book = xl.book
        needed = book.add_format({"bold": True, "bg_color": "#FDE68A", "border": 1})
        guide().to_excel(xl, sheet_name=GUIDE_SHEET, index=False)
        ws = xl.sheets[GUIDE_SHEET]
        for first, last, width in ((0, 1, 20), (2, 3, 16), (4, 4, 90), (5, 5, 22)):
            ws.set_column(first, last, width)
        for t in TABLES:
            names = [c.name for c in t.columns]
            df = (filled or {}).get(t.name)
            df = pd.DataFrame(columns=names) if df is None else df[[n for n in names if n in df.columns]]
            df.to_excel(xl, sheet_name=t.name, index=False)
            ws = xl.sheets[t.name]
            ws.freeze_panes(1, 0)
            for j, name in enumerate(df.columns):
                ws.set_column(j, j, max(14, len(name) + 4))
                if t.columns[names.index(name)].required:
                    ws.write(0, j, name, needed)
    return buf.getvalue()


# Reading a file -----------------------------------------------------------------------------------------------------


@dataclass
class Sheet:
    source: str  # the file, and for a workbook the sheet: "pack.xlsx › receipts"
    name: str  # what the file or sheet is called (used to recognise the table)
    frame: pd.DataFrame  # every cell as text, with the header applied
    header_row: int = 1  # the line the header was found on (1: the first)
    problem: str = ""  # why it could not be read (the frame is empty then)


def _norm(text: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


KNOWN = {_norm(n) for t in TABLES for c in t.columns for n in (c.name, *c.also)}
CLOSE_MAX = 2 * max(len(n) for n in KNOWN)  # close spelling is tried only on names up to this long


def _header_at(rows: list[list[str]]) -> int:
    """The header line: of the first lines, the one with the most cells named like a known column (an export often puts
    a report title and a date range above its header). The first line when no line has at least two."""
    best, at = 0, 0
    for i, row in enumerate(rows[:HEADER_SCAN]):
        hits = len({_norm(str(c)[:MAX_HEADER_CHARS]) for c in row} & KNOWN)
        if hits > best:
            best, at = hits, i
    if best < 2:  # no exact names: try close spellings
        for i, row in enumerate(rows[:HEADER_SCAN]):
            names = [_norm(str(c)[:MAX_HEADER_CHARS]) for c in row]
            hits = sum(1 for n in names if 2 < len(n) <= CLOSE_MAX and difflib.get_close_matches(n, KNOWN, 1, MATCH))
            if hits > best:
                best, at = hits, i
    return at if best >= 2 else 0


def _unique(names: list[str]) -> list[str]:
    out, seen = [], {}
    for j, n in enumerate(names):
        n = n.strip() or f"column {j + 1}"
        seen[n] = seen.get(n, 0) + 1
        out.append(n if seen[n] == 1 else f"{n} ({seen[n]})")
    return out


def _tidy(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.astype(object)
    for col in frame.columns:
        s = frame[col]
        frame[col] = s.where(s.isna(), s.astype(str).str.strip())
    frame = frame.replace({"": None})
    return frame.dropna(how="all").reset_index(drop=True)


def _sheet(source: str, name: str, rows: list[list[str]], budget: int = MAX_CELLS) -> Sheet:
    """Rows of text (each already cut to `MAX_COLUMNS`) as a sheet: find the header line, pad the rows to it."""
    if not any(any(str(c).strip() for c in r) for r in rows):
        return Sheet(source, name, pd.DataFrame(), problem="it is empty")
    h = _header_at(rows)
    header = _unique([str(c)[:MAX_HEADER_CHARS] for c in rows[h]])
    width = len(header)
    if (len(rows) - h - 1) * width > budget:  # padding every row to the header would pass the budget
        return Sheet(source, name, pd.DataFrame(), problem=_too_many_cells())
    body = [(list(r) + [None] * width)[:width] for r in rows[h + 1 :]]
    return Sheet(source, name, _tidy(pd.DataFrame(body, columns=header, dtype=object)), header_row=h + 1)


def _too_many_cells() -> str:
    return (
        f"together with the other files it holds more than {MAX_CELLS:,} cells, more than the free public app reads "
        "at once: upload fewer files, or leave out columns Yardstick does not use"
    )


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def _delimiter(text: str) -> str:
    head = text[:65_536].splitlines()[:HEADER_SCAN]  # only the top: splitting a whole 50 MB file would cost GBs
    counts = {d: sum(line.count(d) for line in head) for d in (",", ";", "\t", "|")}
    best = max(counts, key=counts.get)
    return best if counts[best] else ","


def _read_csv(source: str, name: str, data: bytes, budget: int) -> Sheet:
    text = _decode(data)
    rows: list[list[str]] = []
    cells = 0
    for row in csv.reader(io.StringIO(text), delimiter=_delimiter(text)):
        row = row[:MAX_COLUMNS]
        while row and not row[-1].strip():
            row.pop()
        rows.append(row)
        cells += len(row)
        if len(rows) > MAX_ROWS + HEADER_SCAN:
            return Sheet(source, name, pd.DataFrame(), problem=f"it has more than {MAX_ROWS:,} rows")
        if cells > budget:
            return Sheet(source, name, pd.DataFrame(), problem=_too_many_cells())
    return _sheet(source, name, rows, budget)


def _cell_text(value: object) -> str:
    """An Excel cell as the text pandas would have read (whole numbers without '.0'; errors such as #N/A as blank)."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, str) and value in ERROR_CODES:
        return ""
    return str(value)


def _read_xlsx(source: str, data: bytes, budget: int) -> list[Sheet]:
    stem = source.rsplit(".", 1)[0]

    def bad(why: str) -> list[Sheet]:
        return [Sheet(source, stem, pd.DataFrame(), problem=why)]

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            parts = z.infolist()
    except zipfile.BadZipFile:
        return bad("it is not a valid .xlsx workbook")
    if len(parts) > 5000 or sum(p.file_size for p in parts) > MAX_UNPACKED_MB * 2**20:
        return bad(f"it would unpack to more than {MAX_UNPACKED_MB} MB")
    if any(p.file_size > 2**20 and p.file_size > MAX_ZIP_RATIO * max(p.compress_size, 1) for p in parts):
        return bad(f"a part of it unpacks to more than {MAX_ZIP_RATIO} times its size, which no real workbook does")
    if any(
        p.filename.lower().endswith("sharedstrings.xml") and p.file_size > MAX_SHARED_TEXT_MB * 2**20 for p in parts
    ):
        return bad(f"it holds more than {MAX_SHARED_TEXT_MB} MB of text: save the sheets as CSV files instead")
    # Read-only and values only: openpyxl streams the sheets, parses the XML with defusedxml (installed), and each row
    # is cut to MAX_COLUMNS while it is read (pandas would pad every row to the widest cell in the sheet).
    book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    out: list[Sheet] = []
    try:
        for i, ws in enumerate(book.worksheets):
            title = str(ws.title)
            src = f"{source} › {title}"
            if i >= MAX_SHEETS:
                out.append(Sheet(src, title, pd.DataFrame(), problem=f"only the first {MAX_SHEETS} sheets are read"))
                break
            if title == GUIDE_SHEET:
                continue
            rows: list[list[str]] = []
            cells, why = 0, ""
            for values in ws.iter_rows(values_only=True, max_col=MAX_COLUMNS):
                row = [_cell_text(v) for v in values]
                while row and not row[-1].strip():
                    row.pop()
                rows.append(row)
                cells += len(row)
                if len(rows) > MAX_ROWS + HEADER_SCAN:
                    why = f"it has more than {MAX_ROWS:,} rows"
                    break
                if cells > budget:
                    why = _too_many_cells()
                    break
            if why:
                out.append(Sheet(src, title, pd.DataFrame(), problem=why))
                continue
            if not any(rows):  # an empty sheet holds nothing to read
                continue
            sheet = _sheet(src, title, rows, budget)
            budget -= len(sheet.frame) * len(sheet.frame.columns)
            out.append(sheet)
    finally:
        book.close()
    return out or bad("it has no rows")


def cells(sheets: Iterable[Sheet]) -> int:
    """How many cells the sheets hold (what the upload's cell budget counts)."""
    return sum(len(s.frame) * len(s.frame.columns) for s in sheets)


def read_upload(name: str, data: bytes, budget: int = MAX_CELLS) -> list[Sheet]:
    """One uploaded file as sheets of text (a CSV is one sheet). It never raises: a file that cannot be read comes back
    as one sheet with `problem` set, in words the visitor can act on. `budget` is how many cells the upload may still
    hold (`MAX_CELLS` less what the other files of the same upload hold)."""
    stem, _, ext = name.rpartition(".")
    stem, ext = (stem or name), ext.lower()

    def bad(why: str) -> list[Sheet]:
        return [Sheet(name, stem, pd.DataFrame(), problem=why)]

    if len(data) > MAX_FILE_MB * 2**20:
        return bad(f"it is bigger than {MAX_FILE_MB} MB")
    if ext not in ("csv", "txt", "xlsx"):
        return bad("only .xlsx and .csv files can be read: save it as one of those first")
    if budget <= 0:
        return bad(_too_many_cells())
    try:
        return _read_xlsx(name, data, budget) if ext == "xlsx" else [_read_csv(name, stem, data, budget)]
    except Exception:  # noqa: BLE001 - a file from a visitor: say it could not be read, never take the page down
        log.exception("could not read an uploaded %s file", ext)
        return bad(f"it could not be read as {'an Excel workbook' if ext == 'xlsx' else 'a CSV file'}")


# Recognising and mapping --------------------------------------------------------------------------------------------


def _score(text: str, names: Iterable[str]) -> float:
    """1 for the exact name, 0.95 for an exact alternative, else how close the spelling is (0 to 1)."""
    h = _norm(str(text)[:MAX_HEADER_CHARS])
    if not h:
        return 0.0
    close = len(h) <= CLOSE_MAX  # no known name is anywhere near this long: only an exact match could count
    best = 0.0
    for i, name in enumerate(names):
        n = _norm(name)
        if h == n:
            return 1.0 if i == 0 else 0.95
        if close:
            best = max(best, difflib.SequenceMatcher(None, h, n).ratio())
    return best


def suggest_mapping(table: Table, headers: list[str]) -> dict[str, str | None]:
    """For each of the table's columns, the uploaded column that matches it best, or None. Each uploaded column is
    used once, best matches first."""
    scored = sorted(
        (
            (_score(h, (c.name, *c.also)), i, j)
            for j, c in enumerate(table.columns)
            for i, h in enumerate(headers)
            if h is not None
        ),
        key=lambda x: (-x[0], x[1], x[2]),
    )
    out: dict[str, str | None] = {c.name: None for c in table.columns}
    used: set[int] = set()
    for score, i, j in scored:
        if score < MATCH:
            break
        name = table.columns[j].name
        if out[name] is None and i not in used:
            out[name] = headers[i]
            used.add(i)
    return out


def _name_score(name: str, table: Table) -> float:
    n = _norm(name)
    names = [_norm(x) for x in (table.name, *table.also)]
    if n in names:
        return 1.0
    if any(f" {x} " in f" {n} " for x in names):
        return 0.9  # "kanpur receipts 2024" holds "receipts"
    return max(difflib.SequenceMatcher(None, n, x).ratio() for x in names)


def _fit(table: Table, headers: list[str]) -> tuple[bool, int, int]:
    m = suggest_mapping(table, headers)
    found = sum(m[c] is not None for c in table.needed)
    extra = sum(m[c.name] is not None for c in table.columns if not c.required)
    return found == len(table.needed), found, extra


def guess_table(name: str, headers: list[str]) -> str | None:
    """Which table a file or sheet holds. Its name decides when it names a table ("receipts", "GRN", "Issues"); else the
    table whose needed columns are all found, the one with the most columns found winning."""
    named = [t for t in TABLES if _name_score(name, t) >= 0.9]
    if named:
        return max(named, key=lambda t: (_fit(t, headers), _name_score(name, t))).name
    complete = [t for t in TABLES if _fit(t, headers)[0]]
    return max(complete, key=lambda t: _fit(t, headers)).name if complete else None


def apply_mapping(table: Table, frame: pd.DataFrame, mapping: dict[str, str | None]) -> pd.DataFrame:
    """The sheet's columns renamed to the format's names (unmapped ones left out). Numbers lose thousands separators."""
    out = {}
    for col in table.columns:
        src = mapping.get(col.name)
        if src is None or src not in frame.columns:
            continue
        s = frame[src].astype(object)
        s = s.where(s.isna(), s.astype(str).str.strip())
        if col.kind == "number":
            s = s.where(s.isna(), s.astype(str).str.replace(",", "", regex=False))
        out[col.name] = s
    return pd.DataFrame(out).reset_index(drop=True)


def move_type(value: object) -> str | None:
    """ISSUE, RETURN or CONSUMPTION from the words an export uses ("Issued", "Return from contractor", "Used")."""
    hits = set()
    for word in re.sub(r"[^A-Z]+", " ", str(value).upper()).split():
        if word.startswith("ISS"):
            hits.add("ISSUE")
        elif word.startswith("RET"):
            hits.add("RETURN")
        elif word.startswith(("CONSUM", "USE", "UTIL")):
            hits.add("CONSUMPTION")
    return hits.pop() if len(hits) == 1 else None


IN_WORDS = {"IN", "INWARD", "INWARDS", "RECEIPT", "RECEIVED", "RECEIVE", "TI"}
OUT_WORDS = {"OUT", "OUTWARD", "OUTWARDS", "ISSUE", "ISSUED", "SENT", "DISPATCH", "DISPATCHED", "TO"}


def direction(value: object) -> str | None:
    """IN or OUT from the words an export uses ("Receipt from another store", "Transfer out")."""
    words = set(re.sub(r"[^A-Z]+", " ", str(value).upper()).split())
    hits = {"IN"} if words & IN_WORDS else set()
    hits |= {"OUT"} if words & OUT_WORDS else set()
    return hits.pop() if len(hits) == 1 else None


# Checking -----------------------------------------------------------------------------------------------------------


@dataclass
class Check:
    level: str  # PASS, WARN or FAIL
    table: str
    text: str


@dataclass
class Part:
    table: str  # which table the sheet holds
    sheet: Sheet
    mapping: dict[str, str | None]


@dataclass
class Report:
    checks: list[Check]
    tables: dict[str, pd.DataFrame]  # the mapped tables the checks ran on, ready for `build`

    @property
    def verdict(self) -> str:
        levels = {c.level for c in self.checks}
        return FAIL if FAIL in levels else WARN if WARN in levels else PASS

    @property
    def ok(self) -> bool:
        return self.verdict != FAIL

    def frame(self) -> pd.DataFrame:
        order = {FAIL: 0, WARN: 1, PASS: 2}
        rows = sorted(self.checks, key=lambda c: order[c.level])
        return pd.DataFrame([(c.level, c.table, c.text) for c in rows], columns=["Result", "Table", "text"])


def _n(count: int, noun: str) -> str:
    """A count with its noun: '1 row', '2,600 rows'."""
    return f"{count:,} {noun}" + ("" if count == 1 else "s")


def _eg(values: pd.Series) -> str:
    seen = [str(v) for v in pd.unique(values.dropna())[:EXAMPLES]]
    return ", ".join(f"'{v[:40]}'" for v in seen) if seen else "a blank"


def _normalise(tables: dict[str, pd.DataFrame]) -> list[Check]:
    """Movement types and transfer directions in the words an export uses, read as the format's values (in place)."""
    out = []
    for name, col, read, allowed in (
        ("contractor_moves", "type", move_type, "ISSUE, RETURN or CONSUMPTION"),
        ("transfers", "direction", direction, "IN or OUT"),
    ):
        df = tables.get(name)
        if df is None or col not in df:
            continue
        raw = df[col]
        lookup = {v: read(v) for v in pd.unique(raw.dropna())}
        mapped = raw.map(lookup)
        label = BY_NAME[name].label
        reread = sorted({(str(v), m) for v, m in lookup.items() if m and str(v).strip().upper() != m})
        if reread:
            shown = ", ".join(f"'{v[:30]}' as {m}" for v, m in reread[:6])
            out.append(Check(PASS, label, f"Read {shown}{' and more' if len(reread) > 6 else ''}."))
        unknown = raw.notna() & mapped.isna()
        if unknown.any():
            out.append(
                Check(
                    WARN,
                    label,
                    f"{_n(unknown.sum(), 'row')} with a {col} that is not {allowed} (for example {_eg(raw[unknown])}): "
                    "not counted.",
                )
            )
        df[col] = mapped.where(mapped.notna(), raw.astype(str).str.upper())
    return out


def _check_items(df: pd.DataFrame) -> tuple[list[Check], set[str]]:
    label = BY_NAME["items"].label
    if df.empty:
        return [Check(FAIL, label, "No rows.")], set()
    if df["item_code"].isna().all():
        return [Check(FAIL, label, "No item codes, so nothing can be matched to an item.")], set()
    out = []
    code = df["item_code"]
    if code.isna().any():
        out.append(Check(WARN, label, f"{_n(code.isna().sum(), 'row')} with no item code: left out."))
    dup = code.notna() & code.duplicated()
    if dup.any():
        out.append(
            Check(
                WARN,
                label,
                f"{_n(dup.sum(), 'item code')} listed more than once (for example {_eg(code[dup])}): the first row "
                "of each is used.",
            )
        )
    first = df[code.notna() & ~dup]
    lead = pd.to_numeric(first["lead_time_days"], errors="coerce")
    bad = lead.isna() | (lead < 0)
    if bad.any():
        out.append(
            Check(
                FAIL,
                label,
                f"{_n(bad.sum(), 'item')} with no usable lead time (for example {_eg(first.loc[bad, 'item_code'])}). "
                "The planner needs it to know when to order: fill lead_time_days in with days.",
            )
        )
    out.append(Check(PASS, label, f"{_n(len(first), 'item')}."))
    return out, set(first["item_code"].astype(str))


def _check_moves(name: str, df: pd.DataFrame, qty: str, known: set[str]) -> tuple[list[Check], pd.DataFrame]:
    """Rows that cannot be placed (no readable date or quantity, an unknown item, no godown where one is needed) are
    reported; the rest are returned with their month."""
    t = BY_NAME[name]
    if df.empty:
        return [Check(FAIL if t.required else WARN, t.label, "No rows.")], df.assign(month=pd.Series(dtype=str))
    out = []
    n = len(df)
    dates = ingest.parse_dates(df["date"])
    q = pd.to_numeric(df[qty], errors="coerce")
    code = df["item_code"]
    bad_date, bad_qty, no_code = dates.isna(), q.isna(), code.isna()
    unknown = ~no_code & ~code.astype(str).isin(known)
    if bad_date.all():
        out.append(
            Check(
                FAIL,
                t.label,
                f"None of the {n:,} dates could be read (for example {_eg(df['date'])}). Use dates like 2024-01-31 or "
                "31-01-2024.",
            )
        )
    elif bad_date.any():
        out.append(
            Check(
                WARN,
                t.label,
                f"{bad_date.sum():,} of {_n(n, 'row')} with a date that could not be read or is outside 1990 to 2100 "
                f"(for example "
                f"{_eg(df.loc[bad_date, 'date'])}): left out.",
            )
        )
    if bad_qty.any():
        out.append(
            Check(
                WARN,
                t.label,
                f"{_n(bad_qty.sum(), 'row')} with a quantity that is not a number (for example "
                f"{_eg(df.loc[bad_qty, qty])}): left out.",
            )
        )
    if (q < 0).any():
        out.append(
            Check(
                WARN,
                t.label,
                f"{_n((q < 0).sum(), 'row')} with a negative quantity: counted as entered, so each one takes stock "
                "off.",
            )
        )
    if no_code.any():
        out.append(Check(WARN, t.label, f"{_n(no_code.sum(), 'row')} with no item code: left out."))
    if unknown.any():
        out.append(
            Check(
                WARN,
                t.label,
                f"{_n(unknown.sum(), 'row')} with an item code not in the items table (for example "
                f"{_eg(code[unknown])}): left out.",
            )
        )
    keep = ~(bad_date | bad_qty | no_code | unknown)
    kind = df["type"].astype(str).str.upper() if "type" in df else None
    needs_godown = keep if kind is None else keep & kind.isin(["ISSUE", "RETURN"])
    no_godown = needs_godown & df["godown"].isna()
    if no_godown.any():
        what = "issue or return row" if name == "contractor_moves" else "row"
        out.append(Check(WARN, t.label, f"{_n(no_godown.sum(), what)} with no godown: left out."))
    rows = df[keep & ~no_godown].assign(month=dates[keep & ~no_godown].dt.strftime("%Y-%m"))
    if len(rows):
        zero = (q[keep & ~no_godown] == 0).sum()
        out.append(
            Check(
                PASS,
                t.label,
                f"{_n(len(rows), 'row')} from {month_label(rows['month'].min())} to {month_label(rows['month'].max())}"
                + (f" ({zero:,} of them with quantity 0, which add nothing)." if zero else "."),
            )
        )
    elif n:
        out.append(Check(FAIL if t.required else WARN, t.label, "No row could be used."))
    return out, rows


def _check_sets(df: pd.DataFrame, known: set[str]) -> list[Check]:
    label = BY_NAME["sets"].label
    ratio = pd.to_numeric(df["ratio_of_parent"], errors="coerce")
    parent, child = df["parent_code"].astype(str), df["child_code"].astype(str)
    bad = ~parent.isin(known) | ~child.isin(known) | ratio.isna() | (ratio <= 0) | (parent == child)
    out = []
    if bad.any():
        out.append(
            Check(
                WARN,
                label,
                f"{_n(bad.sum(), 'set line')} left out: an item not in the items table, a ratio that is not above "
                "zero, or a part listed as its own parent.",
            )
        )
    if (~bad).any():
        out.append(
            Check(PASS, label, f"{_n((~bad).sum(), 'set line')} for {_n(parent[~bad].nunique(), 'parent item')}.")
        )
    return out


def _check_boq(df: pd.DataFrame, known: set[str]) -> list[Check]:
    label = BY_NAME["boq"].label
    qty = pd.to_numeric(df["qty"], errors="coerce")
    bad = ~df["item_code"].astype(str).isin(known) | qty.isna() | (qty <= 0)
    out = []
    if bad.any():
        out.append(
            Check(
                WARN,
                label,
                f"{_n(bad.sum(), 'BOQ line')} left out: an item not in the items table or a quantity that is not "
                "above zero.",
            )
        )
    if (~bad).any():
        out.append(Check(PASS, label, f"A BOQ quantity for {_n(df.loc[~bad, 'item_code'].nunique(), 'item')}."))
    return out


def _check_meta(df: pd.DataFrame) -> list[Check]:
    label = BY_NAME["meta"].label
    keys = df["key"].dropna().astype(str).str.strip().str.lower()
    unknown = sorted(set(keys) - set(META_KEYS))
    out = []
    if unknown:
        shown = ", ".join(f"'{k[:30]}'" for k in unknown[:6])
        out.append(Check(WARN, label, f"Not used: {shown}. Known keys: {', '.join(META_KEYS)}."))
    known = [k for k in META_KEYS if k in set(keys)]
    if known:
        out.append(Check(PASS, label, f"Given: {', '.join(known)}."))
    return out


def _months(*month_columns: pd.Series) -> int:
    seen = pd.concat(month_columns).dropna()
    if seen.empty:
        return 0
    first, last = pd.Period(seen.min(), freq="M"), pd.Period(seen.max(), freq="M")
    return (last - first).n + 1


def validate(tables: dict[str, pd.DataFrame]) -> list[Check]:
    """Everything the build would quietly drop or misread, said out loud; Fail where nothing sensible can be built."""
    out = [Check(FAIL, t.label, f"Missing. {t.meaning}") for t in TABLES if t.required and t.name not in tables]
    known: set[str] = set()
    if "items" in tables:
        checks, known = _check_items(tables["items"])
        out += checks
    used = []
    for name, qty in (("receipts", "qty_received"), ("contractor_moves", "qty"), ("transfers", "qty")):
        if name in tables:
            checks, rows = _check_moves(name, tables[name], qty, known)
            out += checks
            used.append(rows)
            if (
                name == "contractor_moves"
                and len(rows)
                and not rows["type"].astype(str).str.upper().eq("CONSUMPTION").any()
            ):
                out.append(
                    Check(
                        FAIL,
                        BY_NAME[name].label,
                        "No CONSUMPTION rows: nothing says what the site used, so there is nothing to plan against.",
                    )
                )
    n = _months(*[r["month"] for r in used]) if used else 0
    if used and 0 < n < MIN_MONTHS:
        out.append(
            Check(
                FAIL,
                "All",
                f"The records cover {n} months. The replay needs at least {MIN_MONTHS}: 3 to learn from and 4 to "
                "score.",
            )
        )
    elif used and MIN_MONTHS <= n < SHORT_MONTHS:
        out.append(Check(WARN, "All", f"The records cover only {n} months, so the results will be rough."))
    elif used and n > MAX_MONTHS:
        out.append(
            Check(
                FAIL,
                "All",
                f"The records span {n} months; Yardstick reads up to {MAX_MONTHS}. Check for a mistyped year.",
            )
        )
    if used and n <= MAX_MONTHS:
        godowns = set()
        for rows in used:
            if "godown" in rows:
                godowns |= set(rows["godown"].dropna().astype(str).str.strip()) - {""}
        too_big = check_size(len(known), len(godowns), n)
        if too_big:
            out.append(Check(FAIL, "All", f"The project is too big: {too_big}. Upload one project at a time."))
    if "sets" in tables:
        out += _check_sets(tables["sets"], known)
    if "boq" in tables:
        out += _check_boq(tables["boq"], known)
    if "meta" in tables:
        out += _check_meta(tables["meta"])
    return out


def check(parts: list[Part]) -> Report:
    """Map every part, add up the parts that hold the same table, and check the result."""
    total = sum(len(p.sheet.frame) for p in parts)
    if total > MAX_TOTAL_ROWS:
        return Report([Check(FAIL, "All", f"More than {MAX_TOTAL_ROWS:,} rows in all: upload less at a time.")], {})
    out: list[Check] = []
    grouped: dict[str, list[pd.DataFrame]] = {}
    for p in parts:
        t = BY_NAME[p.table]
        missing = [c for c in t.needed if p.mapping.get(c) not in p.sheet.frame.columns]
        if missing:
            out.append(
                Check(
                    FAIL,
                    t.label,
                    f"{p.sheet.source}: no column picked for {', '.join(missing)}. Pick one under 'Match the columns'.",
                )
            )
            continue
        grouped.setdefault(p.table, []).append(apply_mapping(t, p.sheet.frame, p.mapping))
    tables = {}
    for name, frames in grouped.items():
        if len(frames) > 1:
            out.append(
                Check(
                    WARN,
                    BY_NAME[name].label,
                    f"{len(frames)} files or sheets are added together. Any rows that appear in more than one count "
                    "more than once.",
                )
            )
        tables[name] = pd.concat(frames, ignore_index=True)
    present = {p.table for p in parts}
    out += [Check(FAIL, t.label, f"Missing. {t.meaning}") for t in TABLES if t.required and t.name not in present]
    if any(c.level == FAIL for c in out):  # a table is missing or a needed column is not picked: fix that first
        return Report(out, tables)
    out += _normalise(tables)
    return Report(out + validate(tables), tables)


# Building -----------------------------------------------------------------------------------------------------------


def build(tables: dict[str, pd.DataFrame], project: str = "") -> Dataset:
    """The project, from tables that passed the check. `project` names it (over any name in the project details)."""
    raw = {k: v.copy() for k, v in tables.items()}
    items = raw["items"]
    for col in ("item_name", "unit"):
        if col not in items:
            items[col] = None
    if "sets" in raw and "set_no" in raw["sets"]:
        numbers = pd.to_numeric(raw["sets"]["set_no"], errors="coerce")
        if numbers.notna().all():  # as the command line reads it
            raw["sets"]["set_no"] = numbers
    name = project.strip()[:80]
    if name:
        row = pd.DataFrame({"key": ["project"], "value": [name]})
        meta = raw.get("meta")
        if meta is not None:
            meta = meta[meta["key"].astype(str).str.strip().str.lower() != "project"]
            row = pd.concat([meta, row], ignore_index=True)
        raw["meta"] = row
    return from_tables(ingest.monthly_tables(raw), key="custom")
