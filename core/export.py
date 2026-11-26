"""Excel export of the plan: the list to approve, the replay, the scoreboard and the assumptions."""

from __future__ import annotations

import io

import pandas as pd

from . import explain
from .fmt import month_label
from .planner import MAX_SHORTAGE_SHARE, Replay
from .requisition import BASES, Requisition

XLSX_OPTIONS = {"options": {"strings_to_formulas": False, "strings_to_urls": False}}  # text stays text


def assumptions(r: Replay, name: str, source: str = "") -> list[str]:
    s = r.settings
    return [
        f"Item: {name} ({r.unit}); lead time {r.lead_months} months.",
        f"The first {s.takeover} months run on what really arrived (a forecast needs history). Scored from {month_label(r.summary['eval_from'])}.",
        f"Forecast: the higher of the last 3 and last {s.window} months' average use. Safety margin: {s.safety:.2f} standard deviations of the last {s.sd_window} months.",
        "Orders cover the lead time plus this month of forecast use plus the safety margin, after counting stock and orders already on the way.",
        "The plan never buys more in total than the project really bought (the data has no bill of quantities).",
        "Use is recorded for the whole project only; it is split across godowns by what each godown handed out that month (an estimate).",
        "Stock may move between godowns the same day (the records show same-day transfer vouchers).",
        f"A saving counts only if the plan left under {MAX_SHORTAGE_SHARE:.0%} of use unsupplied.",
        _source_line(source),
    ]


def _source_line(source: str) -> str:
    return (f"Source data: {source}. " if source else "") + "Planning support, not an instruction to buy."


def workbook(r: Replay, month: str, name: str, everything: pd.DataFrame, source: str = "") -> bytes:
    """One .xlsx a store-keeper or purchase officer can open: this month's list, the full replay, all items, assumptions."""
    plan_now = explain.month_plan(r, month)
    replay = pd.DataFrame(
        {
            "Month": r.monthly["month"],
            "Actually used": r.monthly["used"],
            "Really arrived": r.monthly["real_arrived"],
            "Really idle": r.monthly["real_idle"],
            "Planner ordered": r.monthly["plan_order"],
            "Plan: arrived": r.monthly["plan_arrived"],
            "Plan: idle": r.monthly["plan_idle"],
            "Plan: could not supply": r.monthly["plan_shortage"],
        }
    )
    board = everything.rename(
        columns={
            "item": "Item",
            "unit": "Unit",
            "lead_days": "Lead time (days)",
            "avg_real": "Idle really (avg)",
            "avg_plan": "Idle with planner (avg)",
            "cut": "Cut",
            "peak_real": "Peak really",
            "peak_plan": "Peak with planner",
            "short": "Shortage share",
            "orders": "Orders",
            "counts": "Counts?",
        }
    ).drop(columns=["code"])
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter", engine_kwargs=XLSX_OPTIONS) as xl:
        book = xl.book
        wrap = book.add_format({"text_wrap": True, "valign": "top"})
        head = book.add_format({"bold": True, "bg_color": "#EDE9FE", "border": 1, "text_wrap": True, "valign": "top"})
        sheets = [(f"Plan for {month}", plan_now), ("Replay", replay), ("All items", board)]
        for title, df in sheets:
            df.to_excel(xl, sheet_name=title[:31], index=False, startrow=1, header=False)
            ws = xl.sheets[title[:31]]
            for c, col in enumerate(df.columns):
                ws.write(0, c, col, head)
                width = 60 if col == "Why" else (26 if col in ("Action", "Item") else 16)
                ws.set_column(c, c, width, wrap if col in ("Why", "Action", "Item") else None)
            ws.freeze_panes(1, 0)
        pd.DataFrame({"Assumptions and limits": assumptions(r, name, source)}).to_excel(
            xl, sheet_name="Assumptions", index=False
        )
        xl.sheets["Assumptions"].set_column(0, 0, 130, wrap)
    return buf.getvalue()


