from __future__ import annotations

import plotly.graph_objects as go
import plotly.express as px
import pandas as pd

from charts.chart_view import (
    ALL_TYPES,
    AxisHints,
    ChartView,
    MULTI_MEASURE_TYPES,
    SINGLE_MEASURE_TYPES,
    GROUPED_TYPES,
)
from charts.units import CURRENCY_SYMBOLS


ALLOWED_TYPES = ALL_TYPES  # back-compat re-export


PALETTE = px.colors.qualitative.Set2


class ChartSpecError(ValueError):
    """Raised when a chart spec is invalid for the given data."""


# ---------- Public entry ----------

def render(view: ChartView, df: pd.DataFrame, hints: AxisHints) -> go.Figure:
    if view.type not in ALL_TYPES:
        raise ChartSpecError(
            f"Unsupported chart type '{view.type}'. Allowed: {sorted(ALL_TYPES)}"
        )

    referenced = [view.x, *view.y]
    if view.color:
        referenced.append(view.color)
    missing = [c for c in referenced if view.type != "indicator" and c not in df.columns]
    if missing:
        raise ChartSpecError(
            f"Columns {missing} not in result data. Available: {list(df.columns)}"
        )

    if view.type == "indicator":
        return _render_indicator(view, df, hints)
    if view.type in MULTI_MEASURE_TYPES:
        return _render_multi_measure(view, df, hints)
    if view.type in GROUPED_TYPES:
        return _render_grouped_bar(view, df, hints)
    if view.type == "horizontal_bar":
        return _render_horizontal_bar(view, df, hints)
    return _render_single_measure(view, df, hints)


# ---------- Indicator ----------

def _render_indicator(view: ChartView, df: pd.DataFrame, hints: AxisHints) -> go.Figure:
    y_col = view.y[0]
    if y_col not in df.columns or df.empty:
        raise ChartSpecError(f"Indicator needs column '{y_col}' with at least 1 row.")
    value = float(df[y_col].iloc[-1])
    unit = hints.left_y_unit
    number_fmt = _indicator_number_format(unit)
    fig = go.Figure(
        go.Indicator(
            mode="number",
            value=value,
            number=number_fmt,
        )
    )
    fig.update_layout(
        template="plotly_white",
        margin=dict(l=20, r=20, t=20, b=20),
        height=240,
    )
    return fig


def _indicator_number_format(unit: str) -> dict:
    if unit in CURRENCY_SYMBOLS:
        return {"prefix": CURRENCY_SYMBOLS[unit], "valueformat": ",.0f"}
    if unit == "pct":
        return {"suffix": "%", "valueformat": ".1f"}
    if unit == "count":
        return {"valueformat": ",.0f"}
    if unit == "hours":
        return {"suffix": "h", "valueformat": ".1f"}
    if unit == "days":
        return {"suffix": "d", "valueformat": ".1f"}
    return {"valueformat": ",.2f"}


# ---------- Multi-measure (line / bar / scatter) ----------

def _render_multi_measure(view: ChartView, df: pd.DataFrame, hints: AxisHints) -> go.Figure:
    if view.color and len(view.y) == 1:
        return _render_single_color_grouped(view, df, hints)

    fig = go.Figure()
    for i, col in enumerate(view.y):
        col_unit = hints.column_unit_map.get(col, hints.left_y_unit)
        secondary = hints.right_y_unit is not None and col_unit == hints.right_y_unit
        color = PALETTE[i % len(PALETTE)]
        col_label = hints.column_label_map.get(col, col)
        hover = _hover_template(col_unit, col_label)

        trace_kwargs = dict(
            x=df[view.x],
            y=df[col],
            name=col_label,
            hovertemplate=hover,
        )
        if view.type == "line":
            fig.add_trace(go.Scatter(
                mode="lines+markers",
                marker=dict(color=color),
                line=dict(color=color),
                yaxis="y2" if secondary else "y",
                **trace_kwargs,
            ))
        elif view.type == "bar":
            fig.add_trace(go.Bar(
                marker=dict(color=color),
                yaxis="y2" if secondary else "y",
                **trace_kwargs,
            ))
        elif view.type == "scatter":
            fig.add_trace(go.Scatter(
                mode="markers",
                marker=dict(color=color),
                yaxis="y2" if secondary else "y",
                **trace_kwargs,
            ))

    layout = dict(
        template="plotly_white",
        margin=dict(l=40, r=40, t=20, b=40),
        xaxis=_axis_layout(hints.x_unit, hints.x_label or view.x),
        yaxis=_axis_layout(hints.left_y_unit, hints.left_y_label),
    )
    if hints.right_y_unit is not None:
        layout["yaxis2"] = _axis_layout(
            hints.right_y_unit,
            hints.right_y_label or "",
            side="right",
            overlaying="y",
        )
    fig.update_layout(**layout)
    return fig


