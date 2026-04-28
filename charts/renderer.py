from __future__ import annotations

import plotly.graph_objects as go
import plotly.express as px
import pandas as pd


ALLOWED_TYPES = {"bar", "line", "scatter", "pie", "histogram", "box", "heatmap"}


class ChartSpecError(ValueError):
    """Raised when a chart spec is invalid."""

def spec_to_figure(spec: dict, data: dict) -> go.Figure:
    """Build a Plotly figure from a chart spec and a {columns, rows} data dict."""

    chart_type = spec.get("type")
    if chart_type not in ALLOWED_TYPES:
        raise ChartSpecError(
            f"Unsupported chart type '{chart_type}'. "
            f"Allowed: {sorted(ALLOWED_TYPES)}"
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
        fig = px.scatter(
            df, x=spec["x"], y=spec["y"], color=spec.get("color"), title=title
        )
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
        fig = px.box(
            df, y=spec["y"], x=spec.get("x"), color=spec.get("color"), title=title
        )
    elif chart_type == "heatmap":
        _require(spec, ["x", "y", "z"])
        _check_columns(df, [spec["x"], spec["y"], spec["z"]])
        pivot = df.pivot_table(index=spec["y"], columns=spec["x"], values=spec["z"])
        fig = go.Figure(
            data=go.Heatmap(
                z=pivot.values, x=list(pivot.columns), y=list(pivot.index)
            )
        )
        if title:
            fig.update_layout(title=title)
    else:
        raise ChartSpecError(f"Unhandled chart type {chart_type}")
    
    fig.update_layout(margin=dict(l=40, r=20, t=50, b=40))
    return fig


def _to_dataframe(data: dict) -> pd.DataFrame:
    if not isinstance(data, dict) or "columns" not in data or "rows" not in data:
        raise ChartSpecError(
            "Chart data must be a dict with 'columns' and 'rows' keys."
        )
    return pd.DataFrame(data["rows"], columns=data["columns"])


def _require(spec: dict, keys: list[str]) -> None:
    missing = [k for k in keys if k not in spec or spec[k] is None]
    if missing:
        raise ChartSpecError(
            f"Chart spec missing required keys: {missing}"
        )
    

def _opt(spec: dict, key: str) -> list[str]:
    return [spec[key]] if spec.get(key) else []


def _check_columns(df: pd.DataFrame, cols: list[str]) -> None:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ChartSpecError(
            f"Columns {missing} not in data. Available: {list(df.columns)}"
        )
