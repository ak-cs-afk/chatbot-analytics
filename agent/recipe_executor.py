# agent/recipe_executor.py
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from agent.recipe import (
    CustomPythonOp,
    DeriveOp,
    FilterOp,
    GroupbyOp,
    JoinOp,
    Op,
    Recipe,
    SortOp,
    TimeBucketOp,
    TopNOp,
    ALLOWED_DERIVE_FUNCS,
)
from agent.sandbox import run_user_code, SandboxError
from charts.renderer import ChartSpecError, spec_to_figure
from features.loader import Feature


class RecipeExecutionError(RuntimeError):
    """Raised when a recipe cannot be executed against the current features data."""


@dataclass
class ExecutionResult:
    df: pd.DataFrame
    figure: go.Figure | None
    stats: dict[str, dict[str, float]]
    recipe_text: str
    sources_used: list[dict]   # [{"id": ..., "name": ...}, ...]


# ---------- Public entry ----------

def execute(recipe: Recipe, features: dict[str, Feature]) -> ExecutionResult:
    sources_used = []
    for fid in recipe.sources:
        if fid not in features:
            raise RecipeExecutionError(f"Source feature '{fid}' not found.")
        sources_used.append({"id": fid, "name": features[fid].name})

    df = _feature_to_df(features[recipe.sources[0]])

    for idx, op in enumerate(recipe.ops):
        try:
            df = _apply_op(op, df, features)
        except RecipeExecutionError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise RecipeExecutionError(
                f"Op #{idx + 1} ({op.type}) failed: {type(exc).__name__}: {exc}"
            ) from exc

    figure: go.Figure | None = None
    if recipe.chart is not None:
        chart_spec = dict(recipe.chart)
        if recipe.is_direct():
            # Naming policy: direct charts use the feature's canonical name.
            chart_spec["title"] = features[recipe.sources[0]].name
        try:
            figure = spec_to_figure(chart_spec, _df_to_columnar(df))
        except ChartSpecError as exc:
            raise RecipeExecutionError(f"Chart spec invalid: {exc}") from exc

    stats = _compute_stats(df, recipe.stats)
    recipe_text = _humanize(recipe, sources_used)

    return ExecutionResult(
        df=df, figure=figure, stats=stats, recipe_text=recipe_text, sources_used=sources_used
    )


# ---------- Op dispatch ----------

def _apply_op(op: Op, df: pd.DataFrame, features: dict[str, Feature]) -> pd.DataFrame:
    if isinstance(op, FilterOp):
        return _apply_filter(op, df)
    if isinstance(op, GroupbyOp):
        return _apply_groupby(op, df)
    if isinstance(op, JoinOp):
        return _apply_join(op, df, features)
    if isinstance(op, DeriveOp):
        return _apply_derive(op, df)
    if isinstance(op, SortOp):
        _require_column(df, op.by, "sort.by")
        return df.sort_values(by=op.by, ascending=(op.order == "asc")).reset_index(drop=True)
    if isinstance(op, TopNOp):
        _require_column(df, op.by, "top_n.by")
        return df.sort_values(by=op.by, ascending=False).head(op.n).reset_index(drop=True)
    if isinstance(op, TimeBucketOp):
        return _apply_time_bucket(op, df)
    if isinstance(op, CustomPythonOp):
        try:
            return run_user_code(op.code, df)
        except SandboxError as exc:
            raise RecipeExecutionError(f"custom_python: {exc}") from exc
    raise RecipeExecutionError(f"Unhandled op type: {type(op).__name__}")


def _apply_filter(op: FilterOp, df: pd.DataFrame) -> pd.DataFrame:
    _require_column(df, op.column, "filter.column")
    series = df[op.column]
    if op.op == "==":
        mask = series == op.value
    elif op.op == "!=":
        mask = series != op.value
    elif op.op == "<":
        mask = series < op.value
    elif op.op == "<=":
        mask = series <= op.value
    elif op.op == ">":
        mask = series > op.value
    elif op.op == ">=":
        mask = series >= op.value
    elif op.op == "in":
        if not isinstance(op.value, list):
            raise RecipeExecutionError("filter.value for 'in' must be a list.")
        mask = series.isin(op.value)
    elif op.op == "between":
        if not (isinstance(op.value, list) and len(op.value) == 2):
            raise RecipeExecutionError("filter.value for 'between' must be a 2-element list [lo, hi].")
        mask = (series >= op.value[0]) & (series <= op.value[1])
    else:
        raise RecipeExecutionError(f"Unknown filter.op: {op.op}")
    return df[mask].reset_index(drop=True)


