# Recipe-Based Chart Engine (v3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the per-feature chart pipeline with a recipe-based engine that lets the agent pull from multiple features per query, persist the calculation alongside each saved chart, and refuse cleanly when data is missing.

**Architecture:** Agent's tool surface shrinks to `peek_feature` + `analyze`. Every chart - direct or derived - is the output of a recipe (sources + ops + chart spec). The recipe is what gets persisted; the dashboard re-runs it on every visit against the latest features data. Direct charts (single source, no ops) keep the feature's canonical name; derived charts get an agent-authored title.

**Tech Stack:** Python 3.11+, Streamlit, pandas, plotly, openai (Azure), dataclasses.

**User preferences honored:** No TDD (manual smoke tests at the end of each task and a full pass at the end). No git commands (user runs git separately).

**Spec:** `docs/superpowers/specs/2026-04-30-recipe-based-chart-engine-design.md`.

---

## File Map

**New files:**
- `agent/recipe.py` - Recipe + Op dataclasses, validators, JSON round-trip.
- `agent/sandbox.py` - Restricted exec for `custom_python`.
- `agent/recipe_executor.py` - Recipe → DataFrame → figure + stats + recipe_text.

**Modified files:**
- `agent/tools.py` - Replace tools with `peek_feature` + `analyze`. New `ChartMeta` shape.
- `agent/prompts.py` - Rewritten system prompt: refusal, naming policy, methodology, recipe authoring.
- `agent/client.py` - Progress labels for new tools.
- `dashboard/store.py` - `SavedChart` carries `recipe`. `_recipe_hash` replaces `_spec_hash`. `save_chart` signature changes.
- `charts/chart_actions.py` - Add "How this was calculated" expander.
- `views/conversation.py` - Methodology line render, recipe-hash for saved-keys.
- `views/dashboard.py` - Rebuild every saved chart via `recipe_executor`.

**Deleted files:**
- `data/saved_charts.json` (deleted at task 11; user re-saves manually).

---

## Task 1: Recipe data layer

**Files:**
- Create: `agent/recipe.py`

- [ ] **Step 1: Create the recipe module**

Write the full file:

