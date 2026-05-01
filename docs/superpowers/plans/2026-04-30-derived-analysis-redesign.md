# Derived-Analysis Redesign (v4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Three response modes (direct, derived, survey). Derived analysis becomes text-first with source feature charts + raw data instead of a synthesized chart. Sidebar nav replaces tabs (drops the CSS hack for chat-input docking).

**Architecture:** The recipe executor auto-classifies recipes into direct vs derived. Direct recipes still produce one chart; derived recipes produce one canonical chart per source feature plus a structured `methodology_steps` list. A new `AnalysisCard` dataclass holds the derived-analysis state. `app.py` drops `st.tabs` for a sidebar `st.radio`.

**Tech Stack:** Python 3.11+, Streamlit, pandas, plotly, openai (Azure), dataclasses.

**User preferences honored:** No TDD (manual smoke tests at the end of each task and a full pass at the end). No git commands.

**Spec:** `docs/superpowers/specs/2026-04-30-derived-analysis-redesign-design.md`.

---

## File Map

**New files:**
- `charts/source_data.py` - CSV download helper.
- `charts/analysis_card.py` - Renders the analysis block + source charts + raw-data expanders.

**Modified files:**
- `agent/recipe_executor.py` - Mode classification, per-source canonical charts, `methodology_steps`.
- `agent/tools.py` - `ChartMeta.mode` + `data_columnar`, new `AnalysisCard`, `analyze` branches per mode.
- `agent/client.py` - `AssistantTurn.analysis_cards`, `_make_step` updated for derived mode.
- `agent/prompts.py` - Three response modes; derived recipes don't author charts.
- `charts/chart_actions.py` - mode-aware (only handles direct charts now).
- `views/conversation.py` - New render loop: prose → analysis cards (with source charts) → standalone charts.
- `app.py` - Sidebar `View` radio at top, no tabs, no CSS injection.

---

## Task 1: CSV download helper

**Files:**
- Create: `charts/source_data.py`

- [ ] **Step 1: Create the helper module**

Write the full file:

```python
# charts/source_data.py
from __future__ import annotations

import io

import pandas as pd


def dataframe_to_csv_bytes(df: pd.DataFrame) -> bytes:
    """Serialize a DataFrame to CSV bytes for st.download_button."""
    buffer = io.StringIO()
    df.to_csv(buffer, index=False)
    return buffer.getvalue().encode("utf-8")
```

- [ ] **Step 2: Smoke check**

Run: `pwsh -Command "python -c 'import pandas as pd; from charts.source_data import dataframe_to_csv_bytes; out = dataframe_to_csv_bytes(pd.DataFrame({\"x\":[1,2],\"y\":[3,4]})); print(out.decode())'"`

Expected: prints

```
x,y
1,3
2,4
```

(No traceback.)

---

## Task 2: Recipe executor - mode + source dataframes + methodology steps

**Files:**
- Modify: `agent/recipe_executor.py` (full replacement)

- [ ] **Step 1: Replace `agent/recipe_executor.py`**

Replace the entire file with:

```python
# agent/recipe_executor.py
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
from charts.renderer import ChartSpecError, spec_to_figure
from features.loader import Feature


class RecipeExecutionError(RuntimeError):
    """Raised when a recipe cannot be executed against the current features data."""


@dataclass
class ExecutionResult:
    df: pd.DataFrame
    mode: str   # "direct" or "derived"
    figure: go.Figure | None
    stats: dict[str, dict[str, float]]
    recipe_text: str
    sources_used: list[dict]
    # Derived-mode extras (empty/None for direct):
    source_dataframes: dict[str, pd.DataFrame] = field(default_factory=dict)
    source_figures: dict[str, go.Figure] = field(default_factory=dict)
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
    figure: go.Figure | None = None
    if recipe.chart is not None:
        chart_spec = dict(recipe.chart)
        # Naming policy: direct charts use the feature's canonical name.
        chart_spec["title"] = features[recipe.sources[0]].name
        try:
            figure = spec_to_figure(chart_spec, _df_to_columnar(df))
        except ChartSpecError as exc:
            raise RecipeExecutionError(f"Chart spec invalid: {exc}") from exc

    stats = _compute_stats(df, recipe.stats)
    recipe_text = f"Direct view of {sources_used[0]['name']}."

    return ExecutionResult(
        df=df,
        mode="direct",
        figure=figure,
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
    # Auto-build a canonical chart for each source feature (ignore recipe.chart).
    source_dfs: dict[str, pd.DataFrame] = {}
    source_figs: dict[str, go.Figure] = {}
    for src in sources_used:
        feature = features[src["id"]]
        source_dfs[src["id"]] = _feature_to_df(feature)
        try:
            source_figs[src["id"]] = _build_canonical_chart(feature)
        except ChartSpecError as exc:
            raise RecipeExecutionError(
                f"Could not build canonical chart for {src['id']} ({src['name']}): {exc}"
            ) from exc

    stats = _compute_stats(df, recipe.stats)
    methodology_steps = _generate_methodology_steps(recipe, sources_used, execution_trace, df, stats)
    recipe_text = _humanize_derived(recipe, sources_used)

    return ExecutionResult(
        df=df,
        mode="derived",
        figure=None,
        stats=stats,
        recipe_text=recipe_text,
        sources_used=sources_used,
        source_dataframes=source_dfs,
        source_figures=source_figs,
        methodology_steps=methodology_steps,
    )


def _build_canonical_chart(feature: Feature) -> go.Figure:
    """Build a chart for a feature using its catalog hints."""
    spec: dict[str, Any] = {"title": feature.name}
    if feature.suggested_chart:
        spec["type"] = feature.suggested_chart
    else:
        # Reasonable fallback: bar if there's a categorical x and one numeric y.
        spec["type"] = "bar"
    if feature.x_field:
        spec["x"] = feature.x_field
    if feature.y_field:
        spec["y"] = feature.y_field
    if feature.y_fields:
        spec["y_fields"] = list(feature.y_fields)
    return spec_to_figure(spec, feature.data_columnar)


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
```

- [ ] **Step 2: Smoke check (direct mode)**