def _apply_groupby(op: GroupbyOp, df: pd.DataFrame) -> pd.DataFrame:
    for col in op.by:
        _require_column(df, col, "groupby.by")
    for col in op.agg:
        _require_column(df, col, "groupby.agg")
    grouped = df.groupby(op.by, as_index=False).agg(op.agg)
    return grouped


def _apply_join(op: JoinOp, df: pd.DataFrame, features: dict[str, Feature]) -> pd.DataFrame:
    if op.with_ not in features:
        raise RecipeExecutionError(f"join.with feature '{op.with_}' not found.")
    right = _feature_to_df(features[op.with_])
    for col in op.on:
        _require_column(df, col, "join.on (left)")
        if col not in right.columns:
            raise RecipeExecutionError(
                f"join.on column '{col}' not in right side. Right has: {list(right.columns)}"
            )
    return df.merge(right, on=op.on, how=op.how)


def _apply_derive(op: DeriveOp, df: pd.DataFrame) -> pd.DataFrame:
    safe_globals = {
        "__builtins__": {},
        "abs": abs, "min": min, "max": max, "round": round,
        "log": math.log, "sqrt": math.sqrt,
    }
    local_ns = {col: df[col] for col in df.columns}
    try:
        result = eval(op.expr, safe_globals, local_ns)  # noqa: S307 - validated by recipe.py
    except NameError as exc:
        raise RecipeExecutionError(
            f"derive.expr references unknown name. Available columns: {list(df.columns)}. ({exc})"
        ) from exc
    out = df.copy()
    out[op.name] = result
    return out


def _apply_time_bucket(op: TimeBucketOp, df: pd.DataFrame) -> pd.DataFrame:
    _require_column(df, op.column, "time_bucket.column")
    out = df.copy()
    parsed = pd.to_datetime(out[op.column], errors="coerce")
    if parsed.isna().all():
        raise RecipeExecutionError(
            f"time_bucket: column '{op.column}' could not be parsed as datetime."
        )
    out[op.column] = parsed.dt.to_period(op.freq).dt.start_time
    return out


# ---------- Helpers ----------

def _feature_to_df(feature: Feature) -> pd.DataFrame:
    cols = feature.data_columnar["columns"]
    rows = feature.data_columnar["rows"]
    return pd.DataFrame(rows, columns=cols)


def _df_to_columnar(df: pd.DataFrame) -> dict:
    return {"columns": list(df.columns), "rows": df.values.tolist()}


def _require_column(df: pd.DataFrame, col: str, where: str) -> None:
    if col not in df.columns:
        raise RecipeExecutionError(
            f"{where}: column '{col}' not found. Available: {list(df.columns)}"
        )


def _compute_stats(df: pd.DataFrame, stats: list[str]) -> dict[str, dict[str, float]]:
    if not stats:
        return {}
    numeric = df.select_dtypes(include=[np.number])
    out: dict[str, dict[str, float]] = {}
    for col in numeric.columns:
        col_stats: dict[str, float] = {}
        s = numeric[col]
        for stat in stats:
            if stat == "mean":
                col_stats["mean"] = float(s.mean())
            elif stat == "min":
                col_stats["min"] = float(s.min())
            elif stat == "max":
                col_stats["max"] = float(s.max())
            elif stat == "median":
                col_stats["median"] = float(s.median())
            elif stat == "sum":
                col_stats["sum"] = float(s.sum())
            elif stat == "count":
                col_stats["count"] = int(s.count())
        out[col] = col_stats
    return out


# ---------- Human-readable recipe text ----------

def _humanize(recipe: Recipe, sources_used: list[dict]) -> str:
    if recipe.is_direct():
        return f"Direct view of {sources_used[0]['name']}."
    src_names = ", ".join(s["name"] for s in sources_used)
    parts = [f"Sources: {src_names}."]
    for op in recipe.ops:
        parts.append(_humanize_op(op))
    return " ".join(parts)


def _humanize_op(op: Op) -> str:
    if isinstance(op, FilterOp):
        return f"Filter where {op.column} {op.op} {op.value!r}."
    if isinstance(op, GroupbyOp):
        agg_text = ", ".join(f"{c}:{fn}" for c, fn in op.agg.items())
        return f"Group by {', '.join(op.by)} aggregating {agg_text}."
    if isinstance(op, JoinOp):
        return f"Join with {op.with_} on {', '.join(op.on)} ({op.how})."
    if isinstance(op, DeriveOp):
        return f"Derive {op.name} = {op.expr}."
    if isinstance(op, SortOp):
        return f"Sort by {op.by} {op.order}."
    if isinstance(op, TopNOp):
        return f"Keep top {op.n} by {op.by}."
    if isinstance(op, TimeBucketOp):
        return f"Bucket {op.column} to {op.freq}-period start."
    if isinstance(op, CustomPythonOp):
        return f"Custom transformation ({len(op.code)} chars of Python)."
    return ""