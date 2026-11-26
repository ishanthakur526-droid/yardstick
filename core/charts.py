"""Plotly figures. One palette, used everywhere, so a colour always means the same thing."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .fmt import short_godown

COLOR = {
    "arrived": "#3b82f6",
    "issued": "#f59e0b",
    "used": "#10b981",
    "godown": "#7c3aed",
    "contractor": "#fb923c",
    "plan": "#0ea5e9",
    "real": "#a855f7",
    "short": "#ef4444",
}
_GRID = "rgba(128,128,128,0.22)"


def _dates(months: pd.Series) -> pd.DatetimeIndex:
    return pd.to_datetime(months.astype(str) + "-01")


def _base(fig: go.Figure, height: int) -> go.Figure:
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=8, t=72, b=8),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.07, x=0),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    fig.update_xaxes(gridcolor=_GRID, showline=False)
    fig.update_yaxes(gridcolor=_GRID, zeroline=False)
    return fig


def stock_story(site: pd.DataFrame, unit: str) -> go.Figure:
    """Top: where the stock sat. Bottom: what moved each month. Same months, so the gap is obvious."""
    x = _dates(site["month"])
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.6, 0.4],
        vertical_spacing=0.08,
        subplot_titles=(f"Stock on hand at month end ({unit})", f"What moved each month ({unit})"),
    )
    fig.add_trace(
        go.Scatter(
            x=x,
            y=site["godown_stock"],
            name="Idle in godowns",
            mode="lines",
            stackgroup="stock",
            line=dict(width=0.8, color=COLOR["godown"]),
            fillcolor="rgba(124,58,237,0.55)",
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=x,
            y=site["with_contractors"],
            name="Held by contractors",
            mode="lines",
            stackgroup="stock",
            line=dict(width=0.8, color=COLOR["contractor"]),
            fillcolor="rgba(251,146,60,0.55)",
        ),
        row=1,
        col=1,
    )
    for col, label, color in (
        ("received", "Arrived from vendors", "arrived"),
        ("issue", "Issued to contractors", "issued"),
        ("used", "Actually used", "used"),
    ):
        fig.add_trace(go.Bar(x=x, y=site[col], name=label, marker_color=COLOR[color]), row=2, col=1)
    fig.update_layout(barmode="group", bargap=0.2)
    fig.update_annotations(font_size=13, xanchor="left", x=0)
    return _base(fig, 600)


def replay_chart(monthly: pd.DataFrame, takeover: int, eval_start: int, unit: str) -> go.Figure:
    """Real idle stock against the plan's, with use underneath and shortage and orders below."""
    x = _dates(monthly["month"])
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.72, 0.28],
        vertical_spacing=0.07,
        subplot_titles=(
            f"Idle stock at month end ({unit})",
            f"Orders the planner placed, and use it could not supply ({unit})",
        ),
    )
    fig.add_trace(
        go.Bar(x=x, y=monthly["used"], name="Actually used", marker_color=COLOR["used"], opacity=0.5), row=1, col=1
    )
    fig.add_trace(
        go.Scatter(
            x=x,
            y=monthly["real_idle"],
            name="What really happened",
            mode="lines",
            line=dict(width=3, color=COLOR["real"]),
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=x, y=monthly["plan_idle"], name="With the planner", mode="lines", line=dict(width=3, color=COLOR["plan"])
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Bar(x=x, y=monthly["plan_order"], name="Orders placed", marker_color=COLOR["plan"], opacity=0.7),
        row=2,
        col=1,
    )
    fig.add_trace(
        go.Bar(x=x, y=monthly["plan_shortage"], name="Could not supply", marker_color=COLOR["short"]), row=2, col=1
    )

    first = x[0].to_pydatetime()
    take = x[min(takeover, len(x) - 1)].to_pydatetime()
    ev = x[min(eval_start, len(x) - 1)].to_pydatetime()
    for x0, x1, label, fill in (
        (first, take, "Original plan", "rgba(128,128,128,0.10)"),
        (take, ev, "First orders on their way", "rgba(14,165,233,0.07)"),
    ):
        if x1 > x0:
            fig.add_shape(
                type="rect",
                xref="x",
                yref="paper",
                x0=x0,
                x1=x1,
                y0=0,
                y1=1,
                fillcolor=fill,
                line_width=0,
                layer="below",
            )
            fig.add_annotation(
                xref="x",
                yref="y domain",
                x=x0,
                y=0.98,
                text=label,
                showarrow=False,
                xanchor="left",
                yanchor="top",
                font=dict(size=11, color="gray"),
            )
    fig.add_shape(
        type="line", xref="x", yref="paper", x0=ev, x1=ev, y0=0, y1=1, line=dict(width=1, dash="dot", color="gray")
    )
    fig.update_layout(barmode="overlay", bargap=0.2)
    fig.update_annotations(font_size=13, selector=dict(xref="paper"))
    return _base(fig, 620)


def set_calendar(cal: pd.DataFrame) -> go.Figure:
    """For every month: how many godown accounts were out of balance, serious or on watch."""
    x = _dates(cal["month"])
    fig = go.Figure()
    fig.add_trace(go.Bar(x=x, y=cal["serious"], name="Serious", marker_color=COLOR["short"]))
    fig.add_trace(go.Bar(x=x, y=cal["watch"], name="Watch", marker_color=COLOR["issued"]))
    fig.update_layout(barmode="stack", bargap=0.25, yaxis_title="godown accounts out of balance")
    return _base(fig, 260).update_layout(margin=dict(l=8, r=8, t=40, b=8))


def set_account(view_account: pd.DataFrame, child_name: str, watch: float, serious: float) -> go.Figure:
    """How far each godown's account for one child is off, as a share of every set issued so far."""
    fig = go.Figure()
    for place, sub in view_account.groupby("godown"):
        if place == "All godowns":
            continue
        fig.add_trace(go.Scatter(x=_dates(sub["month"]), y=sub["off"], name=short_godown(place), mode="lines"))
    for level, color in ((watch, "rgba(245,158,11,0.9)"), (serious, "rgba(239,68,68,0.9)")):
        for sign in (1, -1):
            fig.add_hline(y=sign * level, line_dash="dot", line_color=color, line_width=1)
    fig.update_yaxes(tickformat=".0%", title=f"{child_name}: ahead (+) or behind (−) the parents")
    return _base(fig, 340)


def godown_heatmap(grid_item: pd.DataFrame, unit: str) -> go.Figure:
    """Godown by month: where it piled up."""
    piv = grid_item.pivot(index="godown", columns="month", values="stock")
    places = list(piv.index)
    fig = go.Figure(
        go.Heatmap(
            z=piv.loc[places].values,
            x=_dates(pd.Series(piv.columns)),
            y=[short_godown(g) for g in places],
            colorscale="Purples",
            colorbar=dict(title=unit, thickness=12),
            hovertemplate="%{y} · %{x|%b %Y}<br>%{z:,.0f} " + unit + " idle<extra></extra>",
        )
    )
    fig.update_yaxes(autorange="reversed")
    fig.update_layout(hovermode="closest")
    return _base(fig, 300).update_layout(margin=dict(l=8, r=8, t=8, b=8))
