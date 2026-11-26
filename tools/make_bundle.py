"""Pack a project's raw files into the bundle the app reads (run locally; the outputs are git-ignored).

    python tools/make_bundle.py <folder with the csv files> [--meta meta.json] [--out data_local/bundle.txt]
                                [--secrets data_local/streamlit_secrets.toml]

The folder holds items.csv, receipts.csv and contractor_moves.csv, and optionally sets.csv, transfers.csv, meta.csv
and boq.csv (see core/ingest.py). `--meta` adds labels and settings from a JSON object (see core/config.py).
`--secrets` also writes the one line to paste into the host's secrets box.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.bundle import LOCAL_FILE, SECRET_NAMES, pack  # noqa: E402
from core.ingest import monthly_tables, read_folder  # noqa: E402


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder")
    ap.add_argument("--meta", type=Path, help="JSON object of meta keys (project, company, anchor_code, ...)")
    ap.add_argument("--out", type=Path, default=LOCAL_FILE)
    ap.add_argument("--secrets", type=Path, help="also write a secrets.toml line for the host")
    args = ap.parse_args(argv)

    raw = read_folder(args.folder)
    if args.meta:
        given = json.loads(args.meta.read_text(encoding="utf-8"))
        meta = pd.DataFrame([(str(k), str(v)) for k, v in given.items()], columns=["key", "value"])
        if "meta" in raw:
            meta = pd.concat([raw["meta"][~raw["meta"]["key"].isin(meta["key"])], meta], ignore_index=True)
        raw["meta"] = meta
    tables = monthly_tables(raw)
    blob = pack(tables)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(blob, encoding="utf-8")
    print(
        f"wrote {args.out} ({len(blob) / 1024:.0f} KB) | " + ", ".join(f"{k}: {len(v)} rows" for k, v in tables.items())
    )
    if args.secrets:
        args.secrets.parent.mkdir(parents=True, exist_ok=True)
        args.secrets.write_text(f'{SECRET_NAMES[0]} = "{blob}"\n', encoding="utf-8")
        print(f"wrote {args.secrets}: paste its one line into the host's Secrets box")


if __name__ == "__main__":
    main()