```python
# agent/recipe.py
from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field
from typing import Any, Literal


# ---------- Op types ----------

@dataclass(frozen=True)
class FilterOp:
    column: str
    op: str  # one of: ==, !=, <, <=, >, >=, in, between
    value: Any
    type: Literal["filter"] = "filter"


@dataclass(frozen=True)
class GroupbyOp:
    by: list[str]
    agg: dict[str, str]  # column -> aggfn (sum/mean/median/min/max/count/nunique)
    type: Literal["groupby"] = "groupby"


@dataclass(frozen=True)
class JoinOp:
    with_: str  # feature_id (renamed from "with" which is a Python keyword)
    on: list[str]
    how: str = "inner"
    type: Literal["join"] = "join"


@dataclass(frozen=True)
class DeriveOp:
    name: str
    expr: str
    type: Literal["derive"] = "derive"


@dataclass(frozen=True)
class SortOp:
    by: str
    order: str = "asc"
    type: Literal["sort"] = "sort"


@dataclass(frozen=True)
class TopNOp:
    n: int
    by: str
    type: Literal["top_n"] = "top_n"


@dataclass(frozen=True)
class TimeBucketOp:
    column: str
    freq: str  # D/W/M/Q/Y
    type: Literal["time_bucket"] = "time_bucket"


@dataclass(frozen=True)
class CustomPythonOp:
    code: str
    type: Literal["custom_python"] = "custom_python"


Op = (
    FilterOp | GroupbyOp | JoinOp | DeriveOp | SortOp | TopNOp | TimeBucketOp | CustomPythonOp
)


# ---------- Recipe ----------

ALLOWED_AGGS = {"sum", "mean", "median", "min", "max", "count", "nunique"}
ALLOWED_FILTER_OPS = {"==", "!=", "<", "<=", ">", ">=", "in", "between"}
ALLOWED_FREQS = {"D", "W", "M", "Q", "Y"}
ALLOWED_JOIN_HOW = {"inner", "left"}
ALLOWED_SORT_ORDER = {"asc", "desc"}
ALLOWED_STATS = {"mean", "min", "max", "median", "sum", "count"}
ALLOWED_DERIVE_FUNCS = {"abs", "min", "max", "round", "log", "sqrt"}
CODE_LENGTH_CAP = 2000


class RecipeValidationError(ValueError):
    """Raised when a recipe fails structural validation."""


@dataclass
class Recipe:
    sources: list[str]
    ops: list[Op] = field(default_factory=list)
    chart: dict | None = None
    stats: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "sources": list(self.sources),
            "ops": [_op_to_dict(op) for op in self.ops],
            "chart": self.chart,
            "stats": list(self.stats),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Recipe":
        if not isinstance(data, dict):
            raise RecipeValidationError("Recipe must be an object.")
        sources = data.get("sources")
        if not isinstance(sources, list) or not sources or not all(isinstance(s, str) for s in sources):
            raise RecipeValidationError("Recipe.sources must be a non-empty list of feature_id strings.")

        ops_raw = data.get("ops", []) or []
        if not isinstance(ops_raw, list):
            raise RecipeValidationError("Recipe.ops must be a list.")
        ops = [_op_from_dict(o) for o in ops_raw]

        chart = data.get("chart")
        if chart is not None:
            if not isinstance(chart, dict):
                raise RecipeValidationError("Recipe.chart must be an object or omitted.")
            chart_type = chart.get("type")
            # Imported lazily to avoid circular dep with renderer at import time.
            from charts.renderer import ALLOWED_TYPES
            if chart_type not in ALLOWED_TYPES:
                raise RecipeValidationError(
                    f"Recipe.chart.type '{chart_type}' not in allowed types: {sorted(ALLOWED_TYPES)}"
                )

        stats = data.get("stats", []) or []
        if not isinstance(stats, list) or any(s not in ALLOWED_STATS for s in stats):
            raise RecipeValidationError(
                f"Recipe.stats must be a list of {sorted(ALLOWED_STATS)}."
            )

        return cls(sources=list(sources), ops=ops, chart=chart, stats=list(stats))

    def is_direct(self) -> bool:
        return len(self.sources) == 1 and not self.ops


# ---------- Op (de)serialization ----------

def _op_from_dict(raw: Any) -> Op:
    if not isinstance(raw, dict):
        raise RecipeValidationError(f"Op must be an object, got {type(raw).__name__}")
    kind = raw.get("type")
    if kind == "filter":
        _require_keys(raw, ["column", "op", "value"], "filter")
        if raw["op"] not in ALLOWED_FILTER_OPS:
            raise RecipeValidationError(f"filter.op '{raw['op']}' not in {sorted(ALLOWED_FILTER_OPS)}")
        return FilterOp(column=raw["column"], op=raw["op"], value=raw["value"])
    if kind == "groupby":
        _require_keys(raw, ["by", "agg"], "groupby")
        if not isinstance(raw["by"], list) or not raw["by"]:
            raise RecipeValidationError("groupby.by must be a non-empty list of columns.")
        if not isinstance(raw["agg"], dict) or not raw["agg"]:
            raise RecipeValidationError("groupby.agg must be a non-empty dict of column: aggfn.")
        for col, fn in raw["agg"].items():
            if fn not in ALLOWED_AGGS:
                raise RecipeValidationError(
                    f"groupby.agg['{col}'] = '{fn}' not in {sorted(ALLOWED_AGGS)}"
                )
        return GroupbyOp(by=list(raw["by"]), agg=dict(raw["agg"]))
    if kind == "join":
        # Accept either "with" (JSON wire form) or "with_" (Python form).
        with_value = raw.get("with", raw.get("with_"))
        if not isinstance(with_value, str) or not with_value:
            raise RecipeValidationError("join.with must be a feature_id string.")
        on_value = raw.get("on")
        if isinstance(on_value, str):
            on_list = [on_value]
        elif isinstance(on_value, list) and all(isinstance(c, str) for c in on_value) and on_value:
            on_list = list(on_value)
        else:
            raise RecipeValidationError("join.on must be a column name or list of column names.")
        how = raw.get("how", "inner")
        if how not in ALLOWED_JOIN_HOW:
            raise RecipeValidationError(f"join.how '{how}' not in {sorted(ALLOWED_JOIN_HOW)}")
        return JoinOp(with_=with_value, on=on_list, how=how)
    if kind == "derive":
        _require_keys(raw, ["name", "expr"], "derive")
        expr = raw["expr"]
        if not isinstance(expr, str) or not expr.strip():
            raise RecipeValidationError("derive.expr must be a non-empty string.")
        _validate_derive_expr(expr)
        return DeriveOp(name=raw["name"], expr=expr)
    if kind == "sort":
        _require_keys(raw, ["by"], "sort")
        order = raw.get("order", "asc")
        if order not in ALLOWED_SORT_ORDER:
            raise RecipeValidationError(f"sort.order '{order}' not in {sorted(ALLOWED_SORT_ORDER)}")
        return SortOp(by=raw["by"], order=order)
    if kind == "top_n":
        _require_keys(raw, ["n", "by"], "top_n")
        n = raw["n"]
        if not isinstance(n, int) or n <= 0:
            raise RecipeValidationError("top_n.n must be a positive integer.")
        return TopNOp(n=n, by=raw["by"])
    if kind == "time_bucket":
        _require_keys(raw, ["column", "freq"], "time_bucket")
        if raw["freq"] not in ALLOWED_FREQS:
            raise RecipeValidationError(f"time_bucket.freq '{raw['freq']}' not in {sorted(ALLOWED_FREQS)}")
        return TimeBucketOp(column=raw["column"], freq=raw["freq"])
    if kind == "custom_python":
        _require_keys(raw, ["code"], "custom_python")
        code = raw["code"]
        if not isinstance(code, str):
            raise RecipeValidationError("custom_python.code must be a string.")
        if len(code) > CODE_LENGTH_CAP:
            raise RecipeValidationError(
                f"custom_python.code exceeds {CODE_LENGTH_CAP}-char cap (got {len(code)})."
            )
        _validate_custom_python(code)
        return CustomPythonOp(code=code)
    raise RecipeValidationError(f"Unknown op type: {kind!r}")


def _op_to_dict(op: Op) -> dict:
    if isinstance(op, JoinOp):
        return {"type": "join", "with": op.with_, "on": op.on, "how": op.how}
    if isinstance(op, FilterOp):
        return {"type": "filter", "column": op.column, "op": op.op, "value": op.value}
    if isinstance(op, GroupbyOp):
        return {"type": "groupby", "by": list(op.by), "agg": dict(op.agg)}
    if isinstance(op, DeriveOp):
        return {"type": "derive", "name": op.name, "expr": op.expr}
    if isinstance(op, SortOp):
        return {"type": "sort", "by": op.by, "order": op.order}
    if isinstance(op, TopNOp):
        return {"type": "top_n", "n": op.n, "by": op.by}
    if isinstance(op, TimeBucketOp):
        return {"type": "time_bucket", "column": op.column, "freq": op.freq}
    if isinstance(op, CustomPythonOp):
        return {"type": "custom_python", "code": op.code}
    raise RecipeValidationError(f"Unhandled op type for serialization: {type(op).__name__}")


def _require_keys(raw: dict, keys: list[str], op_name: str) -> None:
    missing = [k for k in keys if k not in raw]
    if missing:
        raise RecipeValidationError(f"{op_name} missing required keys: {missing}")


# ---------- Expression and code validators ----------

def _validate_derive_expr(expr: str) -> None:
    if "__" in expr:
        raise RecipeValidationError("derive.expr must not contain '__'.")
    if "import" in expr.lower():
        raise RecipeValidationError("derive.expr must not contain 'import'.")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise RecipeValidationError(f"derive.expr parse error: {exc}") from exc
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            raise RecipeValidationError("derive.expr must not use attribute access.")
        if isinstance(node, ast.Call):
            func = node.func
            if not isinstance(func, ast.Name) or func.id not in ALLOWED_DERIVE_FUNCS:
                raise RecipeValidationError(
                    f"derive.expr may only call: {sorted(ALLOWED_DERIVE_FUNCS)}"
                )
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise RecipeValidationError("derive.expr must not contain imports.")


def _validate_custom_python(code: str) -> None:
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        raise RecipeValidationError(f"custom_python.code parse error: {exc}") from exc
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise RecipeValidationError("custom_python.code must not import modules.")


# ---------- Hashing for saved-chart deduplication ----------

def recipe_hash(recipe_dict: dict) -> str:
    import hashlib
    canonical = json.dumps(recipe_dict, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

- [ ] **Step 2: Smoke check (manual REPL)**

From `D:\chatbot-analytics`, run `pwsh -Command "python -c 'from agent.recipe import Recipe; r = Recipe.from_dict({\"sources\":[\"mrr\"],\"ops\":[],\"chart\":{\"type\":\"line\",\"x\":\"month\",\"y\":\"mrr_usd\"}}); print(r.is_direct(), r.to_dict())'"`

Expected: `True {'sources': ['mrr'], 'ops': [], 'chart': {...}, 'stats': []}` (no traceback).

Then: `pwsh -Command "python -c 'from agent.recipe import Recipe; Recipe.from_dict({\"sources\":[\"x\"],\"ops\":[{\"type\":\"derive\",\"name\":\"y\",\"expr\":\"__import__(0)\"}]})'"`

Expected: traceback ending in `RecipeValidationError: derive.expr must not contain '__'.`

---

## Task 2: Sandbox

**Files:**
- Create: `agent/sandbox.py`

- [ ] **Step 1: Create the sandbox module**

Write the full file:

```python
# agent/sandbox.py
from __future__ import annotations

