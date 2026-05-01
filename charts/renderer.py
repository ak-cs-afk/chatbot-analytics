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


ALLOWED_TYPES = ALL_TYPES  # back-compat re-export for any consumers


class ChartSpecError(ValueError):
    """Raised when a chart spec is invalid for the given data."""


PALETTE = px.colors.qualitative.Set2


# ---------- Public entry ----------

def render(view: ChartView, df: pd.DataFrame, hints: AxisHints) -> go.Figure:
    if view.type not in ALL_TYPES:
        raise ChartSpecError(
            f"Unsupported chart type '{view.type}'. Allowed: {sorted(ALL_TYPES)}"
        )

    # Validate columns referenced by the view exist in df.
    referenced = [view.x, *view.y]
    if view.color:
        referenced.append(view.color)
    missing = [c for c in referenced if c not in df.columns]
    if missing:
        raise ChartSpecError(
            f"Columns {missing} not in result data. Available: {list(df.columns)}"
        )

    if view.type in MULTI_MEASURE_TYPES:
        return _render_multi_measure(view, df, hints)
    if view.type in GROUPED_TYPES:
        return _render_grouped_bar(view, df, hints)
    if view.type == "horizontal_bar":
        return _render_horizontal_bar(view, df, hints)
    return _render_single_measure(view, df, hints)


# ---------- Multi-measure (line / bar / scatter) ----------

def _render_multi_measure(view: ChartView, df: pd.DataFrame, hints: AxisHints) -> go.Figure:
    fig = go.Figure()
    if view.color and len(view.y) == 1:
        return _render_single_color_grouped(view, df, hints)

    # Determine each Y series' unit so we know which axis to assign.
    from charts.chart_view import _resolve_unit  # type: ignore

    units = []  # We can't import the feature_columns here; rely on hints semantics.
    # The hints already encode left/right unit. We classify each Y by unit equality.
    # Resolve units from view.column_units (overrides) only; the renderer trusts
    # hints for the axis decision and treats remaining columns as left.
    for col in view.y:
        units.append(view.column_units.get(col))

    for i, col in enumerate(view.y):
        # If the explicit override matches the right unit -> right axis.
        # Otherwise: if there's a right axis and we have N=2 measures, second goes right.
        secondary = (
            hints.right_y_unit is not None
            and units[i] == hints.right_y_unit
        )
        if hints.right_y_unit is not None and not any(units):
            secondary = (i == 1)  # fall back to "second measure goes right" when overrides absent

        color = PALETTE[i % len(PALETTE)]
        trace_kwargs = dict(
            x=df[view.x],
            y=df[col],
            name=col,
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
        title=view.title,
        template="plotly_white",
        margin=dict(l=40, r=40, t=50, b=40),
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
    # Single Y measure with a color column: use plotly.express for the convenience.
    y_col = view.y[0]
    if view.type == "line":
        fig = px.line(df, x=view.x, y=y_col, color=view.color, title=view.title)
    elif view.type == "bar":
        fig = px.bar(df, x=view.x, y=y_col, color=view.color, title=view.title)
    elif view.type == "scatter":
        fig = px.scatter(df, x=view.x, y=y_col, color=view.color, title=view.title)
    else:
        raise ChartSpecError(f"Color grouping not supported for type '{view.type}'.")
    fig.update_layout(
        template="plotly_white",
        margin=dict(l=40, r=20, t=50, b=40),
        xaxis=_axis_layout(hints.x_unit, hints.x_label or view.x),
        yaxis=_axis_layout(hints.left_y_unit, hints.left_y_label),
    )
    return fig


# ---------- Single-measure types ----------

def _render_single_measure(view: ChartView, df: pd.DataFrame, hints: AxisHints) -> go.Figure:
    y = view.y[0]
    if view.type == "pie":
        fig = px.pie(df, names=view.x, values=y, title=view.title)
    elif view.type == "histogram":
        fig = px.histogram(df, x=view.x, color=view.color, title=view.title)
    elif view.type == "box":
        fig = px.box(df, y=y, x=view.x, color=view.color, title=view.title)
    elif view.type == "heatmap":
        if view.color is None:
            raise ChartSpecError("heatmap requires color (z) field via view.color.")
        pivot = df.pivot_table(index=y, columns=view.x, values=view.color)
        fig = go.Figure(
            data=go.Heatmap(z=pivot.values, x=list(pivot.columns), y=list(pivot.index))
        )
        fig.update_layout(title=view.title)
    elif view.type == "funnel":
        fig = go.Figure(go.Funnel(x=df[y], y=df[view.x]))
        fig.update_layout(title=view.title)
    else:
        raise ChartSpecError(f"Unhandled single-measure type: {view.type}")

    fig.update_layout(template="plotly_white", margin=dict(l=40, r=20, t=50, b=40))
    if view.type in {"box", "histogram"}:
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
        title=view.title,
    )
    fig.update_layout(
        template="plotly_white",
        margin=dict(l=40, r=20, t=50, b=40),
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
        title=view.title,
    )
    fig.update_layout(
        template="plotly_white",
        margin=dict(l=40, r=20, t=50, b=40),
        xaxis=_axis_layout(hints.left_y_unit, hints.left_y_label or y),  # numeric on x
        yaxis=_axis_layout(hints.x_unit, hints.x_label or view.x),       # categories on y
    )
    return fig


# ---------- Axis layout helper ----------

def _axis_layout(unit: str, label: str, **extra) -> dict:
    base: dict = {"title": label, **extra}
    if unit == "usd":
        base["tickprefix"] = "$"
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
    # number / string -> no formatter
    return base