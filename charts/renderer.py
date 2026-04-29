from __future__ import annotations

import plotly.graph_objects as go
import plotly.express as px
import pandas as pd


ALLOWED_TYPES = {
    "bar",
    "line",
    "scatter",
    "pie",
    "histogram",
    "box",
    "heatmap",
    "funnel",
    "grouped_bar",
    "horizontal_bar",
}


class ChartSpecError(ValueError):
    """Raised when a chart spec is invalid."""


def spec_to_figure(spec: dict, data: dict) -> go.Figure:
    """Build a Plotly figure from a chart spec and a {columns, rows} data dict."""
    chart_type = spec.get("type")
    if chart_type not in ALLOWED_TYPES:
        raise ChartSpecError(
            f"Unsupported chart type '{chart_type}'. Allowed: {sorted(ALLOWED_TYPES)}"
        )

    df = _to_dataframe(data)
    title = spec.get("title", "")

    if chart_type == "bar":
        _require(spec, ["x", "y"])
        _check_columns(df, [spec["x"], spec["y"]] + _opt(spec, "color"))
        fig = px.bar(df, x=spec["x"], y=spec["y"], color=spec.get("color"), title=title)

    elif chart_type == "line":
        _require(spec, ["x", "y"])
        _check_columns(df, [spec["x"], spec["y"]] + _opt(spec, "color"))
        fig = px.line(df, x=spec["x"], y=spec["y"], color=spec.get("color"), title=title)

    elif chart_type == "scatter":
        _require(spec, ["x", "y"])
        _check_columns(df, [spec["x"], spec["y"]] + _opt(spec, "color"))
        fig = px.scatter(df, x=spec["x"], y=spec["y"], color=spec.get("color"), title=title)

    elif chart_type == "pie":
        _require(spec, ["names", "values"])
        _check_columns(df, [spec["names"], spec["values"]])
        fig = px.pie(df, names=spec["names"], values=spec["values"], title=title)

    elif chart_type == "histogram":
        _require(spec, ["x"])
        _check_columns(df, [spec["x"]] + _opt(spec, "color"))
        fig = px.histogram(df, x=spec["x"], color=spec.get("color"), title=title)

    elif chart_type == "box":
        _require(spec, ["y"])
        cols = [spec["y"]] + _opt(spec, "x") + _opt(spec, "color")
        _check_columns(df, cols)
        fig = px.box(df, y=spec["y"], x=spec.get("x"), color=spec.get("color"), title=title)

    elif chart_type == "heatmap":
        _require(spec, ["x", "y", "z"])
        _check_columns(df, [spec["x"], spec["y"], spec["z"]])
        pivot = df.pivot_table(index=spec["y"], columns=spec["x"], values=spec["z"])
        fig = go.Figure(
            data=go.Heatmap(z=pivot.values, x=list(pivot.columns), y=list(pivot.index))
        )
        if title:
            fig.update_layout(title=title)

    elif chart_type == "funnel":
        _require(spec, ["x", "y"])
        # x = stage label column, y = numeric value column
        _check_columns(df, [spec["x"], spec["y"]])
        fig = go.Figure(go.Funnel(x=df[spec["y"]], y=df[spec["x"]]))
        if title:
            fig.update_layout(title=title)

    elif chart_type == "grouped_bar":
        # spec.y_fields = list of numeric columns to group; x is the categorical axis.
        _require(spec, ["x", "y_fields"])
        y_fields = spec["y_fields"]
        if not isinstance(y_fields, list) or not y_fields:
            raise ChartSpecError("grouped_bar requires non-empty y_fields list.")
        _check_columns(df, [spec["x"], *y_fields])
        long_df = df.melt(
            id_vars=[spec["x"]],
            value_vars=y_fields,
            var_name="series",
            value_name="value",
        )
        fig = px.bar(
            long_df,
            x=spec["x"],
            y="value",
            color="series",
            barmode="group",
            title=title,
        )

    elif chart_type == "horizontal_bar":
        _require(spec, ["x", "y"])
        _check_columns(df, [spec["x"], spec["y"]] + _opt(spec, "color"))
        # x is the numeric axis (length), y is the category axis. Sort descending by x.
        sorted_df = df.sort_values(by=spec["x"], ascending=True)
        fig = px.bar(
            sorted_df,
            x=spec["x"],
            y=spec["y"],
            color=spec.get("color"),
            orientation="h",
            title=title,
        )

    else:
        raise ChartSpecError(f"Unhandled chart type {chart_type}")

    _apply_executive_theme(fig, df.columns.tolist())
    return fig


# ---------- helpers ----------

def _to_dataframe(data: dict) -> pd.DataFrame:
    if not isinstance(data, dict) or "columns" not in data or "rows" not in data:
        raise ChartSpecError("Chart data must be a dict with 'columns' and 'rows' keys.")
    return pd.DataFrame(data["rows"], columns=data["columns"])


def _require(spec: dict, keys: list[str]) -> None:
    missing = [k for k in keys if k not in spec or spec[k] is None]
    if missing:
        raise ChartSpecError(f"Chart spec missing required keys: {missing}")


def _opt(spec: dict, key: str) -> list[str]:
    return [spec[key]] if spec.get(key) else []


def _check_columns(df: pd.DataFrame, cols: list[str]) -> None:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ChartSpecError(
            f"Columns {missing} not in data. Available: {list(df.columns)}"
        )


def _apply_executive_theme(fig: go.Figure, columns: list[str]) -> None:
    fig.update_layout(
        template="plotly_white",
        margin=dict(l=40, r=20, t=50, b=40),
    )
    has_usd = any(c.endswith("_usd") for c in columns)
    has_pct = any(c.endswith("_pct") for c in columns)
    if has_usd:
        fig.update_yaxes(tickprefix="$", separatethousands=True)
    if has_pct:
        # Apply percent formatting to y-axis only when no usd column overrides it.
        if not has_usd:
            fig.update_yaxes(ticksuffix="%")