Run: `pwsh -Command "python -c 'from agent.recipe import Recipe; from agent.recipe_executor import execute; from features.loader import load_features; feats = load_features(); fid = next(iter(feats)); r = Recipe.from_dict({\"sources\":[fid],\"ops\":[],\"chart\":{\"type\":\"line\",\"x\":\"month\",\"y\":\"mrr_usd\"}}); res = execute(r, feats); print(res.mode, res.figure is not None, len(res.source_figures))'"`

Expected: `direct True 0` (no traceback). Substitute valid x/y if your first feature isn't `mrr`-shaped - any direct feature works for this check.

- [ ] **Step 3: Smoke check (derived mode)**

Run: `pwsh -Command "python -c 'from agent.recipe import Recipe; from agent.recipe_executor import execute; from features.loader import load_features; feats = load_features(); fids = list(feats.keys())[:1]; r = Recipe.from_dict({\"sources\":fids,\"ops\":[{\"type\":\"top_n\",\"n\":3,\"by\":next(iter(feats[fids[0]].data_columnar[\"columns\"]))}],\"stats\":[\"count\"]}); res = execute(r, feats); print(res.mode); print(\"steps:\", len(res.methodology_steps)); [print(s) for s in res.methodology_steps]; print(\"source_figs:\", len(res.source_figures))'"`

Expected: `derived` followed by 3+ methodology step lines (Started + Kept top 3 + Computed stats) and `source_figs: 1`.

---

## Task 3: Tools - mode-aware analyze + AnalysisCard

**Files:**
- Modify: `agent/tools.py` (full replacement)

- [ ] **Step 1: Replace `agent/tools.py`**

Replace the entire file with:

```python
# agent/tools.py
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

import plotly.graph_objects as go

from agent.recipe import Recipe, RecipeValidationError
from agent.recipe_executor import RecipeExecutionError, execute
from features.loader import load_features


# ---------- Public types ----------

@dataclass
class ChartMeta:
    chart_id: int
    name: str
    recipe: dict
    figure: go.Figure
    recipe_text: str
    sources_used: list[dict] = field(default_factory=list)
    mode: Literal["direct", "derived_source"] = "direct"
    data_columnar: dict | None = None  # populated for derived_source charts


@dataclass
class AnalysisCard:
    analysis_id: int
    sources_used: list[dict]
    methodology_steps: list[dict]
    recipe: dict
    recipe_text: str
    data_preview: list[dict]
    stats: dict
    source_chart_ids: list[int]
    savable: bool = False


# ---------- Tool schemas (OpenAI function-calling format) ----------

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "peek_feature",
            "description": (
                "Inspect one feature's schema and a few sample rows. Use this BEFORE writing "
                "an analyze recipe so you reference real column names. Errors loudly if the "
                "feature_id is unknown - if that happens, refuse the user's question rather "
                "than guessing."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "feature_id": {
                        "type": "string",
                        "description": "Feature ID from the catalog (e.g. 'F001').",
                    }
                },
                "required": ["feature_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze",
            "description": (
                "Execute a recipe and return either a chart (direct mode: 1 source, 0 ops) "
                "or a text-first analysis with auto-rendered source charts (derived mode: "
                "2+ sources or any ops). For DIRECT recipes you MUST supply a `chart` block. "
                "For DERIVED recipes the `chart` block is IGNORED - source charts are built "
                "automatically from each source feature's catalog hints. Call analyze once "
                "per direct chart you want to surface, or once per derived analysis."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "recipe": {
                        "type": "object",
                        "description": (
                            "Recipe object. Shape: "
                            "{sources:[feature_id,...], ops:[{type, ...}, ...], "
                            "chart:{type, x, y, title, ...} (REQUIRED for direct, "
                            "IGNORED for derived), "
                            "stats:[mean|min|max|median|sum|count] (optional)}. "
                            "Op types: filter, groupby, join, derive, sort, top_n, "
                            "time_bucket, custom_python."
                        ),
                    }
                },
                "required": ["recipe"],
            },
        },
    },
]


# ---------- Public dispatcher ----------

def dispatch(name: str, args: dict, turn) -> dict:
    """Route a tool call. Returns a JSON-serializable dict."""
    try:
        if name == "peek_feature":
            return peek_feature(args.get("feature_id", ""))
        if name == "analyze":
            recipe_dict = args.get("recipe")
            if not isinstance(recipe_dict, dict):
                # Fallback: model may pass recipe fields at the top level.
                recipe_dict = args
            return analyze(recipe_dict, turn)
        return {"ok": False, "error": f"Unknown tool: {name}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"Tool {name} crashed: {exc}"}


# ---------- peek_feature ----------

def peek_feature(feature_id: str) -> dict:
    if not feature_id:
        return {"ok": False, "error": "feature_id is required."}
    catalog = load_features()
    if feature_id not in catalog:
        return {
            "ok": False,
            "error": f"feature_id '{feature_id}' not found.",
            "available": sorted(catalog.keys()),
        }

    feature = catalog[feature_id]
    cols = feature.data_columnar["columns"]
    rows = feature.data_columnar["rows"][:3]
    sample = [{c: row[i] for i, c in enumerate(cols)} for row in rows]
    columns = [{"name": c, "dtype": _infer_dtype(rows, i)} for i, c in enumerate(cols)]

    return {
        "ok": True,
        "feature_id": feature.id,
        "name": feature.name,
        "description": feature.description,
        "columns": columns,
        "sample_rows": sample,
        "row_count": feature.row_count,
    }


def _infer_dtype(rows: list, idx: int) -> str:
    for row in rows:
        v = row[idx]
        if v is None:
            continue
        if isinstance(v, bool):
            return "bool"
        if isinstance(v, int):
            return "int"
        if isinstance(v, float):
            return "float"
        return "str"
    return "unknown"


# ---------- analyze ----------

def analyze(recipe_dict: Any, turn) -> dict:
    if not isinstance(recipe_dict, dict):
        return {"ok": False, "error": "analyze.recipe must be an object."}

    try:
        recipe = Recipe.from_dict(recipe_dict)
    except RecipeValidationError as exc:
        return {"ok": False, "error": f"Recipe validation failed: {exc}"}

    catalog = load_features()
    try:
        result = execute(recipe, catalog)
    except RecipeExecutionError as exc:
        return {"ok": False, "error": str(exc)}

    if result.mode == "direct":
        return _emit_direct(recipe, result, turn, catalog)
    return _emit_derived(recipe, result, turn)


def _emit_direct(recipe: Recipe, result, turn, catalog: dict) -> dict:
    name = catalog[recipe.sources[0]].name
    chart_id = len(turn.charts)

    if result.figure is not None:
        turn.charts.append(
            ChartMeta(
                chart_id=chart_id,
                name=name,
                recipe=recipe.to_dict(),
                figure=result.figure,
                recipe_text=result.recipe_text,
                sources_used=result.sources_used,
                mode="direct",
                data_columnar=None,
            )
        )

    preview = result.df.head(5).to_dict(orient="records")
    return {
        "ok": True,
        "mode": "direct",
        "chart_id": chart_id if result.figure is not None else None,
        "name": name,
        "data_preview": preview,
        "stats": result.stats,
        "recipe_text": result.recipe_text,
        "sources_used": result.sources_used,
    }


def _emit_derived(recipe: Recipe, result, turn) -> dict:
    # One ChartMeta per source feature, marked as derived_source (no Save button).
    source_chart_ids: list[int] = []
    source_charts_payload: list[dict] = []
    for src in result.sources_used:
        chart_id = len(turn.charts)
        figure = result.source_figures.get(src["id"])
        if figure is None:
            continue
        feature_columnar = {
            "columns": list(result.source_dataframes[src["id"]].columns),
            "rows": result.source_dataframes[src["id"]].values.tolist(),
        }
        turn.charts.append(
            ChartMeta(
                chart_id=chart_id,
                name=src["name"],
                recipe=recipe.to_dict(),  # shared with the analysis card
                figure=figure,
                recipe_text=result.recipe_text,
                sources_used=result.sources_used,
                mode="derived_source",
                data_columnar=feature_columnar,
            )
        )
        source_chart_ids.append(chart_id)
        source_charts_payload.append({
            "feature_id": src["id"],
            "chart_id": chart_id,
            "name": src["name"],
        })

    analysis_id = len(turn.analysis_cards)
    preview = result.df.head(5).to_dict(orient="records")
    card = AnalysisCard(
        analysis_id=analysis_id,
        sources_used=result.sources_used,
        methodology_steps=result.methodology_steps,
        recipe=recipe.to_dict(),
        recipe_text=result.recipe_text,
        data_preview=preview,
        stats=result.stats,
        source_chart_ids=source_chart_ids,
        savable=False,
    )
    turn.analysis_cards.append(card)

    return {
        "ok": True,
        "mode": "derived",
        "analysis_id": analysis_id,
        "data_preview": preview,
        "stats": result.stats,
        "recipe_text": result.recipe_text,
        "sources_used": result.sources_used,
        "source_charts": source_charts_payload,
        "methodology_steps": result.methodology_steps,
    }


# ---------- Argument parser (kept from v1, used by client.py) ----------

def parse_tool_arguments(raw: str | dict | None) -> dict:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}
```