import threading
from typing import Any

import numpy as np
import pandas as pd

from agent.recipe import _validate_custom_python  # reuse AST scan


SAFE_BUILTINS = {
    name: getattr(__builtins__, name) if hasattr(__builtins__, name) else __builtins__[name]
    for name in [
        "len", "range", "sum", "min", "max", "abs", "round", "sorted",
        "enumerate", "zip", "dict", "list", "tuple", "set",
        "str", "int", "float", "bool",
    ]
}

TIMEOUT_SECONDS = 5.0


class SandboxError(RuntimeError):
    """Raised when sandboxed code fails, times out, or returns a bad type."""


def run_user_code(code: str, df_in: pd.DataFrame) -> pd.DataFrame:
    """Execute `code` in a restricted namespace; return its DataFrame result.

    Convention: the code receives `df` (input), `pd`, `np`, and must assign the
    final DataFrame to a variable named `df` (overwriting the input is fine).
    """
    _validate_custom_python(code)

    namespace: dict[str, Any] = {
        "__builtins__": SAFE_BUILTINS,
        "pd": pd,
        "np": np,
        "df": df_in.copy(),
    }
    interrupt = {"fired": False}

    def _trip() -> None:
        interrupt["fired"] = True

    timer = threading.Timer(TIMEOUT_SECONDS, _trip)
    timer.start()
    try:
        try:
            exec(compile(code, "<custom_python>", "exec"), namespace)  # noqa: S102
        except Exception as exc:  # noqa: BLE001
            raise SandboxError(f"{type(exc).__name__}: {exc}") from exc
        if interrupt["fired"]:
            raise SandboxError(
                f"custom_python timed out (best-effort {TIMEOUT_SECONDS}s)."
            )
    finally:
        timer.cancel()

    result = namespace.get("df")
    if not isinstance(result, pd.DataFrame):
        raise SandboxError(
            f"custom_python must leave a pandas.DataFrame in `df` (got {type(result).__name__})."
        )
    return result
