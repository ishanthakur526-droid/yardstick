"""Time every section of the app, cold and warm, on the sample project and (when the bundle is here) the real one.

    python tools/profile_sections.py                 # the table
    python tools/profile_sections.py --profile "Plan it" bundle   # cProfile of that section's warm run, top 25

Runs the app headless with Streamlit's AppTest, so the times are the script's own (no browser, no network). "Warm" is a
second run in the same process with every cache filled, which is what a visitor clicking around sees.
"""

from __future__ import annotations

import argparse
import cProfile
import io
import logging
import pstats
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
logging.disable(logging.WARNING)  # AppTest's "missing ScriptRunContext" chatter

from streamlit.testing.v1 import AppTest  # noqa: E402

from core.bundle import find_blob  # noqa: E402

APP = str(ROOT / "app.py")
SECTIONS = ["Home", "See it", "Plan it", "Requisition", "Match the sets", "About the data", "Your data"]


def _app(section: str, dataset: str) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=600)
    at.session_state["dataset"] = dataset
    at.session_state["section"] = section
    return at


def timed(section: str, dataset: str) -> tuple[float, float]:
    at = _app(section, dataset)
    t = time.perf_counter()
    at.run()
    cold = time.perf_counter() - t
    if at.exception:
        raise RuntimeError(f"{section} on {dataset}: {[e.value for e in at.exception]}")
    t = time.perf_counter()
    at.run()
    return cold, time.perf_counter() - t


def profile(section: str, dataset: str, top: int = 25) -> str:
    at = _app(section, dataset)
    at.run()
    prof = cProfile.Profile()
    prof.enable()
    at.run()
    prof.disable()
    out = io.StringIO()
    pstats.Stats(prof, stream=out).sort_stats("cumulative").print_stats(top)
    return out.getvalue()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", nargs=2, metavar=("SECTION", "DATASET"))
    args = ap.parse_args()
    if args.profile:
        print(profile(*args.profile))
        return
    datasets = ["sample"] + (["bundle"] if find_blob() else [])
    print(f"{'section':<16}" + "".join(f"{d + ' cold':>14}{d + ' warm':>14}" for d in datasets))
    for section in SECTIONS:
        cells = []
        for d in datasets:
            cold, warm = timed(section, d)
            cells.append(f"{cold:>13.2f}s{warm:>13.2f}s")
        print(f"{section:<16}" + "".join(cells))


if __name__ == "__main__":
    main()