def requisition_assumptions(req: Requisition, source: str = "") -> list[str]:
    held = "plus stock still with contractors" if req.with_contractors else "not counting stock still with contractors"
    out = [
        f"Stock in hand is as at {month_label(req.stock_month)}, the last month in the data: godown stock including the closing store, {held}.",
        f"The material is needed on site by {req.needed_by:%d %b %Y} ({(req.needed_by - req.today).days} days from {req.today:%d %b %Y}). "
        "Order by = that date minus the item's usual lead time; a line is 'too late' when that date has already passed.",
        "A line typed on the BOQ is used as the requirement. Nothing here overrides it.",
        f"Parts that come with a set (eye hooks, lugs, bolts) use the ratio {BASES[req.basis].lower()}. The sheet 'Ratio check' shows both, "
        "because the data pack's ratios often disagree with what the site really used.",
    ]
    if req.handoff_days:
        out.append(
            f"Procurement needs {req.handoff_days} days to place an order, so the list must reach them that long before the order-by date "
            "('Send to procurement by')."
        )
    per = sorted(s for s in req.lines["source"].unique() if str(s).startswith("Per "))
    if per:
        out.append(
            f"Items marked '{per[0]}' are estimates from what this project used per unit of the BOQ's main item, not BOQ lines. "
            "Replace them with the BOQ's own figures."
        )
    out += [
        "Items bought in whole units are rounded up.",
        "The data has no prices and no orders still on the way, so open orders are not taken off the buy list.",
        _source_line(source),
    ]
    return out


def requisition_workbook(req: Requisition, source: str = "") -> bytes:
    """One .xlsx for head-office procurement: the buy list, where the ratios disagree, and the assumptions."""
    lines = req.lines
    buy = pd.DataFrame(
        {
            "Item": lines["item"],
            "Unit": lines["unit"],
            "Needed": lines["needed"],
            "Where the number comes from": lines["source"],
            "In godowns": lines["in_godowns"],
            "With contractors": lines["with_contractors"],
            "To buy": lines["buy"],
            "Left in stock after": lines["left_over"],
            "Lead time (days)": lines["lead_days"],
            "Order by": [f"{d:%d %b %Y}" if k != "covered" else "" for d, k in zip(lines["order_by"], lines["kind"])],
            "Send to procurement by": [
                f"{d:%d %b %Y}" if k != "covered" else "" for d, k in zip(lines["send_by"], lines["kind"])
            ],
            "Status": lines["status"],
        }
    )
    derived = lines[lines["at_file"].notna()]
    check = pd.DataFrame(
        {
            "Item": derived["item"],
            "Unit": derived["unit"],
            "Needed at the file's ratio": derived["at_file"],
            "Needed at the site's ratio": derived["at_site"],
            "What it says": derived["check"],
        }
    )
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter", engine_kwargs=XLSX_OPTIONS) as xl:
        book = xl.book
        wrap = book.add_format({"text_wrap": True, "valign": "top"})
        head = book.add_format({"bold": True, "bg_color": "#EDE9FE", "border": 1, "text_wrap": True, "valign": "top"})
        for title, df in (("Buy list", buy), ("Ratio check", check)):
            df.to_excel(xl, sheet_name=title, index=False, startrow=1, header=False)
            ws = xl.sheets[title]
            for c, col in enumerate(df.columns):
                ws.write(0, c, col, head)
                ws.set_column(
                    c,
                    c,
                    60 if col == "What it says" else (46 if col == "Item" else 18),
                    wrap if col in ("Item", "What it says") else None,
                )
            ws.freeze_panes(1, 0)
        pd.DataFrame({"Assumptions and limits": requisition_assumptions(req, source)}).to_excel(
            xl, sheet_name="Assumptions", index=False
        )
        xl.sheets["Assumptions"].set_column(0, 0, 130, wrap)
    return buf.getvalue()
