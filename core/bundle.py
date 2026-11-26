"""The data bundle: how a project's records reach the app without living in the repo.

`tools/make_bundle.py` sums a project's raw files to month x item x godown and packs the result into one text blob
(each table as CSV text, then JSON, zlib and base64). The app looks for it in, in this order:

1. Streamlit secrets: `YARDSTICK_BUNDLE`, or the older name `LUMINO_BUNDLE`   - how the deployed app gets it
2. the same names as environment variables
3. `data_local/bundle.txt`                                                     - local development (git-ignored)

Nothing readable is committed. Without a bundle the app runs on its built-in sample project.
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import os
import zlib
from pathlib import Path

import pandas as pd

BUNDLE_VERSION = 1
SECRET_NAMES = ("YARDSTICK_BUNDLE", "LUMINO_BUNDLE")
LOCAL_FILE = Path(__file__).resolve().parent.parent / "data_local" / "bundle.txt"
MAX_BLOB_CHARS = 30_000_000  # a packed bundle is ~40 KB for a 3.5-year project; anything near this is not a bundle
MAX_UNPACKED_BYTES = 300_000_000  # refuse to inflate past this (a zip bomb would)
TEXT_COLUMNS = (  # read as text, so a code like 0100 keeps its leading zero
    "item_code",
    "item_name",
    "unit",
    "role",
    "godown",
    "month",
    "parent_code",
    "parent_name",
    "parent_unit",
    "child_code",
    "child_name",
    "child_unit",
    "key",
    "value",
)


class BundleError(ValueError):
    """The bundle is there but cannot be read."""


def pack(tables: dict[str, pd.DataFrame]) -> str:
    payload = {name: df.to_csv(index=False) for name, df in tables.items()}
    raw = json.dumps({"v": BUNDLE_VERSION, "tables": payload}, separators=(",", ":")).encode()
    return base64.b64encode(zlib.compress(raw, 9)).decode()


def unpack(blob: str) -> dict[str, pd.DataFrame]:
    if len(blob) > MAX_BLOB_CHARS:
        raise BundleError("the data bundle is far larger than expected")
    try:
        inflate = zlib.decompressobj()
        raw = inflate.decompress(base64.b64decode(blob.strip()), MAX_UNPACKED_BYTES)
        if inflate.unconsumed_tail:
            raise BundleError("the data bundle unpacks to more than the app will read")
        data = json.loads(raw)
    except (binascii.Error, zlib.error, UnicodeDecodeError, json.JSONDecodeError) as err:
        raise BundleError("the data bundle is damaged") from err
    if not isinstance(data, dict) or data.get("v") != BUNDLE_VERSION or not isinstance(data.get("tables"), dict):
        raise BundleError("the data bundle has an unexpected format")
    tables = {}
    for name, text in data["tables"].items():
        if not isinstance(text, str):
            raise BundleError(f"table {name!r} in the data bundle is not text")
        header = pd.read_csv(io.StringIO(text), nrows=0).columns
        tables[str(name)] = pd.read_csv(io.StringIO(text), dtype={c: str for c in TEXT_COLUMNS if c in header})
    return tables


def _secret(name: str) -> str | None:
    try:
        import streamlit as st

        value = st.secrets.get(name)
    except (ImportError, FileNotFoundError):  # no secrets file: StreamlitSecretNotFoundError is a FileNotFoundError
        return None
    return str(value) if value else None


def find_blob() -> str | None:
    """Look for the bundle in secrets, then the environment, then the local file."""
    for name in SECRET_NAMES:
        blob = _secret(name) or os.environ.get(name)
        if blob:
            return blob
    if LOCAL_FILE.exists():
        return LOCAL_FILE.read_text(encoding="utf-8")
    return None


def load_tables() -> dict[str, pd.DataFrame] | None:
    blob = find_blob()
    return unpack(blob) if blob else None
