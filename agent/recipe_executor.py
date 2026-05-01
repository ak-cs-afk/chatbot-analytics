from __future__ import annotations

import math
from dataclasses import dataclass, field
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
)
from agent.sandbox import run_user_code, SandboxError
from charts.renderer import ChartSpecError
from features.loader import Feature


class RecipeExecutionError(RuntimeError):
    """Raised when a recipe cannot be executed against the current features data."""


@dataclass
class ExecutionResult:
    df: pd.DataFrame
    mode: str   # "direct" or "derived"
    stats: dict[str, dict[str, float]]
    recipe_text: str
    sources_used: list[dict]
    # Derived-mode extras (empty for direct):
    source_dataframes: dict[str, pd.DataFrame] = field(default_factory=dict)
    methodology_steps: list[dict] = field(default_factory=list)

# ---------- Public entry ----------

def execute(recipe: Recipe, features: dict[str, Feature]) -> ExecutionResult:
    sources_used = []
    for fid in recipe.sources:
        if fid not in features:
            raise RecipeExecutionError(f"Source feature '{fid}' not found.")
        sources_used.append({"id": fid, "name": features[fid].name})

    df = _feature_to_df(features[recipe.sources[0]])

    # Build a row-count trace as ops execute, so we can describe what each op did.
    execution_trace: list[dict] = [{"op": None, "rows_after": len(df)}]
    for idx, op in enumerate(recipe.ops):
        try:
            df = _apply_op(op, df, features)
        except RecipeExecutionError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise RecipeExecutionError(
                f"Op #{idx + 1} ({op.type}) failed: {type(exc).__name__}: {exc}"
            ) from exc
        execution_trace.append({"op": op, "rows_after": len(df), "columns_after": list(df.columns)})

    if recipe.is_direct():
        return _build_direct_result(recipe, features, df, sources_used)

    return _build_derived_result(recipe, features, df, sources_used, execution_trace)


# ---------- Result builders ----------

def _build_direct_result(
    recipe: Recipe,
    features: dict[str, Feature],
    df: pd.DataFrame,
    sources_used: list[dict],
) -> ExecutionResult:
    stats = _compute_stats(df, recipe.stats)
    recipe_text = f"Direct view of {sources_used[0]['name']}."
    return ExecutionResult(
        df=df,
        mode="direct",
        stats=stats,
        recipe_text=recipe_text,
        sources_used=sources_used,
    )


def _build_derived_result(
    recipe: Recipe,
    features: dict[str, Feature],
    df: pd.DataFrame,
    sources_used: list[dict],
    execution_trace: list[dict],
) -> ExecutionResult:
    source_dfs: dict[str, pd.DataFrame] = {}
    for src in sources_used:
        feature = features[src["id"]]
        source_dfs[src["id"]] = _feature_to_df(feature)

    stats = _compute_stats(df, recipe.stats)
    methodology_steps = _generate_methodology_steps(recipe, sources_used, execution_trace, df, stats)
    recipe_text = _humanize_derived(recipe, sources_used)

    return ExecutionResult(
        df=df,
        mode="derived",
        stats=stats,
        recipe_text=recipe_text,
        sources_used=sources_used,
        source_dataframes=source_dfs,
        methodology_steps=methodology_steps,
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


# ---------- Methodology generation ----------

def _generate_methodology_steps(
    recipe: Recipe,
    sources_used: list[dict],
    trace: list[dict],
    final_df: pd.DataFrame,
    stats: dict[str, dict[str, float]],
) -> list[dict]:
    """Emit a structured list of [{step: int, text: str}] describing each op + the result."""
    steps: list[dict] = []
    step_no = 1

    if len(sources_used) == 1:
        starter = (
            f"Started from `{sources_used[0]['id']}` "
            f"({sources_used[0]['name']}) - {trace[0]['rows_after']} rows."
        )
    else:
        first = sources_used[0]
        starter = (
            f"Started from `{first['id']}` ({first['name']}) - {trace[0]['rows_after']} rows. "
            f"Will combine with {len(sources_used) - 1} other source(s) below."
        )
    steps.append({"step": step_no, "text": starter})
    step_no += 1

    for trace_entry in trace[1:]:
        op = trace_entry["op"]
        rows = trace_entry["rows_after"]
        text = _describe_op(op, rows)
        steps.append({"step": step_no, "text": text})
        step_no += 1

    if stats:
        flat = []
        for col, col_stats in stats.items():
            for stat_name, value in col_stats.items():
                if isinstance(value, float):
                    flat.append(f"{col}.{stat_name}={value:.4g}")
                else:
                    flat.append(f"{col}.{stat_name}={value}")
        if flat:
            steps.append({
                "step": step_no,
                "text": f"Computed stats: {', '.join(flat)}.",
            })
            step_no += 1

    return steps


def _describe_op(op: Op, rows_after: int) -> str:
    if isinstance(op, FilterOp):
        return f"Filter where `{op.column} {op.op} {op.value!r}` -> {rows_after} rows."
    if isinstance(op, GroupbyOp):
        agg_text = ", ".join(f"`{c}`:{fn}" for c, fn in op.agg.items())
        return (
            f"Group by {', '.join(f'`{c}`' for c in op.by)} aggregating {agg_text} "
            f"-> {rows_after} groups."
        )
    if isinstance(op, JoinOp):
        return (
            f"Joined with `{op.with_}` on {', '.join(f'`{c}`' for c in op.on)} "
            f"({op.how} join) -> {rows_after} rows matched."
        )
    if isinstance(op, DeriveOp):
        return f"Derived new column `{op.name}` = `{op.expr}` ({rows_after} rows)."
    if isinstance(op, SortOp):
        return f"Sorted by `{op.by}` {op.order}."
    if isinstance(op, TopNOp):
        return f"Kept top {op.n} rows by `{op.by}`."
    if isinstance(op, TimeBucketOp):
        return f"Bucketed `{op.column}` to {op.freq}-period start."
    if isinstance(op, CustomPythonOp):
        return f"Custom Python transformation ({len(op.code)} chars) -> {rows_after} rows."
    return f"Unknown op -> {rows_after} rows."


def _humanize_derived(recipe: Recipe, sources_used: list[dict]) -> str:
    src_names = ", ".join(s["name"] for s in sources_used)
    op_summaries = []
    for op in recipe.ops:
        if isinstance(op, JoinOp):
            op_summaries.append(f"joined with {op.with_}")
        elif isinstance(op, DeriveOp):
            op_summaries.append(f"derived {op.name}")
        elif isinstance(op, GroupbyOp):
            op_summaries.append(f"grouped by {', '.join(op.by)}")
        elif isinstance(op, FilterOp):
            op_summaries.append(f"filtered on {op.column}")
        elif isinstance(op, TopNOp):
            op_summaries.append(f"kept top {op.n} by {op.by}")
        elif isinstance(op, SortOp):
            op_summaries.append(f"sorted by {op.by}")
        elif isinstance(op, TimeBucketOp):
            op_summaries.append(f"bucketed {op.column} to {op.freq}")
        elif isinstance(op, CustomPythonOp):
            op_summaries.append("ran a custom Python transformation")
    if op_summaries:
        return f"Sources: {src_names}. Steps: {', '.join(op_summaries)}."
    return f"Sources: {src_names}."