from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class FilterOp:
    column: str
    op: str # one of: ==, !=, <, >, <=, >=, in, between
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