```

- [ ] **Step 2: Smoke check**

Run: `pwsh -Command "python -c 'import pandas as pd; from agent.sandbox import run_user_code; out = run_user_code(\"df = df.assign(double=df.x * 2)\", pd.DataFrame({\"x\":[1,2,3]})); print(out.to_dict(orient=\"list\"))'"`

Expected: `{'x': [1, 2, 3], 'double': [2, 4, 6]}`.

Then negative test: `pwsh -Command "python -c 'import pandas as pd; from agent.sandbox import run_user_code; run_user_code(\"import os\", pd.DataFrame())'"`

Expected: traceback ending with `RecipeValidationError: custom_python.code must not import modules.`

---

## Task 3: Recipe executor

**Files:**
- Create: `agent/recipe_executor.py`

- [ ] **Step 1: Create the executor module**

Write the full file:

```python
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
```

- [ ] **Step 2: Smoke check (REPL with real features)**

Run from `D:\chatbot-analytics`:

```
pwsh -Command "python -c 'from agent.recipe import Recipe; from agent.recipe_executor import execute; from features.loader import load_features; feats = load_features(); fid = next(iter(feats)); r = Recipe.from_dict({\"sources\":[fid],\"ops\":[],\"stats\":[\"mean\",\"min\",\"max\"]}); res = execute(r, feats); print(res.recipe_text); print(res.stats)'"
```

Expected: prints "Direct view of <feature name>." plus a stats dict with mean/min/max for each numeric column. No traceback.

---

## Task 4: Tools rewrite

**Files:**
- Modify: `agent/tools.py` (full replacement of body, keep `parse_tool_arguments`)

- [ ] **Step 1: Replace `agent/tools.py` with the new surface**

Open `agent/tools.py` and replace the entire file with:

```python
# agent/tools.py
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import plotly.graph_objects as go

from agent.recipe import Recipe, RecipeValidationError
from agent.recipe_executor import RecipeExecutionError, execute
from features.loader import load_features


# ---------- Public types ----------