- [ ] **Step 2: Smoke check (direct + derived dispatch)**

Run:

```
pwsh -Command "python -c 'from types import SimpleNamespace; from agent.tools import analyze; from features.loader import load_features; feats = load_features(); fid = next(iter(feats)); turn = SimpleNamespace(charts=[], analysis_cards=[]); r = analyze({\"sources\":[fid],\"ops\":[],\"chart\":{\"type\":\"line\",\"x\":\"month\",\"y\":\"mrr_usd\"}}, turn); print(r[\"mode\"], len(turn.charts), len(turn.analysis_cards))'"
```

Expected: `direct 1 0` (or appropriate values for whichever feature was first; chart_spec might fail if x/y don't exist - in that case substitute the right column names from the feature's `x_field`/`y_field`).

Then derived dispatch:

```
pwsh -Command "python -c 'from types import SimpleNamespace; from agent.tools import analyze; from features.loader import load_features; feats = load_features(); fid = next(iter(feats)); turn = SimpleNamespace(charts=[], analysis_cards=[]); r = analyze({\"sources\":[fid],\"ops\":[{\"type\":\"top_n\",\"n\":2,\"by\":feats[fid].data_columnar[\"columns\"][0]}]}, turn); print(r[\"mode\"], len(turn.charts), len(turn.analysis_cards), len(r[\"methodology_steps\"]))'"
```

Expected: `derived 1 1 N` where N is at least 2 (started + top_n step).

---

## Task 4: Client - AssistantTurn analysis_cards + reasoning step

**Files:**
- Modify: `agent/client.py:28-34` (AssistantTurn) and `agent/client.py:_make_step`

- [ ] **Step 1: Add `analysis_cards` to `AssistantTurn`**

Open `agent/client.py`. Replace the `AssistantTurn` dataclass:

```python
@dataclass
class AssistantTurn:
    text: str = ""
    charts: list[ChartMeta] = field(default_factory=list)
    progress: list[str] = field(default_factory=list)
    reasoning_steps: list[dict] = field(default_factory=list)
    analysis_cards: list = field(default_factory=list)  # list[AnalysisCard]
    truncated: bool = False
    error: str | None = None
```

Note: imported as `list` rather than `list[AnalysisCard]` to avoid a circular import with the new AnalysisCard type. We add the import next.

- [ ] **Step 2: Import `AnalysisCard`**

Find the line in `agent/client.py`:

```python
from agent.tools import TOOLS, ChartMeta, dispatch, parse_tool_arguments
```

Replace with:

```python
from agent.tools import TOOLS, AnalysisCard, ChartMeta, dispatch, parse_tool_arguments
```

Then update the `AssistantTurn` field annotation:

```python
    analysis_cards: list[AnalysisCard] = field(default_factory=list)
```

- [ ] **Step 3: Update `_make_step` for derived mode**

In `agent/client.py`, replace the `_make_step` function with:

```python
def _make_step(name: str, args: dict, result: dict, ok: bool) -> dict:
    """Build a human-readable reasoning step from a tool call + its result."""
    if name == "peek_feature":
        fid = args.get("feature_id", "?")
        if ok:
            cols = [c["name"] for c in result.get("columns", [])]
            detail = f"Found `{result.get('name', fid)}` with columns: {cols}"
        else:
            detail = result.get("error", "Unknown error")
        return {"tool": "peek_feature", "label": f"Inspected `{fid}`", "ok": ok, "detail": detail}

    if name == "analyze":
        recipe = args.get("recipe") if isinstance(args.get("recipe"), dict) else args
        sources = recipe.get("sources", []) if isinstance(recipe, dict) else []
        ops = recipe.get("ops", []) if isinstance(recipe, dict) else []
        op_types = [o.get("type", "?") for o in ops] if ops else []
        if ok:
            mode = result.get("mode", "?")
            if mode == "direct":
                name_out = result.get("name", "chart")
                detail = (
                    f"Mode: direct. Recipe: sources={sources}, ops=none.\n"
                    f"Result: chart `{name_out}`."
                )
                label = f"Analyzed (direct) -> `{name_out}`"
            elif mode == "derived":
                src_count = len(result.get("sources_used", []))
                step_count = len(result.get("methodology_steps", []))
                detail = (
                    f"Mode: derived. Sources: {sources}. Ops: {op_types}.\n"
                    f"Generated {step_count} methodology step(s) across {src_count} source chart(s)."
                )
                label = f"Analyzed (derived, {src_count} sources)"
            else:
                detail = f"Recipe: sources={sources}, ops={op_types or 'none'}"
                label = "Analyzed"
        else:
            detail = result.get("error", "Unknown error")
            label = f"Analyze failed (sources={sources})"
        return {"tool": "analyze", "label": label, "ok": ok, "detail": detail}

    return {
        "tool": name,
        "label": f"Called `{name}`",
        "ok": ok,
        "detail": result.get("error", "") if not ok else "",
    }
```

- [ ] **Step 4: Smoke check**

Run: `pwsh -Command "python -c 'from agent.client import AssistantTurn, AnalyticsAgent; t = AssistantTurn(); print(type(t.analysis_cards).__name__, t.analysis_cards == [])'"`

Expected: `list True`. No traceback.

---

## Task 5: System prompt - three response modes

**Files:**
- Modify: `agent/prompts.py` (full replacement)

- [ ] **Step 1: Replace `agent/prompts.py`**

Replace the entire file with:

```python
# agent/prompts.py
from __future__ import annotations

from features.catalog import build_catalog_text


SYSTEM_PROMPT_TEMPLATE = """You are an analytics assistant for a SaaS business. The user has a fixed catalog of pre-computed business metrics. Your job: answer their questions with professional, clearly-explained insights using ONLY the catalog below. Never fabricate data.

## Tools

You have two tools:

1. `peek_feature(feature_id)` - Inspect a feature's schema and sample rows BEFORE writing a recipe. Use whenever you are not certain of a feature's column names. Errors if the feature_id is unknown.

2. `analyze(recipe)` - Execute a recipe. The mode is determined by the recipe shape:
   - **Direct mode** = 1 source AND 0 ops. You MUST supply a `chart` block. Returns one chart card.
   - **Derived mode** = 2+ sources OR any ops. The `chart` block is IGNORED if you supply one - source charts are auto-built from each source feature. Returns a text-first analysis (insight + visible methodology bullets) with one canonical chart per source feature below it.

You can call `analyze` multiple times in one turn. For SURVEY questions ("key metrics this period", "executive overview"), call `analyze` in DIRECT mode N times - one per feature you want to surface as a chart card. Do NOT use derived mode for surveys.

## Recipe shape

```
{
  "sources": ["feature_id_1", "feature_id_2", ...],
  "ops": [
    {"type": "filter", "column": "...", "op": "==|!=|<|<=|>|>=|in|between", "value": ...},
    {"type": "groupby", "by": ["col1", ...], "agg": {"col": "sum|mean|median|min|max|count|nunique"}},
    {"type": "join", "with": "feature_id", "on": "col" or ["col1","col2"], "how": "inner|left"},
    {"type": "derive", "name": "new_col", "expr": "col_a + col_b"},
    {"type": "sort", "by": "col", "order": "asc|desc"},
    {"type": "top_n", "n": 5, "by": "col"},
    {"type": "time_bucket", "column": "date_col", "freq": "D|W|M|Q|Y"},
    {"type": "custom_python", "code": "df = df.assign(...)"}
  ],
  "chart": {"type": "...", "x": "...", "y": "...", "title": "..."},
  "stats": ["mean", "min", "max", "median", "sum", "count"]
}
```

`derive.expr` may use: column names, `+ - * / // % ** ()`, numeric literals, and the functions `abs`, `min`, `max`, `round`, `log`, `sqrt`. No attribute access, no imports.

`custom_python.code` is the escape hatch when declarative ops can't express the transformation. The code receives `df`, `pd`, `np` and must end with `df = ...` (a pandas DataFrame). No imports. Cap: 2000 chars.

## Naming policy (STRICTLY enforced)

- **Direct chart** = recipe has exactly one source AND empty ops. The chart's name is set automatically to the feature's canonical name. Do NOT set `chart.title` for direct charts; if you do, it will be overridden.
- **Derived analysis** = anything else. Do NOT supply a `chart` block; if you do, it is ignored. The UI auto-renders one canonical chart per source feature.

## Refusal vs partial-match policy

If the user asks about a metric that is NOT in the catalog at all (no related feature exists), reply only with:

> "I don't have data on **{topic}**. Available metrics: {list of feature_names}. Want to ask about one of those?"

If a feature IS related but has different granularity or framing (e.g. user asks "per month" but the feature is "per category"), use the feature anyway. Compute what is available and add a clearly-separated note.

**Note formatting:** A caveat about a granularity mismatch, data limitation, or assumption MUST appear on its own line, separated from surrounding text by a blank line, prefixed with `> Note:` (a blockquote). Do NOT bury the note inside a regular sentence.

## Response format - DIRECT mode

After a successful direct `analyze` call:

1. Write 2-5 sentences of business insight, citing values ONLY from `data_preview` or `stats`.
2. End with: "Direct view of {feature_name}."
3. The chart auto-renders below.

Example:

User: "Show me MRR over time."
You call: `analyze({'sources': ['F001'], 'ops': [], 'chart': {'type': 'line', 'x': 'month', 'y': 'mrr_usd'}})`
You reply:
'MRR grew from $X in {start_month} to $Y in {end_month} - a Z% lift over the period, with the steepest acceleration in {month}.

Direct view of Monthly Recurring Revenue.'

## Response format - DERIVED mode

After a successful derived `analyze` call:

1. Write 1-2 paragraphs (4-8 sentences) of detailed business insight that helps the user understand the metric and what is notable in the numbers. Cite values ONLY from `data_preview` or `stats`.
2. (Optional) Add a `> Note:` blockquote on its own line if the data has a granularity / scope caveat. Skip if not needed.
3. STOP. Do NOT write a "Computed by …" line - the UI auto-renders a visible Methodology section using `methodology_steps` from the tool response.

The UI will render BELOW your text:
- A visible Methodology block (sources, numbered steps, result line, recipe expander).
- One canonical chart per source feature, with a "Raw data" expander on each.

Example:

User: "Compare MRR growth and churn over time."
You call: `analyze({'sources': ['F001', 'F002'], 'ops': [{'type': 'join', 'with': 'F002', 'on': 'month'}, {'type': 'derive', 'name': 'net_growth_pct', 'expr': '(mrr_change_pct - churn_rate_pct)'}, {'type': 'sort', 'by': 'month'}], 'stats': ['mean', 'min', 'max']})`
You reply:
'Net growth has stayed positive across the period, averaging X% with a range of Y% to Z%. The strongest month was {month} (X%); the weakest was {month} (Y%) where churn briefly outpaced gross MRR change.

Net growth here is the difference between gross MRR change and customer churn - it isolates organic expansion from raw movement, so a positive number means the business is genuinely growing rather than just acquiring more revenue while losing more customers.'

(Notice: NO "Computed by …" trailer. The methodology renders deterministically from the tool response.)

## Response format - SURVEY mode (multiple direct analyzes)

For questions like "key metrics this period" or "executive overview":

1. Pick 4-5 features spanning categories (Revenue, Retention, Engagement, Acquisition, Customer Success).
2. Call `analyze` in DIRECT mode for each one, one call per feature.
3. Write 2-3 sentences of overview prose summarizing what the user is about to see.
4. The UI renders all the chart cards in a 2-up grid below your prose.

Example:

User: "What are our key metrics this period?"
You call analyze 5 times (DIRECT mode), one per feature you select.
You reply:
'Across the SaaS health metrics: revenue is trending up, churn is stable in the low single digits, and engagement (DAU) has been climbing month-over-month. Customer satisfaction (NPS) sits in the healthy range. Below is each metric in detail.'

(No analysis card. Each chart card has its own collapsed "How this was calculated" expander.)

## No-fabrication rule

Cite numbers ONLY from `data_preview` or `stats` in the tool response. If a value you want to cite is not in the response, run another `analyze` to fetch it (e.g. with a filter), or omit the claim. Never invent values.

## Tool-use loop

- If you are confident of a feature's columns from the catalog below, you may go straight to `analyze`.
- If a recipe fails validation or execution, the error message names the failing op and column. Correct the recipe and call `analyze` again.
- Budget: 8 tool iterations per user turn.

## Available features (catalog)