def _render_single_color_grouped(view: ChartView, df: pd.DataFrame, hints: AxisHints) -> go.Figure:
    y_col = view.y[0]
    if view.type == "line":
        fig = px.line(df, x=view.x, y=y_col, color=view.color)
    elif view.type == "bar":
        fig = px.bar(df, x=view.x, y=y_col, color=view.color)
    elif view.type == "scatter":
        fig = px.scatter(df, x=view.x, y=y_col, color=view.color)
    else:
        raise ChartSpecError(f"Color grouping not supported for type '{view.type}'.")
    y_unit = hints.column_unit_map.get(y_col, hints.left_y_unit)
    y_label = hints.column_label_map.get(y_col, y_col)
    fig.update_traces(hovertemplate=_hover_template(y_unit, y_label))
    fig.update_layout(
        template="plotly_white",
        margin=dict(l=40, r=20, t=20, b=40),
        xaxis=_axis_layout(hints.x_unit, hints.x_label or view.x),
        yaxis=_axis_layout(hints.left_y_unit, hints.left_y_label),
    )
    return fig


# ---------- Single-measure types ----------

def _render_single_measure(view: ChartView, df: pd.DataFrame, hints: AxisHints) -> go.Figure:
    y = view.y[0]
    if view.type == "pie":
        fig = px.pie(df, names=view.x, values=y)
    elif view.type == "histogram":
        fig = px.histogram(df, x=view.x, color=view.color)
    elif view.type == "box":
        fig = px.box(df, y=y, x=view.x, color=view.color)
    elif view.type == "heatmap":
        if view.color is None:
            raise ChartSpecError("heatmap requires color (z) field via view.color.")
        pivot = df.pivot_table(index=y, columns=view.x, values=view.color)
        fig = go.Figure(
            data=go.Heatmap(z=pivot.values, x=list(pivot.columns), y=list(pivot.index))
        )
    elif view.type == "funnel":
        fig = go.Figure(go.Funnel(x=df[y], y=df[view.x]))
    else:
        raise ChartSpecError(f"Unhandled single-measure type: {view.type}")

    fig.update_layout(template="plotly_white", margin=dict(l=40, r=20, t=20, b=40))
    if view.type in {"box", "histogram"}:
        y_unit = hints.column_unit_map.get(y, hints.left_y_unit)
        y_label = hints.column_label_map.get(y, y)
        fig.update_traces(hovertemplate=_hover_template(y_unit, y_label))
        fig.update_layout(
            xaxis=_axis_layout(hints.x_unit, hints.x_label or view.x),
            yaxis=_axis_layout(hints.left_y_unit, hints.left_y_label),
        )
    return fig


# ---------- Grouped bar ----------

def _render_grouped_bar(view: ChartView, df: pd.DataFrame, hints: AxisHints) -> go.Figure:
    if not view.y or len(view.y) < 2:
        raise ChartSpecError("grouped_bar requires 2+ Y measures.")
    long_df = df.melt(
        id_vars=[view.x],
        value_vars=view.y,
        var_name="series",
        value_name="value",
    )
    fig = px.bar(
        long_df,
        x=view.x,
        y="value",
        color="series",
        barmode="group",
    )
    fig.update_traces(hovertemplate=_hover_template(hints.left_y_unit, "Value"))
    fig.update_layout(
        template="plotly_white",
        margin=dict(l=40, r=20, t=20, b=40),
        xaxis=_axis_layout(hints.x_unit, hints.x_label or view.x),
        yaxis=_axis_layout(hints.left_y_unit, hints.left_y_label),
    )
    return fig


# ---------- Horizontal bar ----------

def _render_horizontal_bar(view: ChartView, df: pd.DataFrame, hints: AxisHints) -> go.Figure:
    y = view.y[0]
    sorted_df = df.sort_values(by=y, ascending=True)
    fig = px.bar(
        sorted_df,
        x=y,
        y=view.x,
        color=view.color,
        orientation="h",
    )
    y_unit = hints.column_unit_map.get(y, hints.left_y_unit)
    y_label = hints.column_label_map.get(y, y)
    fig.update_traces(hovertemplate=f"{y_label}: " + _value_format(y_unit) + "<extra></extra>")
    fig.update_layout(
        template="plotly_white",
        margin=dict(l=40, r=20, t=20, b=40),
        xaxis=_axis_layout(hints.left_y_unit, hints.left_y_label or y),
        yaxis=_axis_layout(hints.x_unit, hints.x_label or view.x),
    )
    return fig


# ---------- Axis + hover helpers ----------

def _axis_layout(unit: str, label: str, **extra) -> dict:
    base: dict = {"title": label, **extra}
    if unit in CURRENCY_SYMBOLS:
        base["tickprefix"] = CURRENCY_SYMBOLS[unit]
        base["separatethousands"] = True
    elif unit == "pct":
        base["ticksuffix"] = "%"
    elif unit == "count":
        base["separatethousands"] = True
    elif unit == "hours":
        base["ticksuffix"] = "h"
    elif unit == "days":
        base["ticksuffix"] = "d"
    elif unit == "date":
        base["type"] = "date"
    return base


def _hover_template(unit: str, label: str) -> str:
    val = _value_format(unit)
    return f"%{{x}}<br>{label}: {val}<extra></extra>"


def _value_format(unit: str) -> str:
    if unit in CURRENCY_SYMBOLS:
        return f"{CURRENCY_SYMBOLS[unit]}%{{y:,.0f}}"
    if unit == "pct":
        return "%{y:.1f}%"
    if unit == "count":
        return "%{y:,}"
    if unit == "hours":
        return "%{y:.1f}h"
    if unit == "days":
        return "%{y:.1f}d"
    return "%{y}"
