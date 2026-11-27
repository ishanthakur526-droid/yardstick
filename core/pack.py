"""The weekly action pack: Monday's list as a workbook procurement and stores can work from.

Built "as of" a month, like Monday's list: only the records before that month are read, plus the deliveries already
on order. Sheets, in the order the work is done:

  Summary      what is in the pack and the date it is for
  Transfers    transfer vouchers between godowns: stock already bought moves before anything new is ordered
  Orders       purchase orders to place by that Monday, the month each lands, and the split by godown
  Issue holds  contractors holding more than the cover rule allows: issue only what the site uses beyond it
  Set alerts   sets out of balance in a godown at the end of last month (a child behind or ahead of its parent)
  BOM fixes    master-data ratios that disagree with site use so far; a person approves each line
  Assumptions  how every number was made

PO hold/accelerate needs a table of orders already placed, which the data does not have; the pack says so instead.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

import pandas as pd

from . import bomfix, holdings, money
from . import sets as setcheck
from .actions import monday, week
from .config import scaled_thresholds
from .dataset import Dataset
from .export import XLSX_OPTIONS, _source_line
from .fmt import month_label
from .planner import MAX_SHORTAGE_SHARE, Settings

SHEETS = ["Summary", "Transfers", "Orders", "Issue holds", "Set alerts", "BOM fixes", "Assumptions"]
WIDE = {"Why", "Note", "Reason", "What to do", "Assumptions and limits", "Split by godown"}
MID = {"Item", "Set", "Child", "What"}


@dataclass
class Pack:
    month: str
    monday: pd.Timestamp
    sheets: dict[str, pd.DataFrame] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)


def _note(text: str) -> pd.DataFrame:
    return pd.DataFrame({"Note": [text]})


def _rounded(ledger, codes: pd.Series, qty: pd.Series) -> list[float]:
    return [round(float(q), ledger.decimals(c)) for c, q in zip(codes, qty)]


def as_of_thresholds(ds: Dataset, month: str) -> tuple[float, float]:
    """The set-imbalance thresholds as of `month`. Thresholds the data gives are used as they are; thresholds scaled to
    the project's size are scaled again on the months before `month` only (the whole project's size is the future)."""
    p, s = ds.cfg.min_parents, ds.cfg.min_sets
    if any(ds.cfg.scaled):
        flows = ds.tables["flows"]
        early_p, early_s = scaled_thresholds(flows[flows["month"] < month], ds.sets)
        p = early_p if ds.cfg.scaled[0] else p
        s = early_s if ds.cfg.scaled[1] else s
    return p, s


def set_alerts(ds: Dataset, month: str) -> pd.DataFrame:
    """Every godown's set account at the end of the month before `month`, with ratios learned from the months before
    it (never the whole project)."""
    led = ds.ledger
    i = led.months.index(month)
    if ds.sets.empty or i == 0:
        return pd.DataFrame()
    cal = setcheck.calibrate(led, ds.sets, before=month)
    min_parents, min_sets = as_of_thresholds(ds, month)
    last = led.months[i - 1]
    frames = []
    for parent in dict.fromkeys(cal["parent_code"]):
        view = setcheck.balance(led, ds.sets, parent, "observed", cal=cal, min_parents=min_parents, min_sets=min_sets)
        f = setcheck.flagged(view)
        f = f[f["month"] == last]
        if len(f):
            frames.append(f.assign(set_name=view.parent_name))
    if not frames:
        return pd.DataFrame()
    a = pd.concat(frames, ignore_index=True)
    return pd.DataFrame(
        {
            "Set": a["set_name"],
            "Godown": a["godown"].map(led.label),
            "Child": a["child_name"],
            "Severity": a["severity"],
            "Child is": a["kind"],
            "Sets' worth": [round(abs(v)) for v in a["gap_sets"]],
            "Reason": a["reason"],
        }
    )


def build(
    ds: Dataset,
    month: str,
    settings: Settings = Settings(),
    rates: dict[str, float] | None = None,
    actions: pd.DataFrame | None = None,
) -> Pack:
    """Every sheet as a table. `actions` is Monday's list when the caller already has it (the app's cached copy)."""
    led = ds.ledger
    day = monday(month)
    rates = money.clean_rates(rates)
    boq = {c: ds.boq_qty(c) for c in led.items["item_code"]}
    w = week(led, month, settings, boq, ds.sets) if actions is None else actions
    pack = Pack(month=month, monday=day)

    moves = w[w["kind"] == "Move"].reset_index(drop=True)
    pack.sheets["Transfers"] = (
        pd.DataFrame(
            {
                "Voucher": [f"T{n + 1:02d}" for n in range(len(moves))],
                "Item": moves["item"],
                "Unit": moves["unit"],
                "Quantity": _rounded(led, moves["item_code"], moves["qty"]),
                "From": moves["from_godown"],
                "To": moves["to_godown"],
                "Why": moves["why"],
            }
        )
        if len(moves)
        else _note("No transfers this week: no godown is below this month's need while another holds a surplus.")
    )

    orders = w[w["kind"] == "Order"].reset_index(drop=True)
    if len(orders):
        o = pd.DataFrame(
            {
                "PO line": [f"P{n + 1:02d}" for n in range(len(orders))],
                "Item": orders["item"],
                "Unit": orders["unit"],
                "Quantity": _rounded(led, orders["item_code"], orders["qty"]),
                "Order by": f"{day:%d %b %Y}",
                "Lands": [month_label(m) if m else "after the last recorded month" for m in orders["lands"]],
                "Split by godown": orders["to_godown"],
                "Why": orders["why"],
            }
        )
        if rates:
            o["Value (your rates)"] = [
                round(q * rates[c]) if c in rates else None for c, q in zip(orders["item_code"], orders["qty"])
            ]
        pack.sheets["Orders"] = o
    else:
        pack.sheets["Orders"] = _note("No purchase orders this week: stock and orders on the way cover the need.")

    h = holdings.at(led, month, settings)
    h = h[h["alert"]].reset_index(drop=True)
    if len(h):
        names = h["item_code"].map(led.item_name)
        hold = pd.DataFrame(
            {
                "Item": names,
                "Unit": h["item_code"].map(led.unit),
                "With contractors": _rounded(led, h["item_code"], h["held"]),
                "Weeks of use held": [round(v, 1) for v in h["weeks_held"]],
                "Rule allows": _rounded(led, h["item_code"], h["allowed"]),
                "Over the rule": _rounded(led, h["item_code"], h["excess"]),
                "What to do": f"Issue only what the site uses beyond the excess until contractors hold no more than "
                f"{settings.cover_weeks:g} weeks of forecast use.",
            }
        )
        if rates:
            hold["Value over the rule (your rates)"] = [
                round(x * rates[c]) if c in rates else None for c, x in zip(h["item_code"], h["excess"])
            ]
        pack.sheets["Issue holds"] = hold
    else:
        pack.sheets["Issue holds"] = _note(
            "No issue holds: contractors hold no more than the rule allows."
            if settings.cover_weeks is not None
            else "No issue rule is set (contractors draw as they did), so there are no issue holds."
        )

    if ds.sets.empty:
        pack.sheets["Set alerts"] = _note("This project's data has no sets table, so sets cannot be checked.")
        pack.sheets["BOM fixes"] = _note("This project's data has no sets table, so there are no ratios to check.")
        n_alerts = n_fixes = 0
    else:
        alerts = set_alerts(ds, month)
        n_alerts = len(alerts)
        pack.sheets["Set alerts"] = (
            alerts if n_alerts else _note("Every set was in balance at the end of last month, as far as can be judged.")
        )
        fixes = bomfix.fix_sheet(setcheck.calibrate(led, ds.sets, before=month))
        n_fixes = int((fixes["action"] != bomfix.KEEP).sum())
        pack.sheets["BOM fixes"] = bomfix.sheet_for_people(fixes)

    pack.counts = {
        "transfers": len(moves),
        "orders": len(orders),
        "holds": len(h),
        "set_alerts": n_alerts,
        "bom_fixes": n_fixes,
    }
    value = None
    if rates and len(orders) and "Value (your rates)" in pack.sheets["Orders"]:
        priced = pack.sheets["Orders"]["Value (your rates)"].dropna()
        value = f"{money.rupees(float(priced.sum()))} for {len(priced)} of {len(orders)} order lines (your rates)"
    summary = [
        ("Project", ds.cfg.title),
        ("For", f"Monday {day:%d %b %Y}: the records before {month_label(month)}, plus deliveries already on order"),
        ("Transfers (do these first)", str(pack.counts["transfers"])),
        ("Purchase orders", str(pack.counts["orders"]) + (f": {value}" if value else "")),
        ("Contractor issue holds", str(pack.counts["holds"])),
        ("Set alerts", str(n_alerts)),
        ("Master-data ratios to review", str(n_fixes)),
        ("Approval", "Planning support: a person approves every order, transfer and ratio change."),
    ]
    pack.sheets["Summary"] = pd.DataFrame(summary, columns=["What", "Value"])
    pack.sheets["Assumptions"] = pd.DataFrame({"Assumptions and limits": assumptions(ds, month, settings, bool(rates))})
    pack.sheets = {name: pack.sheets[name] for name in SHEETS}
    return pack


def assumptions(ds: Dataset, month: str, settings: Settings, priced: bool) -> list[str]:
    led = ds.ledger
    with_boq = sum(ds.boq_qty(c) is not None for c in led.items["item_code"])
    if not settings.capped:
        source = "none"
    else:
        source = "boq" if with_boq == len(led.items) else ("stand-in" if with_boq == 0 else "some")
    out = [
        f"As of Monday {monday(month):%d %b %Y}: only the records before {month_label(month)} are read, plus the "
        "deliveries already on order (receipts landing within one lead time).",
        f"Forecast: the higher of the last 3 and last {settings.window} months' average site use. Safety margin: "
        f"{settings.safety:g} standard deviations of the last {settings.sd_window} months.",
        "Orders cover the lead time plus this month of forecast demand plus the safety margin, after stock in the "
        "godowns, orders on the way and contractors' stock beyond their cover. Order by: that Monday. Lands: one lead "
        "time later.",
        {
            "boq": "Orders never take total buying past the bill of quantities.",
            "stand-in": "The data has no bill of quantities, so what the project really bought stands in for it as the "
            "cap on total buying (the one number taken from the future).",
            "some": "Orders never take total buying past the bill of quantities; for items the BOQ does not list, "
            "what the project really bought stands in for it.",
            "none": "No cap on total buying.",
        }[source],
        "Transfers come first: a godown below this month's need takes stock from one above its own lead-time need.",
        "Contractors drew as they really drew (no issue rule)."
        if settings.cover_weeks is None
        else f"Issue rule: contractors end each month holding at most {settings.cover_weeks:g} weeks of forecast use; "
        "the records do not say which contractor holds what, so they are one pool for the project.",
    ]
    if not ds.sets.empty:
        out += [
            "A child item is forecast from its sets (its parents' forecast times the ratio the site has shown so far) "
            "only where that has done no worse than its own history up to this month.",
            "Set alerts: issues to the end of last month, at the ratio the site had shown by then; 15% off is 'watch', "
            "30% 'serious'; only children whose use follows the parent closely are judged. A gap counts once a godown "
            "has issued enough parents and the gap is big enough; where the data does not give those two thresholds, "
            "they are scaled to the project's size as it stood then.",
            "BOM fixes: ratios from site use up to last month. Nothing is changed in the master data; a person approves "
            "every line. A ratio more than 100 times off is more likely a unit mismatch than a wrong count.",
        ]
    out += [
        "No table of orders already placed was supplied, so purchase orders cannot be held back or brought forward "
        "here.",
        f"In the replay, a saving counts only if the plan left no more than {MAX_SHORTAGE_SHARE:.0%} of use unsupplied.",
    ]
    if priced:
        out.append("Values use the rates typed in this session only; they are not stored anywhere.")
    out.append(_source_line(ds.cfg.source))
    return out


def workbook(pack: Pack) -> bytes:
    """The pack as one .xlsx."""
    return write_sheets(pack.sheets)


def write_sheets(sheets: dict[str, pd.DataFrame]) -> bytes:
    """Tables as one .xlsx, one sheet each. Text from the data stays text: no formulas or links
    (`export.XLSX_OPTIONS`)."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter", engine_kwargs=XLSX_OPTIONS) as xl:
        book = xl.book
        wrap = book.add_format({"text_wrap": True, "valign": "top"})
        head = book.add_format({"bold": True, "bg_color": "#EDE9FE", "border": 1, "text_wrap": True, "valign": "top"})
        for title, df in sheets.items():
            df.to_excel(xl, sheet_name=title, index=False, startrow=1, header=False)
            ws = xl.sheets[title]
            for c, col in enumerate(df.columns):
                ws.write(0, c, col, head)
                width = 70 if col in WIDE or col == "Note" else (36 if col in MID else 15)
                ws.set_column(c, c, width, wrap if (col in WIDE or col in MID or col == "Note") else None)
            ws.freeze_panes(1, 0)
    return buf.getvalue()