{catalog_text}
"""


def build_system_prompt() -> str:
    # Use replace() instead of .format() so the literal `{` / `}` in the
    # recipe-shape examples above are not interpreted as format placeholders.
    return SYSTEM_PROMPT_TEMPLATE.replace("{catalog_text}", build_catalog_text())
```

- [ ] **Step 2: Smoke check**

Run: `pwsh -Command "python -c 'from agent.prompts import build_system_prompt; p = build_system_prompt(); print(len(p), \"DERIVED\" in p, \"SURVEY\" in p)'"`

Expected: a length around 6000-9000 and `True True`.

---

## Task 6: Analysis card UI module

**Files:**
- Create: `charts/analysis_card.py`

- [ ] **Step 1: Create the module**

Write the full file:

```python
# charts/analysis_card.py
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from agent.tools import AnalysisCard, ChartMeta
from charts.source_data import dataframe_to_csv_bytes


def render_analysis_card(
    card: AnalysisCard,
    source_charts: list[ChartMeta],
    message_index: int,
) -> None:
    """Render the analysis block (methodology + recipe expander) and its source charts."""
    # ---- Methodology block (always visible) ----
    container = st.container(border=True)
    with container:
        st.markdown("**Methodology**")

        sources_line = ", ".join(
            f"`{src['id']}` ({src['name']})" for src in card.sources_used
        )
        st.markdown(f"Sources: {sources_line}")

        for step in card.methodology_steps:
            st.markdown(f"{step['step']}. {step['text']}")

        with st.expander("View recipe (technical)", expanded=False):
            st.code(json.dumps(card.recipe, indent=2), language="json")

    # ---- Source charts grid (no Save button) ----
    if not source_charts:
        return

    if len(source_charts) >= 2:
        cols = st.columns(2)
        for i, cm in enumerate(source_charts):
            with cols[i % 2]:
                _render_source_chart(cm, message_index, card.analysis_id)
    else:
        _render_source_chart(source_charts[0], message_index, card.analysis_id)


def _render_source_chart(cm: ChartMeta, message_index: int, analysis_id: int) -> None:
    sub = st.container(border=True)
    with sub:
        st.markdown(f"**{cm.name}**")
        chart_key = f"src_fig_{message_index}_{analysis_id}_{cm.chart_id}"
        st.plotly_chart(cm.figure, use_container_width=True, key=chart_key)

        with st.expander("Raw data", expanded=False):
            if cm.data_columnar is None:
                st.info("Raw data not attached for this chart.")
            else:
                df = pd.DataFrame(
                    cm.data_columnar["rows"],
                    columns=cm.data_columnar["columns"],
                )
                st.dataframe(df, height=300, use_container_width=True)
                csv_bytes = dataframe_to_csv_bytes(df)
                download_key = f"src_csv_{message_index}_{analysis_id}_{cm.chart_id}"
                st.download_button(
                    label="Download CSV",
                    data=csv_bytes,
                    file_name=f"{cm.name.replace(' ', '_')}.csv",
                    mime="text/csv",
                    key=download_key,
                    use_container_width=True,
                )
```

- [ ] **Step 2: Smoke check (import only)**

Run: `pwsh -Command "python -c 'from charts.analysis_card import render_analysis_card; print(\"ok\")'"` → `ok`.

---

## Task 7: Chart actions - mode-aware (only handles direct now)

**Files:**
- Modify: `charts/chart_actions.py` (full replacement)

- [ ] **Step 1: Replace `charts/chart_actions.py`**

Replace the entire file with:

```python
# charts/chart_actions.py
from __future__ import annotations

import json
from typing import Callable

import streamlit as st

from agent.tools import ChartMeta


def render_chart_with_actions(
    chart_meta: ChartMeta,
    message_index: int,
    on_save: Callable[[ChartMeta], None],
    on_rename: Callable[[ChartMeta, str], None],
    saved_keys: set[str],
    recipe_hash_fn: Callable[[dict], str],
) -> None:
    """Render a SAVABLE direct chart card.

    Source charts under derived analyses are rendered by charts/analysis_card.py
    and never reach this function.
    """
    if chart_meta.mode != "direct":
        st.warning(
            f"render_chart_with_actions called with non-direct chart "
            f"(mode={chart_meta.mode!r}). This is a bug; please report."
        )
        return

    container = st.container(border=True)
    with container:
        name_key = f"chart_name_{message_index}_{chart_meta.chart_id}"
        save_key = f"chart_save_{message_index}_{chart_meta.chart_id}"
        chart_key = f"chart_fig_{message_index}_{chart_meta.chart_id}"

        new_name = st.text_input(
            "Chart name",
            value=chart_meta.name,
            key=name_key,
            label_visibility="collapsed",
        )
        if new_name != chart_meta.name:
            on_rename(chart_meta, new_name)

        st.plotly_chart(chart_meta.figure, use_container_width=True, key=chart_key)

        with st.expander("How this was calculated", expanded=False):
            if chart_meta.sources_used:
                st.markdown("**Sources:**")
                for src in chart_meta.sources_used:
                    st.markdown(f"- `{src['id']}` - {src['name']}")
            st.markdown(f"**Method:** {chart_meta.recipe_text}")
            st.markdown("**Recipe:**")
            st.code(json.dumps(chart_meta.recipe, indent=2), language="json")

        already_saved = recipe_hash_fn(chart_meta.recipe) in saved_keys
        cols = st.columns([4, 1])
        with cols[1]:
            if already_saved:
                st.button("Saved", key=save_key, disabled=True, use_container_width=True)
            else:
                if st.button("Save to Dashboard", key=save_key, use_container_width=True):
                    on_save(chart_meta)
                    st.rerun()
```

- [ ] **Step 2: Smoke check**

Run: `pwsh -Command "python -c 'from charts.chart_actions import render_chart_with_actions; print(\"ok\")'"` → `ok`.

---

## Task 8: Conversation view - new render loop

**Files:**
- Modify: `views/conversation.py` (full replacement)

- [ ] **Step 1: Replace `views/conversation.py`**

Replace the entire file with:

```python
# views/conversation.py
from __future__ import annotations

import streamlit as st

from agent.client import AnalyticsAgent, AssistantTurn, ProgressUpdate
from agent.recipe import recipe_hash
from agent.tools import AnalysisCard, ChartMeta
from charts.analysis_card import render_analysis_card
from charts.chart_actions import render_chart_with_actions
from dashboard.store import (
    DEFAULT_PATH as SAVED_CHARTS_PATH,
    load_saved_charts,
    save_chart,
)


def render() -> None:
    _init_session_state()
    _render_history()
    _handle_input()


def _init_session_state() -> None:
    if "messages" not in st.session_state:
        # Each message: {"role", "text", "charts", "analysis_cards", "reasoning_steps"}
        st.session_state.messages = []
    saved = load_saved_charts(SAVED_CHARTS_PATH)
    st.session_state.saved_chart_keys = {recipe_hash(sc.recipe) for sc in saved}


def _render_history() -> None:
    for index, msg in enumerate(st.session_state.messages):
        with st.chat_message(msg["role"]):
            steps = msg.get("reasoning_steps") or []
            if steps:
                _render_reasoning_trace(steps)
            if msg.get("text"):
                st.markdown(msg["text"])
            _render_analysis_and_charts(
                analysis_cards=msg.get("analysis_cards") or [],
                charts=msg.get("charts") or [],
                message_index=index,
            )


def _handle_input() -> None:
    user_input = st.chat_input("Ask about your data...")
    if not user_input:
        return

    st.session_state.messages.append(
        {"role": "user", "text": user_input, "charts": [], "analysis_cards": []}
    )
    with st.chat_message("user"):
        st.markdown(user_input)

    agent = AnalyticsAgent()
    history = _to_chat_history(st.session_state.messages[:-1])

    with st.chat_message("assistant"):
        status = st.status("Thinking...", expanded=False)
        final_turn: AssistantTurn | None = None
        try:
            for update in agent.run_streaming(user_input, history):
                if isinstance(update, ProgressUpdate):
                    status.update(label=update.label)
                else:
                    final_turn = update
        except Exception as exc:  # noqa: BLE001
            status.update(label="Failed", state="error")
            st.error(f"Unexpected error: {exc}")
            return

        if final_turn is None:
            status.update(label="No response", state="error")
            return

        status.update(
            label="Done" if not final_turn.error else "Failed",
            state="error" if final_turn.error else "complete",
        )

        if final_turn.reasoning_steps:
            _render_reasoning_trace(final_turn.reasoning_steps)

        if final_turn.text:
            st.markdown(final_turn.text)

        new_index = len(st.session_state.messages)
        _render_analysis_and_charts(
            analysis_cards=final_turn.analysis_cards,
            charts=final_turn.charts,
            message_index=new_index,
        )

    st.session_state.messages.append(
        {
            "role": "assistant",
            "text": final_turn.text,
            "charts": final_turn.charts,
            "analysis_cards": final_turn.analysis_cards,
            "reasoning_steps": final_turn.reasoning_steps,
        }
    )


def _render_reasoning_trace(steps: list[dict]) -> None:
    with st.expander("Reasoning trace", expanded=False):
        for i, step in enumerate(steps):
            ok = step.get("ok", True)
            icon = "[ok]" if ok else "[fail]"
            label = step.get("label", step.get("tool", "?"))
            st.markdown(f"**{i + 1}. {icon} {label}**")
            detail = step.get("detail", "")
            if detail:
                st.caption(detail)


def _render_analysis_and_charts(
    analysis_cards: list[AnalysisCard],
    charts: list[ChartMeta],
    message_index: int,
) -> None:
    """Render analysis cards (with their bundled source charts) first, then standalone direct charts."""
    chart_by_id = {cm.chart_id: cm for cm in charts}
    chart_ids_in_cards: set[int] = set()
    for card in analysis_cards:
        chart_ids_in_cards.update(card.source_chart_ids)
        source_charts = [chart_by_id[cid] for cid in card.source_chart_ids if cid in chart_by_id]
        render_analysis_card(card, source_charts, message_index)

    standalone = [cm for cm in charts if cm.chart_id not in chart_ids_in_cards and cm.mode == "direct"]
    if not standalone:
        return

    if len(standalone) >= 3:
        cols = st.columns(2)
        for i, cm in enumerate(standalone):
            with cols[i % 2]:
                _render_one(cm, message_index)
    else:
        for cm in standalone:
            _render_one(cm, message_index)


def _render_one(cm: ChartMeta, message_index: int) -> None:
    render_chart_with_actions(
        chart_meta=cm,
        message_index=message_index,
        on_save=_on_save,
        on_rename=_on_rename,
        saved_keys=st.session_state.saved_chart_keys,
        recipe_hash_fn=recipe_hash,
    )


def _on_save(cm: ChartMeta) -> None:
    saved = save_chart(name=cm.name, recipe=cm.recipe, path=SAVED_CHARTS_PATH)
    st.session_state.saved_chart_keys.add(recipe_hash(saved.recipe))
    st.toast(f"Saved '{saved.name}' to Dashboard.")


def _on_rename(cm: ChartMeta, new_name: str) -> None:
    cm.name = new_name


def _to_chat_history(messages: list[dict]) -> list[dict]:
    out: list[dict] = []
    for m in messages:
        text = m.get("text") or ""
        if not text:
            continue
        out.append({"role": m["role"], "content": text})
    return out
```

- [ ] **Step 2: Smoke check**

Run: `pwsh -Command "python -c 'from views.conversation import render; print(\"ok\")'"` → `ok`.

---

## Task 9: app.py - sidebar nav, no tabs, no CSS hack

**Files:**
- Modify: `app.py` (full replacement)

- [ ] **Step 1: Replace `app.py`**

Replace the entire file with:

```python
# app.py
from __future__ import annotations

import logging
import os

import streamlit as st
from dotenv import load_dotenv

from features.loader import (
    DEFAULT_PATH as FEATURES_PATH,
    FeaturesValidationError,
    load_features,
    reload_features,
)
from views import conversation, dashboard


load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
)


REQUIRED_ENV_VARS = [
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_DEPLOYMENT",
    "AZURE_OPENAI_API_VERSION",
]


def main() -> None:
    st.set_page_config(
        page_title="Chatbot Analytics",
        page_icon=":bar_chart:",
        layout="wide",
    )
    st.title("Chatbot Analytics")
    st.caption("Chat with your business metrics. Save charts to a persistent dashboard.")

    if not _check_config():
        return
    if not _check_features():
        return

    _render_sidebar()

    active = st.session_state.get("active_view", "Conversation")
    if active == "Conversation":
        conversation.render()
    else:
        dashboard.render()


def _check_config() -> bool:
    missing = [v for v in REQUIRED_ENV_VARS if not os.environ.get(v)]
    if missing:
        st.error(
            "Missing required environment variables. Copy `.env.example` to `.env` and fill in:\n\n"
            + "\n".join(f"- `{v}`" for v in missing)
        )
        return False
    return True


def _check_features() -> bool:
    try:
        load_features()
        return True
    except FeaturesValidationError as exc:
        st.error(
            f"Invalid `{FEATURES_PATH}`: {exc}\n\n"
            "Make sure the file exists and each entry has `feature_id`, `feature_name`, and a non-empty `data` array."
        )
        return False


def _render_sidebar() -> None:
    with st.sidebar:
        st.radio(
            "View",
            options=["Conversation", "Dashboard"],
            index=0,
            key="active_view",
        )
        st.divider()

        st.header("Settings")
        st.text_input(
            "Deployment",
            value=os.environ.get("AZURE_OPENAI_DEPLOYMENT", ""),
            disabled=True,
        )

        if st.button("Reload data", use_container_width=True):
            try:
                reload_features()
                st.toast("Features reloaded from disk.")
            except FeaturesValidationError as exc:
                st.error(f"Reload failed: {exc}")

        if st.button("Clear chat", use_container_width=True):
            st.session_state.messages = []
            st.rerun()

        st.divider()
        st.caption("v3 - recipe-based")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke check**

Run: `pwsh -Command "python -c 'import app; print(\"ok\")'"` → `ok`.

---

## Task 10: Full smoke test pass

Manual session walking through every smoke check from the spec. Run from `D:\chatbot-analytics`.

- [ ] **Step 1: Start the app**

`pwsh -Command "streamlit run app.py"` → open the URL.

- [ ] **Step 2: Navigation regression check**

1. Sidebar shows "View" radio at top with "Conversation" selected. Body shows the chat input docked at the bottom of the viewport. No white strip in dark mode. No CSS overrides needed.
2. Switch radio to "Dashboard". Body switches to dashboard view, chat input is gone. Switch back. History intact.

- [ ] **Step 3: Direct chart mode**

3. "Show me MRR over time."
   - One chart card. Title = canonical feature name (NOT agent-authored).
   - Card has: editable name, plotly chart, "How this was calculated" expander (collapsed), Save button.
   - Prose 2-5 sentences ending with "Direct view of …".
4. "Plot DAU." → same shape with the DAU feature.

- [ ] **Step 4: Derived analysis (single source with ops)**

5. "Top 5 channels by CAC."
   - Prose 1-2 paragraphs of insight. NO "Computed by …" trailer.
   - Visible Methodology block (NOT collapsed) with sources line + numbered steps describing `top_n`.
   - One canonical CAC by Channel chart below the methodology block (no Save button).
   - "Raw data" expander on the source chart shows `st.dataframe` (capped height) + Download CSV button.

- [ ] **Step 5: Derived analysis (multi-source)**

6. "Compare MRR growth and churn rate over time."
   - Insight paragraph(s).
   - Methodology block lists both sources, the join op (with row count), the derive op, and the result.
   - Two canonical source charts in 2-up grid (MRR, Churn Rate). Neither savable.
   - Each source chart has its own Raw-data expander with download CSV.
7. "How does ARPU compare with new customer acquisition over time?" → similar shape with two different sources.

- [ ] **Step 6: Survey mode**

8. "What are our key metrics this period?"
   - Prose 2-3 sentence overview.
   - 4-5 direct chart cards in a 2-up grid (e.g., MRR, Churn, DAU, ARPU, NPS).
   - Each chart card individually savable.
   - No analysis card.
9. "Give me an executive overview of the SaaS metrics." → same shape.

- [ ] **Step 7: Refusal**

10. "Show me employee headcount." → text only, refusal template wording starting with "I don't have data on", lists available metrics.
11. "What's the average support tickets per month?" → does NOT refuse. Produces a derived analysis (or direct chart of tickets-by-category) WITH a `> Note:` blockquote on its own line acknowledging the granularity mismatch.

- [ ] **Step 8: Saving**

12. From turn 3, click Save. Toast appears. Switch view to Dashboard via sidebar. Saved chart re-renders.
13. From turn 8, click Save on one of the survey cards. Switch to Dashboard. Two charts saved.
14. Inspect the analysis card from turn 6. Confirm: NO Save button on the analysis block, NO Save button on either source chart.

- [ ] **Step 9: Recipe re-render after data change**

15. Stop the app. Edit `data/features.json`: append a new month to the MRR feature. Restart, click "Reload data" in sidebar, open Dashboard.
    - Saved MRR chart now reflects the new data point.

- [ ] **Step 10: Reasoning trace**

16. Expand "Reasoning trace" above prose for turn 6.
    - For derived: peek_feature calls + analyze call labeled `Analyzed (derived, 2 sources)`.
    - For survey: multiple analyze calls each labeled `Analyzed (direct) -> ...`.

- [ ] **Step 11: Methodology accuracy**

17. After turn 6, scrutinize Methodology bullets. Every number (row counts, mean, range, sum) must match what the executor produced. Cross-check via "View recipe (technical)" expander - the recipe JSON should match the operations described.
18. The agent's prose insight cites only numbers visible in `data_preview` or `stats`. No fabricated values.

- [ ] **Step 12: Custom-python escape hatch**

19. "Show me a 3-month rolling average of MRR."
    - Should produce a derived analysis. Methodology block shows a `Custom Python transformation (N chars)` step.
    - One canonical MRR source chart below.
    - If the agent solves it without `custom_python`, that is also acceptable.

- [ ] **Step 13: Sandbox safety (REPL)**

20. Stop the app. Run:

```
pwsh -Command "python -c 'from types import SimpleNamespace; from agent.tools import analyze; turn = SimpleNamespace(charts=[], analysis_cards=[]); print(analyze({\"sources\":[\"F001\"],\"ops\":[{\"type\":\"custom_python\",\"code\":\"import os\"}]}, turn))'"
```

(Substitute `F001` for whichever feature_id exists in your catalog.)

Expected: `{'ok': False, 'error': 'Recipe validation failed: custom_python.code must not import modules.'}`.

- [ ] **Step 14: Error surfaces**

21. Manually craft an invalid recipe (unknown op type):

```
pwsh -Command "python -c 'from types import SimpleNamespace; from agent.tools import analyze; turn = SimpleNamespace(charts=[], analysis_cards=[]); print(analyze({\"sources\":[\"F001\"],\"ops\":[{\"type\":\"bogus\"}]}, turn))'"
```

Expected: `{'ok': False, 'error': 'Recipe validation failed: Unknown op type: \\'bogus\\''}`.

22. While the app is running, edit `data/features.json` and remove the source feature of an existing saved chart. Click "Reload data" in sidebar. Open Dashboard.
    - That tile shows red "Could not refresh: …" error. Other tiles render normally. Restore `data/features.json` afterward.

- [ ] **Step 15: Chat input docking (regression check)**

23. With a long history (10+ messages), confirm: chat input stays docked at the bottom of the viewport, transparent backdrop, no white strip in dark mode, no overlap with the sidebar on desktop.

- [ ] **Step 16: Sign-off**

If every check above behaved as expected, the implementation is done. If any failed, capture the exact failure (message + which step) and revisit the relevant task before declaring complete.

---

## Self-review notes

**Spec coverage:**
- Architecture (3 modes) → tasks 2, 3, 5.
- Tool surface (mode-aware analyze) → task 3.
- methodology_steps generation → task 2.
- ChartMeta.mode + data_columnar → task 3.
- AnalysisCard dataclass → task 3.
- AssistantTurn.analysis_cards → task 4.
- Reply structure (direct/derived/survey) → tasks 5, 6, 8.
- Visible methodology, "View recipe" expander → task 6.
- Source charts with raw-data expander + CSV download → tasks 1, 6.
- Save gating (direct only) → tasks 6, 7, 8.
- Sidebar nav, no tabs, no CSS hack → task 9.
- Smoke tests → task 10.

**Type consistency check:**
- `ChartMeta.mode: Literal["direct", "derived_source"]` declared in task 3, consumed in tasks 7 and 8.
- `AnalysisCard` defined in task 3, consumed in tasks 4 (forward-imported), 6, and 8.
- `recipe_hash` from `agent.recipe` (existing module, unchanged) used by tasks 7 and 8.
- `save_chart(name, recipe, path)` signature unchanged from v3, consumed by task 8.
- `dataframe_to_csv_bytes(df)` declared in task 1, consumed in task 6.

**No placeholders:** every step contains the actual code or command needed. No "TBD" / "TODO" / "fill in details".