@dataclass
class ChartMeta:
    chart_id: int
    name: str
    recipe: dict          # serialized form (Recipe.to_dict())
    figure: go.Figure
    recipe_text: str
    sources_used: list[dict] = field(default_factory=list)


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
                        "description": "Feature ID from the catalog (e.g. 'mrr', 'F001').",
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
                "Execute a recipe against the features catalog and return a chart, stats, and "
                "a data preview. The recipe is the persisted unit of work - it describes the "
                "sources, the transformations, and (optionally) the chart spec. Direct charts "
                "(one source, no ops) keep the feature's canonical name; derived charts "
                "require an agent-authored chart.title."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "recipe": {
                        "type": "object",
                        "description": (
                            "Recipe object. Shape: "
                            "{sources:[feature_id,...], ops:[{type, ...}, ...], "
                            "chart:{type, x, y, title, ...} (optional), "
                            "stats:[mean|min|max|median|sum|count] (optional)}. "
                            "Op types: filter, groupby, join, derive, sort, top_n, time_bucket, "
                            "custom_python. See system prompt for examples."
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
            return analyze(args.get("recipe"), turn)
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

    chart_id = len(turn.charts)
    name = (
        (recipe.chart or {}).get("title")
        or (catalog[recipe.sources[0]].name if recipe.is_direct() else "Untitled chart")
    )

    if result.figure is not None:
        turn.charts.append(
            ChartMeta(
                chart_id=chart_id,
                name=name,
                recipe=recipe.to_dict(),
                figure=result.figure,
                recipe_text=result.recipe_text,
                sources_used=result.sources_used,
            )
        )

    preview = result.df.head(5).to_dict(orient="records")
    return {
        "ok": True,
        "chart_id": chart_id if result.figure is not None else None,
        "name": name,
        "data_preview": preview,
        "stats": result.stats,
        "recipe_text": result.recipe_text,
        "sources_used": result.sources_used,
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

- [ ] **Step 2: Smoke check (REPL)**

Run:

```
pwsh -Command "python -c 'from types import SimpleNamespace; from agent.tools import peek_feature, analyze; from features.loader import load_features; fid = next(iter(load_features())); print(peek_feature(fid)[\"columns\"]); turn = SimpleNamespace(charts=[]); print(analyze({\"sources\":[fid],\"ops\":[],\"stats\":[\"mean\"]}, turn)[\"stats\"])'"
```

Expected: prints a list of column dicts and a stats dict. No traceback.

Negative: `pwsh -Command "python -c 'from agent.tools import peek_feature; print(peek_feature(\"does_not_exist\"))'"`

Expected: `{'ok': False, 'error': \"feature_id 'does_not_exist' not found.\", 'available': [...]}`.

---

## Task 5: Update progress labels in client.py

**Files:**
- Modify: `agent/client.py:170-175`

- [ ] **Step 1: Replace the `_progress_label` dict**

Open `agent/client.py`. Replace this block:

```python
def _progress_label(tool_name: str) -> str:
    return {
        "get_feature_data": "Fetching feature data...",
        "make_chart": "Building chart...",
        "compute_stats": "Computing statistics...",
    }.get(tool_name, f"Running {tool_name}...")
```

with:

```python
def _progress_label(tool_name: str) -> str:
    return {
        "peek_feature": "Inspecting feature schema...",
        "analyze": "Running analysis...",
    }.get(tool_name, f"Running {tool_name}...")
```

No other changes to `client.py` are needed - `TOOLS`, `ChartMeta`, and `dispatch` are imported by name and the new shapes are compatible.

- [ ] **Step 2: Smoke check**

Just verify the file imports cleanly: `pwsh -Command "python -c 'from agent.client import AnalyticsAgent; print(\"ok\")'"` → prints `ok`.

---

## Task 6: System prompt rewrite

**Files:**
- Modify: `agent/prompts.py` (full replacement)

- [ ] **Step 1: Replace `agent/prompts.py`**

Replace the entire file with:

```python
# agent/prompts.py
from __future__ import annotations

from features.catalog import build_catalog_text


SYSTEM_PROMPT_TEMPLATE = """You are an analytics assistant for a SaaS business. The user has a fixed catalog of pre-computed business metrics. Your job: answer their questions with professional, clearly-explained insights and the right charts, using ONLY the catalog below. Never fabricate data.

## Tools

You have two tools:

1. `peek_feature(feature_id)` - Inspect a feature's schema and sample rows BEFORE writing a recipe. Use this whenever you are not certain of the column names of a feature you intend to use. Errors loudly if the feature_id is unknown.

2. `analyze(recipe)` - Execute a recipe and return a chart, stats, and data preview. A recipe describes the sources, transformations, and (optionally) the chart spec. The recipe is what gets persisted, so dashboards re-render from the latest data.

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

`custom_python.code` is the escape hatch when declarative ops can't express the transformation. The code receives `df`, `pd`, `np` and must end with `df = ...` (a pandas DataFrame). No imports allowed. Cap: 2000 chars.

## Naming policy (STRICTLY enforced)

- **Direct chart** = recipe has exactly one source AND empty ops. The chart's name is set automatically to the feature's canonical name. Do NOT set `chart.title` for direct charts; if you do, it will be overridden.
- **Derived chart** = anything else (multiple sources, or any op). You MUST supply a descriptive `chart.title` that names what was computed (e.g., "Top 5 Channels by CAC", "Net Growth: MRR Change Minus Churn").

## Refusal policy (STRICTLY enforced)

If the user asks about a metric that is NOT in the catalog below, do NOT call any tool. Reply only with:

> "I don't have data on **{topic}**. Available metrics: {list of feature_names}. Want to ask about one of those?"

Never substitute a tangentially related metric without telling the user. If unsure whether a feature applies, ask the user to confirm rather than guessing.

## Response format

After a successful `analyze` call, your text reply MUST:

1. Start with a one-sentence methodology line: "Computed by …" (or "Direct view of …" for a direct chart). Mention every source feature by name.
2. State the numeric findings using ONLY values that came back in `data_preview` or `stats`. If you want to mention a number not in the response, run another `analyze` with a filter to fetch it; never invent values.
3. Optionally add 1-2 sentences of plain-English summary.

## Worked examples

**Direct chart (single feature, no ops):**

User: "Show me MRR over time."
You call: `analyze({"sources": ["mrr"], "ops": [], "chart": {"type": "line", "x": "month", "y": "mrr_usd"}})`
You reply: "Direct view of Monthly Recurring Revenue. MRR ranges from $X to $Y across the period, ending at $Z."

**Derived single-source (top N):**

User: "Top 5 channels by CAC."
You call: `analyze({"sources": ["cac_by_channel"], "ops": [{"type": "top_n", "n": 5, "by": "cac_usd"}], "chart": {"type": "horizontal_bar", "x": "cac_usd", "y": "channel", "title": "Top 5 Channels by CAC"}})`
You reply: "Computed by ranking the CAC by Channel feature on cac_usd and keeping the top 5. The leader is {channel} at ${value}; …"

**Derived multi-source:**

User: "Compare MRR growth and churn over time."
You call: `analyze({"sources": ["mrr", "churn_rate"], "ops": [{"type": "join", "with": "churn_rate", "on": "month"}, {"type": "derive", "name": "net_growth_pct", "expr": "(mrr_change_pct - churn_rate_pct)"}, {"type": "sort", "by": "month"}], "chart": {"type": "line", "x": "month", "y": "net_growth_pct", "title": "Net Growth: MRR Change Minus Churn"}, "stats": ["mean", "min", "max"]})`
You reply: "Computed by joining Monthly Recurring Revenue and Churn Rate on month, then deriving net_growth_pct = mrr_change_pct - churn_rate_pct. Net growth averaged X% with a range of Y% to Z%."

**Refusal:**

User: "What's our employee headcount?"
You reply: "I don't have data on employee headcount. Available metrics: Monthly Recurring Revenue, Churn Rate, …. Want to ask about one of those?"

## Tool-use loop

- If you are confident of a feature's columns from the catalog below, you may go straight to `analyze`.
- If a recipe fails validation or execution, the error message names the failing op and column. Correct the recipe and call `analyze` again. You have a budget of 8 tool iterations per user turn.

## Available features (catalog)

{catalog_text}
"""


def build_system_prompt() -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(catalog_text=build_catalog_text())
```

- [ ] **Step 2: Smoke check**

Run: `pwsh -Command "python -c 'from agent.prompts import build_system_prompt; p = build_system_prompt(); print(len(p), \"ok\" if \"Refusal policy\" in p else \"MISSING\")'"`

Expected: prints a length (likely 4000-8000) and `ok`.

---

## Task 7: Saved-chart store rewrite

**Files:**
- Modify: `dashboard/store.py` (full replacement)

- [ ] **Step 1: Replace `dashboard/store.py`**

Replace the entire file with:

```python
# dashboard/store.py
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from agent.recipe import recipe_hash

logger = logging.getLogger(__name__)

DEFAULT_PATH = "data/saved_charts.json"


@dataclass
class SavedChart:
    id: str
    name: str
    recipe: dict
    created_at: str  # ISO-8601 UTC


def load_saved_charts(path: str = DEFAULT_PATH) -> list[SavedChart]:
    file_path = Path(path)
    if not file_path.exists():
        return []
    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError("saved_charts.json must be a list.")
        out: list[SavedChart] = []
        for item in raw:
            if not isinstance(item, dict) or "recipe" not in item:
                raise ValueError("Saved chart entry missing 'recipe' field (legacy schema).")
            out.append(SavedChart(**item))
        return out
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        backup = _backup_corrupt_file(file_path)
        logger.error(
            "Corrupt saved charts file (%s). Backed up to %s. Treating as empty.",
            exc, backup,
        )
        return []


def save_chart(name: str, recipe: dict, path: str = DEFAULT_PATH) -> SavedChart:
    """Append a new saved chart. Dedupes by recipe_hash."""
    existing = load_saved_charts(path)
    fingerprint = recipe_hash(recipe)
    for sc in existing:
        if recipe_hash(sc.recipe) == fingerprint:
            return sc

    new = SavedChart(
        id=str(uuid.uuid4()),
        name=name,
        recipe=recipe,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    existing.append(new)
    _atomic_write(existing, path)
    return new


def rename_chart(saved_id: str, new_name: str, path: str = DEFAULT_PATH) -> SavedChart | None:
    existing = load_saved_charts(path)
    target = None
    for sc in existing:
        if sc.id == saved_id:
            sc.name = new_name
            target = sc
            break
    if target is None:
        return None
    _atomic_write(existing, path)
    return target


def delete_chart(saved_id: str, path: str = DEFAULT_PATH) -> bool:
    existing = load_saved_charts(path)
    remaining = [sc for sc in existing if sc.id != saved_id]
    if len(remaining) == len(existing):
        return False
    _atomic_write(remaining, path)
    return True


def is_saved(recipe: dict, path: str = DEFAULT_PATH) -> bool:
    fingerprint = recipe_hash(recipe)
    for sc in load_saved_charts(path):
        if recipe_hash(sc.recipe) == fingerprint:
            return True
    return False


# ---------- helpers ----------

def _atomic_write(charts: list[SavedChart], path: str) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = file_path.with_suffix(file_path.suffix + ".tmp")
    payload = json.dumps([asdict(c) for c in charts], indent=2)
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, file_path)


def _backup_corrupt_file(file_path: Path) -> Path:
    timestamp = time.strftime("%Y%m%dT%H%M%S")
    backup = file_path.with_name(f"{file_path.stem}.corrupt-{timestamp}.json")
    try:
        file_path.replace(backup)
    except OSError:
        backup.write_text(file_path.read_text(encoding="utf-8"), encoding="utf-8")
    return backup
```

- [ ] **Step 2: Smoke check**

Run: `pwsh -Command "python -c 'from dashboard.store import save_chart, load_saved_charts; sc = save_chart(\"Test\", {\"sources\":[\"x\"],\"ops\":[],\"chart\":None,\"stats\":[]}, \"data/_smoke.json\"); print(sc.id); print(len(load_saved_charts(\"data/_smoke.json\"))); import os; os.remove(\"data/_smoke.json\")'"`

Expected: prints a UUID and `1`.

---

## Task 8: Chart actions UI - add expander

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
    """Render a chart with its action toolbar inside a Streamlit container.

    Args:
        chart_meta: The chart to render.
        message_index: Position of this turn in the message history (used for unique keys).
        on_save: Callback invoked when user clicks Save to Dashboard.
        on_rename: Callback invoked when user edits the name field.
        saved_keys: Set of recipe_hash strings already in the dashboard.
        recipe_hash_fn: Function that turns a recipe dict into a stable hash string.
    """
    container = st.container(border=True)
    with container:
        name_key = f"chart_name_{message_index}_{chart_meta.chart_id}"
        save_key = f"chart_save_{message_index}_{chart_meta.chart_id}"
        chart_key = f"chart_fig_{message_index}_{chart_meta.chart_id}"
        expand_key = f"chart_explain_{message_index}_{chart_meta.chart_id}"

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
                st.button("Saved ✓", key=save_key, disabled=True, use_container_width=True)
            else:
                if st.button("Save to Dashboard", key=save_key, use_container_width=True):
                    on_save(chart_meta)
                    st.rerun()
```

- [ ] **Step 2: Smoke check**

Just verify it imports: `pwsh -Command "python -c 'from charts.chart_actions import render_chart_with_actions; print(\"ok\")'"` → `ok`.

---

## Task 9: Conversation view - methodology + recipe-hash

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
from agent.tools import ChartMeta
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
        # Each message: {"role", "text", "charts": list[ChartMeta]}
        st.session_state.messages = []
    saved = load_saved_charts(SAVED_CHARTS_PATH)
    st.session_state.saved_chart_keys = {recipe_hash(sc.recipe) for sc in saved}


def _render_history() -> None:
    for index, msg in enumerate(st.session_state.messages):
        with st.chat_message(msg["role"]):
            if msg.get("text"):
                st.markdown(msg["text"])
            charts: list[ChartMeta] = msg.get("charts") or []
            _render_chart_list(charts, message_index=index)


def _handle_input() -> None:
    user_input = st.chat_input("Ask about your data...")
    if not user_input:
        return

    st.session_state.messages.append({"role": "user", "text": user_input, "charts": []})
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

        if final_turn.text:
            st.markdown(final_turn.text)

        new_index = len(st.session_state.messages)
        _render_chart_list(final_turn.charts, message_index=new_index)

    st.session_state.messages.append(
        {"role": "assistant", "text": final_turn.text, "charts": final_turn.charts}
    )


def _render_chart_list(charts: list[ChartMeta], message_index: int) -> None:
    if not charts:
        return
    if len(charts) >= 3:
        cols = st.columns(2)
        for i, cm in enumerate(charts):
            with cols[i % 2]:
                _render_one(cm, message_index)
    else:
        for cm in charts:
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

## Task 10: Dashboard view - rebuild via executor

**Files:**
- Modify: `views/dashboard.py` (full replacement)

- [ ] **Step 1: Replace `views/dashboard.py`**

Replace the entire file with:

```python
# views/dashboard.py
from __future__ import annotations

import json

import streamlit as st

from agent.recipe import Recipe, RecipeValidationError
from agent.recipe_executor import RecipeExecutionError, execute
from dashboard.store import (
    DEFAULT_PATH as SAVED_CHARTS_PATH,
    SavedChart,
    delete_chart,
    load_saved_charts,
    rename_chart,
)
from features.loader import load_features, reload_features


def render() -> None:
    # Always pull fresh feature data so the "latest data" guarantee holds.
    reload_features()
    catalog = load_features()
    saved = sorted(
        load_saved_charts(SAVED_CHARTS_PATH),
        key=lambda c: c.created_at,
        reverse=True,
    )

    if not saved:
        st.info(
            "No saved charts yet. Ask the assistant a question, then click "
            "Save to Dashboard on a chart."
        )
        return

    st.caption(f"{len(saved)} saved chart(s). Charts auto-refresh from latest features data.")

    cols = st.columns(2)
    for i, sc in enumerate(saved):
        with cols[i % 2]:
            _render_tile(sc, catalog)


def _render_tile(sc: SavedChart, catalog: dict) -> None:
    container = st.container(border=True)
    with container:
        name_key = f"saved_name_{sc.id}"
        delete_key = f"saved_delete_{sc.id}"
        chart_key = f"saved_fig_{sc.id}"

        new_name = st.text_input(
            "Chart name",
            value=sc.name,
            key=name_key,
            label_visibility="collapsed",
        )
        if new_name != sc.name:
            rename_chart(sc.id, new_name, SAVED_CHARTS_PATH)

        # Try to rebuild the figure from the saved recipe against latest data.
        error: str | None = None
        result = None
        try:
            recipe = Recipe.from_dict(sc.recipe)
            result = execute(recipe, catalog)
        except (RecipeValidationError, RecipeExecutionError) as exc:
            error = str(exc)

        if error:
            st.error(f"Could not refresh: {error}")
            with st.expander("View saved recipe", expanded=False):
                st.code(json.dumps(sc.recipe, indent=2), language="json")
        elif result is None or result.figure is None:
            st.warning("This saved chart has no chart spec - nothing to render.")
        else:
            st.plotly_chart(result.figure, use_container_width=True, key=chart_key)
            with st.expander("How this was calculated", expanded=False):
                if result.sources_used:
                    st.markdown("**Sources:**")
                    for src in result.sources_used:
                        st.markdown(f"- `{src['id']}` - {src['name']}")
                st.markdown(f"**Method:** {result.recipe_text}")
                st.markdown("**Recipe:**")
                st.code(json.dumps(sc.recipe, indent=2), language="json")

        cols = st.columns([4, 1])
        with cols[1]:
            if st.button("Delete", key=delete_key, use_container_width=True):
                delete_chart(sc.id, SAVED_CHARTS_PATH)
                st.rerun()
```

- [ ] **Step 2: Smoke check**

Run: `pwsh -Command "python -c 'from views.dashboard import render; print(\"ok\")'"` → `ok`.

---

## Task 11: Wipe legacy saved charts

**Files:**
- Delete: `data/saved_charts.json` (if it exists)

- [ ] **Step 1: Delete the legacy file**

Run: `pwsh -Command "if (Test-Path 'data/saved_charts.json') { Remove-Item 'data/saved_charts.json' -Force; 'deleted' } else { 'absent' }"`

Expected: `deleted` or `absent`. The dashboard view will recreate the file in the new shape on the first save.

- [ ] **Step 2: Confirm cold-start app launch**

Run: `pwsh -Command "streamlit run app.py" -run_in_background true` (or however you normally start the app), open the browser, switch to the Dashboard tab. Expected: the "No saved charts yet." info message renders. Stop the app.

---

## Task 12: Full smoke-test pass

This task is one connected manual session - run through all 16 checks from the spec without restarting the app between them, except where noted.

- [ ] **Step 1: Start the app**

Run from `D:\chatbot-analytics`: `pwsh -Command "streamlit run app.py"`. Open the URL it prints.

- [ ] **Step 2: Direct charts (single feature, no ops)**

Type each prompt in the Conversation tab:

1. "Show me MRR over time."
   - Expect: line chart titled exactly with the canonical feature name from the catalog (NOT agent-authored).
   - Expand "How this was calculated": shows source = the MRR feature, method = "Direct view of …", recipe with empty `ops`.
   - Reply text begins with "Direct view of …".

2. "Plot DAU."
   - Same shape; title = the DAU feature's canonical name.

- [ ] **Step 3: Derived charts (single feature with ops)**

3. "Show me the top 5 channels by CAC."
   - Expect: bar or horizontal_bar with an agent-authored title (e.g., "Top 5 Channels by CAC").
   - Expander shows a `top_n` op on the CAC feature.
   - Reply begins with "Computed by …" and cites only the values shown in the chart's data.

4. "What's the average and max DAU?"
   - Expect: stats text in the reply. Whether or not a chart appears, all numbers in the reply must match the `data_preview` / `stats` returned by `analyze`.

- [ ] **Step 4: Derived charts (multi-feature)**

5. "Compare MRR growth and churn rate over time."
   - Expect: chart with agent-authored title. Expander shows two sources, a `join` op on a shared time column, and (likely) a `derive`. Reply begins with "Computed by joining {MRR feature name} and {Churn Rate feature name} on …".

6. "What's our CAC per DAU by channel?" (skip if your features don't share a channel/date column - if so, expect a clean refusal saying so)
   - Expect: derived chart OR refusal explaining the columns don't line up. Either is acceptable; what's NOT acceptable is fabricated data.

- [ ] **Step 5: Refusal**

7. "Show me employee headcount."
   - Expect: text-only reply, no chart, follows the locked refusal template starting with "I don't have data on employee headcount." Lists available metrics.

8. "What's our gross margin?"
   - Expect: same kind of refusal.

- [ ] **Step 6: No-fabrication check**

9. "What was MRR in March 2024?" (substitute a month that exists in your MRR feature)
   - Expect: the agent either filters MRR to that month and cites the value, OR says it cannot pinpoint that exact month. NOT acceptable: a number that does not appear in the `analyze` response.

- [ ] **Step 7: Save + dashboard**

10. From the chart in turn 5 (or any derived chart), click "Save to Dashboard". Switch to the Dashboard tab.
    - Expect: the chart re-renders fresh from `data/features.json`. The "How this was calculated" expander shows the same sources and method as in the conversation.

11. In the Dashboard, edit the chart's name. Stop and restart the app (`Ctrl+C`, then `streamlit run app.py`). Re-open the Dashboard tab.
    - Expect: the new name persists.

12. While the app is stopped, edit `data/features.json`: append one new row to the source feature of the saved chart (e.g., a new month for MRR with a chosen value). Restart the app, click "Reload data" in the sidebar, then open the Dashboard tab.
    - Expect: the saved chart now reflects the new data point.

- [ ] **Step 8: Custom-python escape hatch**

13. "Show me a 3-month rolling average of MRR."
    - Expect: either the agent uses `custom_python` (visible in the recipe expander) and the chart renders, OR the agent uses declarative ops creatively and still produces a sensible chart. Either is fine; the test confirms the path works when needed. If the agent fails after 8 iterations, that's a known limitation - note it but don't block.

- [ ] **Step 9: Sandbox safety spot-check**

14. Stop the app. Run from a Python REPL:

```
pwsh -Command "python -c 'from types import SimpleNamespace; from agent.tools import analyze; turn = SimpleNamespace(charts=[]); print(analyze({\"sources\":[\"mrr\"],\"ops\":[{\"type\":\"custom_python\",\"code\":\"import os\"}],\"chart\":None,\"stats\":[]}, turn))'"
```

(Substitute `mrr` for whichever feature_id exists in your catalog.)

Expected: `{'ok': False, 'error': 'Recipe validation failed: custom_python.code must not import modules.'}`.

- [ ] **Step 10: Error surfaces**

15. From the same REPL, submit an invalid recipe:

```
pwsh -Command "python -c 'from types import SimpleNamespace; from agent.tools import analyze; turn = SimpleNamespace(charts=[]); print(analyze({\"sources\":[\"mrr\"],\"ops\":[{\"type\":\"bogus\"}]}, turn))'"
```

Expected: `{'ok': False, 'error': 'Recipe validation failed: Unknown op type: 'bogus''}`.

16. While the app is running, edit `data/features.json` and remove the source feature that an existing saved chart depends on. Click "Reload data" in the sidebar. Open the Dashboard.
    - Expect: that one card shows the red "Could not refresh: Source feature 'X' not found." message and a "View saved recipe" expander. Other cards keep rendering. Restore `data/features.json` afterward.

- [ ] **Step 11: Sign-off**

If every check above behaved as expected, the implementation is done. If any failed, capture the exact failure (message + which step) and revisit the relevant task before declaring complete.

---

## Self-review notes

- **Spec coverage:** Every section of the spec maps to a task: architecture (1, 3, 4), components (1-10), recipe DSL (1, 3), data flow (4, 5, 9, 10), error handling (1, 2, 3, 4, 10), no-migration (7, 11), smoke tests (12). Refusal policy is enforced via prompt (task 6) and verified in task 12 step 5.
- **Type consistency:** `ChartMeta` shape declared in task 4 is used identically in tasks 8 and 9. `recipe_hash` is defined in task 1 and consumed in tasks 7, 8, 9. `SavedChart` shape in task 7 matches what task 10 reads. `save_chart` signature `(name, recipe, path)` in task 7 matches the call site in task 9.
- **No placeholders:** every step contains the actual code or command needed.
