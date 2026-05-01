# Chart Editor (v5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an inline chart editor (title, type, X/Y axes, multi-measure, units, column labels, filters) usable in conversation cards and dashboard tiles. Fix the wrong-`$` bug. Replace dashboard "How this was computed" with the same Raw data expander used elsewhere. Persist user edits as a `chart_view` layer alongside the recipe.

**Architecture:** Layered visualization model. Recipe stays immutable (computation). New `ChartView` dataclass (visualization layer) is user-editable: title, type, X, Y multi-select, color, column labels, column unit overrides, post-execution filters. Renderer takes `(view, df, axis_hints)` and produces a Plotly figure with unit-aware formatting (driven by `features.json` per-column metadata, not column-name suffixes). Multi-measure charts auto-pick dual-axis when units differ.

**Tech Stack:** Python 3.11+, Streamlit, pandas, plotly, dataclasses.

**User preferences honored:** No TDD (manual smoke tests at the end of each task and a full pass at the end). No git commands.

**Spec:** `docs/superpowers/specs/2026-05-01-chart-editor-design.md`.

---

## File Map

**New files:**
- `charts/chart_view.py` - ChartView dataclass, ChartViewFilter, AxisHints, default_chart_view, apply.
- `charts/chart_editor.py` - Streamlit editor expander.

**Modified files:**
- `features/loader.py` - ColumnMeta + parse `columns` field + inference fallback.
- `data/features.json` - `columns` metadata for all 15 features.
- `charts/renderer.py` - New `render(view, df, axis_hints)`; multi-measure with dual-axis; remove `_apply_executive_theme`.
- `agent/recipe_executor.py` - Remove figure construction; return df + source_dataframes.
- `agent/tools.py` - `ChartMeta.chart_view`, `ChartMeta.figure` removed; `default_chart_view` populated at analyze time.
- `charts/chart_actions.py` - Edit expander; render fresh from chart_view + df.
- `charts/analysis_card.py` - Edit + Save on source charts.
- `dashboard/store.py` - `SavedChart.chart_view`, `updated_at`; `save_chart` updates on hash match; new `update_chart_view`.
- `views/dashboard.py` - Replace "How this was computed" with Source caption + Raw data + Edit expander.

---

## Task 1: features.json - column metadata for all 15 features

**Files:**
- Modify: `data/features.json` (add `columns` block to every feature)

- [ ] **Step 1: Update all 15 features**

For each of the 15 features in `data/features.json`, add a `columns` object before the `data` array. Use this exact mapping:

```json
"F001": {
  "month":   {"label": "Month", "kind": "dimension", "unit": "date"},
  "mrr_usd": {"label": "MRR",   "kind": "measure",   "unit": "usd"}
},
"F002": {
  "month":            {"label": "Month",             "kind": "dimension", "unit": "date"},
  "customers_start":  {"label": "Customers (Start)", "kind": "measure",   "unit": "count"},
  "customers_lost":   {"label": "Customers Lost",    "kind": "measure",   "unit": "count"},
  "churn_rate_pct":   {"label": "Churn Rate",        "kind": "measure",   "unit": "pct"}
},
"F003": {
  "plan_tier":             {"label": "Plan Tier",          "kind": "dimension", "unit": "string"},
  "customers":             {"label": "Customers",          "kind": "measure",   "unit": "count"},
  "revenue_usd":           {"label": "Revenue",            "kind": "measure",   "unit": "usd"},
  "avg_revenue_per_user":  {"label": "Avg Revenue / User", "kind": "measure",   "unit": "usd"}
},
"F004": {
  "month":               {"label": "Month",              "kind": "dimension", "unit": "date"},
  "new_customers":       {"label": "New Customers",      "kind": "measure",   "unit": "count"},
  "returning_customers": {"label": "Returning Customers","kind": "measure",   "unit": "count"}
},
"F005": {
  "channel":     {"label": "Channel",     "kind": "dimension", "unit": "string"},
  "leads":       {"label": "Leads",       "kind": "measure",   "unit": "count"},
  "conversions": {"label": "Conversions", "kind": "measure",   "unit": "count"},
  "spend_usd":   {"label": "Spend",       "kind": "measure",   "unit": "usd"},
  "cac_usd":     {"label": "CAC",         "kind": "measure",   "unit": "usd"}
},
"F006": {
  "month":             {"label": "Month",          "kind": "dimension", "unit": "date"},
  "total_revenue_usd": {"label": "Total Revenue",  "kind": "measure",   "unit": "usd"},
  "active_users":      {"label": "Active Users",   "kind": "measure",   "unit": "count"},
  "arpu_usd":          {"label": "ARPU",           "kind": "measure",   "unit": "usd"}
},
"F007": {
  "category":           {"label": "Category",          "kind": "dimension", "unit": "string"},
  "ticket_count":       {"label": "Ticket Count",      "kind": "measure",   "unit": "count"},
  "avg_resolution_hrs": {"label": "Avg Resolution",    "kind": "measure",   "unit": "hours"},
  "escalated":          {"label": "Escalated",         "kind": "measure",   "unit": "count"}
},
"F008": {
  "date": {"label": "Date", "kind": "dimension", "unit": "date"},
  "dau":  {"label": "DAU",  "kind": "measure",   "unit": "count"}
},
"F009": {
  "stage":                  {"label": "Stage",                "kind": "dimension", "unit": "string"},
  "users":                  {"label": "Users",                "kind": "measure",   "unit": "count"},
  "conversion_to_next_pct": {"label": "Conversion to Next",   "kind": "measure",   "unit": "pct"}
},
"F010": {
  "feature_name":  {"label": "Feature",        "kind": "dimension", "unit": "string"},
  "users_adopted": {"label": "Users Adopted",  "kind": "measure",   "unit": "count"},
  "total_active":  {"label": "Total Active",   "kind": "measure",   "unit": "count"},
  "adoption_pct":  {"label": "Adoption Rate",  "kind": "measure",   "unit": "pct"}
},
"F011": {
  "month":           {"label": "Month",          "kind": "dimension", "unit": "date"},
  "starting_mrr":    {"label": "Starting MRR",   "kind": "measure",   "unit": "usd"},
  "expansion_mrr":   {"label": "Expansion MRR",  "kind": "measure",   "unit": "usd"},
  "contraction_mrr": {"label": "Contraction MRR","kind": "measure",   "unit": "usd"},
  "churned_mrr":     {"label": "Churned MRR",    "kind": "measure",   "unit": "usd"},
  "ending_mrr":      {"label": "Ending MRR",     "kind": "measure",   "unit": "usd"},
  "nrr_pct":         {"label": "NRR",            "kind": "measure",   "unit": "pct"}
},
"F012": {
  "category":         {"label": "Category",         "kind": "dimension", "unit": "string"},
  "orders":           {"label": "Orders",           "kind": "measure",   "unit": "count"},
  "gmv_usd":          {"label": "GMV",              "kind": "measure",   "unit": "usd"},
  "avg_order_value":  {"label": "Avg Order Value",  "kind": "measure",   "unit": "usd"},
  "return_rate_pct":  {"label": "Return Rate",      "kind": "measure",   "unit": "pct"}
},
"F013": {
  "signup_cohort":      {"label": "Signup Cohort",     "kind": "dimension", "unit": "string"},
  "customers":          {"label": "Customers",         "kind": "measure",   "unit": "count"},
  "avg_ltv_usd":        {"label": "Avg LTV",           "kind": "measure",   "unit": "usd"},
  "avg_months_active":  {"label": "Avg Months Active", "kind": "measure",   "unit": "number"},
  "avg_orders":         {"label": "Avg Orders",        "kind": "measure",   "unit": "number"}
},
"F014": {
  "duration_bucket": {"label": "Duration Bucket", "kind": "dimension", "unit": "string"},
  "session_count":   {"label": "Sessions",        "kind": "measure",   "unit": "count"},
  "pct_of_total":    {"label": "% of Total",      "kind": "measure",   "unit": "pct"}
},
"F015": {
  "month":      {"label": "Month",       "kind": "dimension", "unit": "date"},
  "responses":  {"label": "Responses",   "kind": "measure",   "unit": "count"},
  "promoters":  {"label": "Promoters",   "kind": "measure",   "unit": "count"},
  "passives":   {"label": "Passives",    "kind": "measure",   "unit": "count"},
  "detractors": {"label": "Detractors",  "kind": "measure",   "unit": "count"},
  "nps_score":  {"label": "NPS Score",   "kind": "measure",   "unit": "number"}
}
```

For each feature, insert the corresponding `columns` object as a sibling to `data`, keeping all existing fields (feature_id, feature_name, feature_description, category, tags, suggested_chart, x_field, y_field, y_fields, data) intact.

- [ ] **Step 2: Smoke check**

Run: `pwsh -Command ".venv/Scripts/python.exe -c 'import json; d = json.load(open(\"data/features.json\")); [print(f[\"feature_id\"], \"columns\" in f, len(f[\"columns\"])) for f in d]'"`

Expected: 15 lines, each printing the feature_id, `True`, and a numeric column count between 2 and 7.

---

## Task 2: features/loader.py - ColumnMeta + parsing + fallback

**Files:**
- Modify: `features/loader.py`

- [ ] **Step 1: Replace `features/loader.py`**

Replace the entire file with:

```python
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

DEFAULT_PATH = "data/features.json"

ALLOWED_KINDS = {"dimension", "measure"}
ALLOWED_UNITS = {"usd", "pct", "count", "hours", "days", "date", "string", "number"}


class FeaturesValidationError(ValueError):
    """Raised when features.json is missing required fields."""


class FeatureNotFoundError(KeyError):
    """Raised when a feature_id is not in the catalog."""


@dataclass(frozen=True)
class ColumnMeta:
    label: str
    kind: Literal["dimension", "measure"]
    unit: str  # one of ALLOWED_UNITS


@dataclass(frozen=True)
class Feature:
    id: str
    name: str
    description: str
    category: str
    tags: tuple[str, ...]
    suggested_chart: str | None
    x_field: str | None
    y_field: str | None
    y_fields: tuple[str, ...] | None
    data_columnar: dict[str, list]
    raw_data: tuple[dict, ...]
    columns: dict[str, ColumnMeta]  # one entry per data column

    @property
    def row_count(self) -> int:
        return len(self.data_columnar["rows"])


@lru_cache(maxsize=1)
def load_features(path: str = DEFAULT_PATH) -> dict[str, Feature]:
    file_path = Path(path)
    if not file_path.exists():
        raise FeaturesValidationError(f"Missing features file at {path}")

    with file_path.open("r", encoding="utf-8") as fh:
        raw = json.load(fh)

    if not isinstance(raw, list):
        raise FeaturesValidationError("features.json must be a JSON array.")

    catalog: dict[str, Feature] = {}
    for entry in raw:
        feature = _parse_entry(entry)
        if feature.id in catalog:
            raise FeaturesValidationError(f"Duplicate feature_id: {feature.id}")
        catalog[feature.id] = feature
    return catalog


def reload_features(path: str = DEFAULT_PATH) -> dict[str, Feature]:
    load_features.cache_clear()
    return load_features(path)


def get_feature(feature_id: str, path: str = DEFAULT_PATH) -> Feature:
    catalog = load_features(path)
    if feature_id not in catalog:
        raise FeatureNotFoundError(feature_id)
    return catalog[feature_id]


def _parse_entry(entry: Any) -> Feature:
    if not isinstance(entry, dict):
        raise FeaturesValidationError(f"Feature entry must be an object, got {type(entry).__name__}")

    required = ["feature_id", "feature_name", "data"]
    missing = [k for k in required if k not in entry]
    if missing:
        raise FeaturesValidationError(f"Feature entry missing keys {missing}: {entry}")

    rows = entry["data"]
    if not isinstance(rows, list) or not rows:
        raise FeaturesValidationError(
            f"Feature {entry['feature_id']} has empty or invalid data."
        )

    data_columns = _columns_from_rows(rows)
    data_columnar = {
        "columns": data_columns,
        "rows": [[row.get(c) for c in data_columns] for row in rows],
    }

    columns_meta = _parse_columns_meta(
        entry.get("columns"), data_columns, rows, entry["feature_id"]
    )

    return Feature(
        id=entry["feature_id"],
        name=entry["feature_name"],
        description=entry.get("feature_description", ""),
        category=entry.get("category", ""),
        tags=tuple(entry.get("tags", [])),
        suggested_chart=entry.get("suggested_chart"),
        x_field=entry.get("x_field"),
        y_field=entry.get("y_field"),
        y_fields=tuple(entry["y_fields"]) if entry.get("y_fields") else None,
        data_columnar=data_columnar,
        raw_data=tuple(rows),
        columns=columns_meta,
    )


def _columns_from_rows(rows: list[dict]) -> list[str]:
    seen: list[str] = []
    seen_set: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise FeaturesValidationError(f"Each data row must be an object, got {type(row).__name__}")
        for key in row.keys():
            if key not in seen_set:
                seen.append(key)
                seen_set.add(key)
    return seen


def _parse_columns_meta(
    raw_meta: Any,
    data_columns: list[str],
    rows: list[dict],
    feature_id: str,
) -> dict[str, ColumnMeta]:
    """Parse the `columns` block. Missing entries get inferred metadata."""
    if raw_meta is not None and not isinstance(raw_meta, dict):
        raise FeaturesValidationError(
            f"Feature {feature_id}: 'columns' must be an object, got {type(raw_meta).__name__}"
        )
    raw_meta = raw_meta or {}

    out: dict[str, ColumnMeta] = {}
    for col in data_columns:
        if col in raw_meta:
            meta_dict = raw_meta[col]
            if not isinstance(meta_dict, dict):
                raise FeaturesValidationError(
                    f"Feature {feature_id}: columns['{col}'] must be an object."
                )
            kind = meta_dict.get("kind")
            if kind not in ALLOWED_KINDS:
                raise FeaturesValidationError(
                    f"Feature {feature_id}: columns['{col}'].kind '{kind}' not in {sorted(ALLOWED_KINDS)}."
                )
            unit = meta_dict.get("unit")
            if unit not in ALLOWED_UNITS:
                raise FeaturesValidationError(
                    f"Feature {feature_id}: columns['{col}'].unit '{unit}' not in {sorted(ALLOWED_UNITS)}."
                )
            label = meta_dict.get("label") or _default_label(col)
            out[col] = ColumnMeta(label=label, kind=kind, unit=unit)
        else:
            out[col] = infer_column_meta(col, rows)
    return out


def infer_column_meta(column_name: str, rows: list[dict]) -> ColumnMeta:
    """Best-effort fallback when columns metadata isn't supplied for a column."""
    sample = next((r.get(column_name) for r in rows if r.get(column_name) is not None), None)

    # Unit inference from name
    name_lower = column_name.lower()
    if name_lower.endswith("_usd"):
        unit = "usd"
    elif name_lower.endswith("_pct"):
        unit = "pct"
    elif name_lower.endswith("_hrs") or name_lower.endswith("_hours"):
        unit = "hours"
    elif name_lower.endswith("_days"):
        unit = "days"
    elif name_lower in {"date", "month", "quarter"}:
        unit = "date"
    elif isinstance(sample, (int, float)) and not isinstance(sample, bool):
        unit = "count"
    else:
        unit = "string"

    # Kind inference from dtype
    if unit in {"usd", "pct", "count", "hours", "days", "number"}:
        kind = "measure"
    else:
        kind = "dimension"

    return ColumnMeta(label=_default_label(column_name), kind=kind, unit=unit)


def _default_label(column_name: str) -> str:
    return column_name.replace("_", " ").title()
```

- [ ] **Step 2: Smoke check (parse + inference)**

Run: `pwsh -Command ".venv/Scripts/python.exe -c 'from features.loader import reload_features; feats = reload_features(); f = feats[\"F012\"]; [print(c, m.kind, m.unit, m.label) for c, m in f.columns.items()]'"`

Expected:

```
category dimension string Category
orders measure count Orders
gmv_usd measure usd GMV
avg_order_value measure usd Avg Order Value
return_rate_pct measure pct Return Rate
```

- [ ] **Step 3: Smoke check (inference fallback)**

Run: `pwsh -Command ".venv/Scripts/python.exe -c 'from features.loader import infer_column_meta; print(infer_column_meta(\"orders\", [{\"orders\": 100}])); print(infer_column_meta(\"price_usd\", [{\"price_usd\": 9.99}])); print(infer_column_meta(\"category\", [{\"category\": \"X\"}]))'"`

Expected three `ColumnMeta(...)` printouts with: `count/measure/Orders`, `usd/measure/Price Usd`, `string/dimension/Category`.

---

## Task 3: charts/chart_view.py - ChartView dataclass + apply pipeline

**Files:**
- Create: `charts/chart_view.py`

- [ ] **Step 1: Create the module**

Write the full file:

```python
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

import pandas as pd

from features.loader import ColumnMeta, Feature

logger = logging.getLogger(__name__)


# ---------- Constants ----------

ALLOWED_FILTER_OPS = {"==", "!=", "<", "<=", ">", ">=", "in", "between"}
ALLOWED_UNITS = {"usd", "pct", "count", "hours", "days", "date", "string", "number"}

SINGLE_MEASURE_TYPES = {"pie", "heatmap", "funnel", "histogram", "box", "horizontal_bar"}
MULTI_MEASURE_TYPES = {"line", "bar", "scatter"}
GROUPED_TYPES = {"grouped_bar"}
ALL_TYPES = SINGLE_MEASURE_TYPES | MULTI_MEASURE_TYPES | GROUPED_TYPES


class ChartViewError(ValueError):
    """Raised when a chart_view is invalid."""


# ---------- Dataclasses ----------

@dataclass
class ChartViewFilter:
    column: str
    op: str
    value: Any

    def to_dict(self) -> dict:
        return {"column": self.column, "op": self.op, "value": self.value}

    @classmethod
    def from_dict(cls, raw: dict) -> "ChartViewFilter":
        if not isinstance(raw, dict):
            raise ChartViewError(f"Filter must be an object, got {type(raw).__name__}")
        if "column" not in raw or "op" not in raw or "value" not in raw:
            raise ChartViewError(f"Filter missing required keys: {raw}")
        if raw["op"] not in ALLOWED_FILTER_OPS:
            raise ChartViewError(
                f"Filter op '{raw['op']}' not in {sorted(ALLOWED_FILTER_OPS)}"
            )
        return cls(column=raw["column"], op=raw["op"], value=raw["value"])


@dataclass
class AxisHints:
    x_unit: str
    x_label: str
    left_y_unit: str
    left_y_label: str
    right_y_unit: str | None = None
    right_y_label: str | None = None


@dataclass
class ChartView:
    title: str
    type: str
    x: str
    y: list[str]
    color: str | None = None
    column_labels: dict[str, str] = field(default_factory=dict)
    column_units: dict[str, str] = field(default_factory=dict)
    filters: list[ChartViewFilter] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "type": self.type,
            "x": self.x,
            "y": list(self.y),
            "color": self.color,
            "column_labels": dict(self.column_labels),
            "column_units": dict(self.column_units),
            "filters": [f.to_dict() for f in self.filters],
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "ChartView":
        if not isinstance(raw, dict):
            raise ChartViewError(f"chart_view must be an object, got {type(raw).__name__}")
        title = raw.get("title", "")
        ctype = raw.get("type")
        if ctype not in ALL_TYPES:
            raise ChartViewError(
                f"chart_view.type '{ctype}' not in {sorted(ALL_TYPES)}"
            )
        x = raw.get("x")
        if not isinstance(x, str) or not x:
            raise ChartViewError("chart_view.x must be a non-empty string.")
        y_raw = raw.get("y")
        if not isinstance(y_raw, list) or not y_raw or not all(isinstance(c, str) for c in y_raw):
            raise ChartViewError("chart_view.y must be a non-empty list of strings.")
        if ctype in SINGLE_MEASURE_TYPES and len(y_raw) > 1:
            raise ChartViewError(
                f"chart_view.type '{ctype}' supports only one Y measure, got {len(y_raw)}."
            )

        units_raw = raw.get("column_units", {}) or {}
        for col, unit in units_raw.items():
            if unit not in ALLOWED_UNITS:
                raise ChartViewError(
                    f"chart_view.column_units['{col}'] = '{unit}' not in {sorted(ALLOWED_UNITS)}."
                )

        labels_raw = raw.get("column_labels", {}) or {}

        filters_raw = raw.get("filters", []) or []
        if not isinstance(filters_raw, list):
            raise ChartViewError("chart_view.filters must be a list.")
        filters = [ChartViewFilter.from_dict(f) for f in filters_raw]

        return cls(
            title=title,
            type=ctype,
            x=x,
            y=list(y_raw),
            color=raw.get("color"),
            column_labels=dict(labels_raw),
            column_units=dict(units_raw),
            filters=filters,
        )


# ---------- Default chart_view factory ----------

def default_chart_view(
    feature: Feature,
    recipe_chart: dict | None = None,
) -> ChartView:
    """Build the initial chart_view for a chart.

    For direct charts: pass the recipe.chart block (if present).
    For derived analysis source charts: pass recipe_chart=None - we use feature hints.
    """
    if recipe_chart:
        ctype = recipe_chart.get("type") or feature.suggested_chart or "bar"
        x = recipe_chart.get("x") or feature.x_field or _first_column_of_kind(feature, "dimension")
        y_raw = recipe_chart.get("y") or recipe_chart.get("y_fields") or feature.y_field or feature.y_fields
        title = recipe_chart.get("title") or feature.name
    else:
        ctype = feature.suggested_chart or "bar"
        x = feature.x_field or _first_column_of_kind(feature, "dimension")
        y_raw = feature.y_field or feature.y_fields
        title = feature.name

    if isinstance(y_raw, str):
        y_list = [y_raw]
    elif isinstance(y_raw, (list, tuple)) and y_raw:
        y_list = list(y_raw)
    else:
        first_measure = _first_column_of_kind(feature, "measure")
        y_list = [first_measure] if first_measure else []

    if not y_list:
        raise ChartViewError(f"Could not determine Y measure for feature {feature.id}")

    return ChartView(
        title=title,
        type=ctype,
        x=x,
        y=y_list,
        color=recipe_chart.get("color") if recipe_chart else None,
        column_labels={},
        column_units={},
        filters=[],
    )


def _first_column_of_kind(feature: Feature, kind: str) -> str | None:
    for col, meta in feature.columns.items():
        if meta.kind == kind:
            return col
    return None


# ---------- Apply pipeline ----------

def apply(
    view: ChartView,
    df: pd.DataFrame,
    feature_columns: dict[str, ColumnMeta],
) -> tuple[pd.DataFrame, AxisHints]:
    """Apply post-execution filters and resolve axis hints.

    Returns (filtered_df, axis_hints). filter mismatches are logged & skipped.
    """
    out = df
    for flt in view.filters:
        if flt.column not in out.columns:
            logger.warning(
                "ChartView filter references missing column '%s'; skipping.", flt.column
            )
            continue
        try:
            out = _apply_filter(out, flt)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Skipping bad filter %s: %s", flt.to_dict(), exc)

    out = out.reset_index(drop=True)

    x_unit = _resolve_unit(view.x, view, feature_columns)
    x_label = _resolve_label(view.x, view, feature_columns)

    units = [_resolve_unit(c, view, feature_columns) for c in view.y]
    distinct = list(dict.fromkeys(units))

    if len(distinct) <= 1:
        left_unit = distinct[0] if distinct else "number"
        right_unit = None
    elif len(distinct) == 2:
        left_unit, right_unit = distinct
    else:
        raise ChartViewError(
            f"Cannot plot {len(distinct)} distinct units on one chart: {distinct}. "
            "Pick at most 2 different units."
        )

    if len(view.y) == 1:
        left_y_label = _resolve_label(view.y[0], view, feature_columns)
    else:
        left_y_label = ""

    right_y_label: str | None = None
    if right_unit is not None:
        right_columns = [c for c, u in zip(view.y, units) if u == right_unit]
        if len(right_columns) == 1:
            right_y_label = _resolve_label(right_columns[0], view, feature_columns)
        else:
            right_y_label = ""

    hints = AxisHints(
        x_unit=x_unit,
        x_label=x_label,
        left_y_unit=left_unit,
        left_y_label=left_y_label,
        right_y_unit=right_unit,
        right_y_label=right_y_label,
    )
    return out, hints


def _apply_filter(df: pd.DataFrame, flt: ChartViewFilter) -> pd.DataFrame:
    series = df[flt.column]
    op = flt.op
    val = flt.value
    if op == "==":
        mask = series == val
    elif op == "!=":
        mask = series != val
    elif op == "<":
        mask = series < val
    elif op == "<=":
        mask = series <= val
    elif op == ">":
        mask = series > val
    elif op == ">=":
        mask = series >= val
    elif op == "in":
        if not isinstance(val, list):
            raise ChartViewError("filter.value for 'in' must be a list.")
        mask = series.isin(val)
    elif op == "between":
        if not (isinstance(val, list) and len(val) == 2):
            raise ChartViewError("filter.value for 'between' must be a 2-element list.")
        mask = (series >= val[0]) & (series <= val[1])
    else:
        raise ChartViewError(f"Unknown filter op: {op}")
    return df[mask]


def _resolve_unit(col: str, view: ChartView, feature_columns: dict[str, ColumnMeta]) -> str:
    if col in view.column_units:
        return view.column_units[col]
    if col in feature_columns:
        return feature_columns[col].unit
    return "string"


def _resolve_label(col: str, view: ChartView, feature_columns: dict[str, ColumnMeta]) -> str:
    if col in view.column_labels:
        return view.column_labels[col]
    if col in feature_columns:
        return feature_columns[col].label
    return col.replace("_", " ").title()
```

- [ ] **Step 2: Smoke check (default + apply)**

Run:

```
pwsh -Command ".venv/Scripts/python.exe -c 'from features.loader import reload_features; from charts.chart_view import default_chart_view, apply; import pandas as pd; feats = reload_features(); f = feats[\"F012\"]; v = default_chart_view(f, recipe_chart={\"type\":\"bar\",\"x\":\"category\",\"y\":\"orders\"}); print(v.to_dict()); df = pd.DataFrame(f.data_columnar[\"rows\"], columns=f.data_columnar[\"columns\"]); out_df, hints = apply(v, df, f.columns); print(hints)'"
```

Expected: a printed chart_view dict with `"type": "bar", "x": "category", "y": ["orders"]` and an `AxisHints` with `left_y_unit='count'`, `right_y_unit=None`.

- [ ] **Step 3: Smoke check (multi-measure + dual-axis)**

Run:

```
pwsh -Command ".venv/Scripts/python.exe -c 'from features.loader import reload_features; from charts.chart_view import ChartView, apply; import pandas as pd; feats = reload_features(); f = feats[\"F011\"]; v = ChartView(title=\"Test\", type=\"line\", x=\"month\", y=[\"starting_mrr\", \"nrr_pct\"]); df = pd.DataFrame(f.data_columnar[\"rows\"], columns=f.data_columnar[\"columns\"]); out_df, hints = apply(v, df, f.columns); print(\"left:\", hints.left_y_unit, \"right:\", hints.right_y_unit)'"
```

Expected: `left: usd right: pct`.

- [ ] **Step 4: Smoke check (3-unit error)**

Run:

```
pwsh -Command ".venv/Scripts/python.exe -c 'from features.loader import reload_features; from charts.chart_view import ChartView, apply; import pandas as pd; feats = reload_features(); f = feats[\"F011\"]; v = ChartView(title=\"X\", type=\"line\", x=\"month\", y=[\"starting_mrr\", \"nrr_pct\"], column_units={\"starting_mrr\":\"usd\", \"nrr_pct\":\"pct\", \"ending_mrr\":\"count\"}); v.y = [\"starting_mrr\", \"nrr_pct\", \"ending_mrr\"]; df = pd.DataFrame(f.data_columnar[\"rows\"], columns=f.data_columnar[\"columns\"]); apply(v, df, f.columns)'"
```

Expected: traceback ending with `ChartViewError: Cannot plot 3 distinct units...`.

---

## Task 4: charts/renderer.py - render(view, df, axis_hints)

**Files:**
- Modify: `charts/renderer.py` (full replacement)

- [ ] **Step 1: Replace `charts/renderer.py`**

Replace the entire file with:

```python
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
```

- [ ] **Step 2: Smoke check (single-measure render)**

Run:

```
pwsh -Command ".venv/Scripts/python.exe -c 'import pandas as pd; from features.loader import reload_features; from charts.chart_view import default_chart_view, apply; from charts.renderer import render; feats = reload_features(); f = feats[\"F012\"]; v = default_chart_view(f, recipe_chart={\"type\":\"bar\",\"x\":\"category\",\"y\":\"orders\"}); df = pd.DataFrame(f.data_columnar[\"rows\"], columns=f.data_columnar[\"columns\"]); odf, h = apply(v, df, f.columns); fig = render(v, odf, h); print(\"yaxis tickprefix:\", fig.layout.yaxis.tickprefix, \"ticksuffix:\", fig.layout.yaxis.ticksuffix)'"
```

Expected: `yaxis tickprefix: None ticksuffix: None` (count format - no $, no %). This validates the bug fix.

- [ ] **Step 3: Smoke check (dual axis)**

Run:

```
pwsh -Command ".venv/Scripts/python.exe -c 'import pandas as pd; from features.loader import reload_features; from charts.chart_view import ChartView, apply; from charts.renderer import render; feats = reload_features(); f = feats[\"F011\"]; v = ChartView(title=\"NRR vs MRR\", type=\"line\", x=\"month\", y=[\"starting_mrr\", \"nrr_pct\"]); df = pd.DataFrame(f.data_columnar[\"rows\"], columns=f.data_columnar[\"columns\"]); odf, h = apply(v, df, f.columns); fig = render(v, odf, h); print(\"yaxis:\", fig.layout.yaxis.tickprefix, \"yaxis2:\", fig.layout.yaxis2.ticksuffix)'"
```

Expected: `yaxis: $ yaxis2: %`.

---

## Task 5: agent/recipe_executor.py - drop figure construction

**Files:**
- Modify: `agent/recipe_executor.py`

- [ ] **Step 1: Edit `ExecutionResult` to drop figure fields**

Open `agent/recipe_executor.py`. Find the `ExecutionResult` dataclass and replace it with:

```python
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
```

Note: `figure` and `source_figures` are removed. Figures are built downstream now.

- [ ] **Step 2: Update `_build_direct_result` to drop figure**

Find `_build_direct_result` and replace its body with:

```python
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
```

- [ ] **Step 3: Update `_build_derived_result` to drop figure**

Find `_build_derived_result` and replace its body with:

```python
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
```

- [ ] **Step 4: Remove `_build_canonical_chart` and the `spec_to_figure` import**

In `agent/recipe_executor.py`:

1. Delete the `_build_canonical_chart` function entirely.
2. Update the imports - replace:
   ```python
   from charts.renderer import ChartSpecError, spec_to_figure
   ```
   with:
   ```python
   from charts.renderer import ChartSpecError
   ```
3. The `_build_direct_result` no longer creates a figure, so the `ChartSpecError` import is unused there too - but it may still be raised by other ops. Keep the import.

- [ ] **Step 5: Smoke check**

Run:

```
pwsh -Command ".venv/Scripts/python.exe -c 'from agent.recipe import Recipe; from agent.recipe_executor import execute; from features.loader import reload_features; feats = reload_features(); r = Recipe.from_dict({\"sources\":[\"F001\"],\"ops\":[]}); res = execute(r, feats); print(res.mode, len(res.df), hasattr(res, \"figure\"))'"
```

Expected: `direct 12 False` (no figure attribute).

---

## Task 6: agent/tools.py - ChartMeta.chart_view + drop figure

**Files:**
- Modify: `agent/tools.py`

- [ ] **Step 1: Update imports and ChartMeta**

Open `agent/tools.py`. Replace:

```python
import plotly.graph_objects as go

from agent.recipe import Recipe, RecipeValidationError
from agent.recipe_executor import RecipeExecutionError, execute
from features.loader import load_features
```

with:

```python
from agent.recipe import Recipe, RecipeValidationError
from agent.recipe_executor import RecipeExecutionError, execute
from charts.chart_view import ChartView, default_chart_view
from features.loader import load_features
```

Replace the `ChartMeta` dataclass with:

```python
@dataclass
class ChartMeta:
    chart_id: int
    name: str
    recipe: dict
    chart_view: dict     # serialized ChartView
    recipe_text: str
    sources_used: list[dict] = field(default_factory=list)
    mode: Literal["direct", "derived_source"] = "direct"
    data_columnar: dict | None = None  # populated for all charts (direct + derived_source)
    saved_id: str | None = None        # populated when this chart is loaded from a saved entry
```

Note: the `figure: go.Figure` field is removed. The `go` import is also no longer needed.

- [ ] **Step 2: Update `_emit_direct` to populate chart_view (no figure)**

Replace `_emit_direct` with:

```python
def _emit_direct(recipe: Recipe, result, turn, catalog: dict) -> dict:
    feature = catalog[recipe.sources[0]]
    name = feature.name
    chart_id = len(turn.charts)

    try:
        view = default_chart_view(feature, recipe_chart=recipe.chart)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"Could not build chart_view: {exc}"}

    turn.charts.append(
        ChartMeta(
            chart_id=chart_id,
            name=name,
            recipe=recipe.to_dict(),
            chart_view=view.to_dict(),
            recipe_text=result.recipe_text,
            sources_used=result.sources_used,
            mode="direct",
            data_columnar=feature.data_columnar,
        )
    )

    preview = result.df.head(5).to_dict(orient="records")
    return {
        "ok": True,
        "mode": "direct",
        "chart_id": chart_id,
        "name": name,
        "data_preview": preview,
        "stats": result.stats,
        "recipe_text": result.recipe_text,
        "sources_used": result.sources_used,
    }
```

- [ ] **Step 3: Update `_emit_derived` to populate chart_view per source (no figure)**

Replace `_emit_derived` with:

```python
def _emit_derived(recipe: Recipe, result, turn) -> dict:
    catalog = load_features()
    source_chart_ids: list[int] = []
    source_charts_payload: list[dict] = []

    for src in result.sources_used:
        feature = catalog[src["id"]]
        chart_id = len(turn.charts)
        try:
            view = default_chart_view(feature, recipe_chart=None)
        except Exception as exc:  # noqa: BLE001
            # Skip this source if we can't build a default view.
            continue
        feature_columnar = {
            "columns": list(result.source_dataframes[src["id"]].columns),
            "rows": result.source_dataframes[src["id"]].values.tolist(),
        }
        # Synthesize a "direct" recipe that the user could save standalone if they edit.
        synth_recipe = {
            "sources": [src["id"]],
            "ops": [],
            "chart": view.to_dict(),  # editor edits chart_view, recipe just records the chart for hash
            "stats": [],
        }
        turn.charts.append(
            ChartMeta(
                chart_id=chart_id,
                name=src["name"],
                recipe=synth_recipe,
                chart_view=view.to_dict(),
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
```

- [ ] **Step 4: Smoke check (direct + chart_view)**

Run:

```
pwsh -Command ".venv/Scripts/python.exe -c 'from types import SimpleNamespace; from agent.tools import analyze; turn = SimpleNamespace(charts=[], analysis_cards=[]); r = analyze({\"sources\":[\"F012\"],\"ops\":[],\"chart\":{\"type\":\"bar\",\"x\":\"category\",\"y\":\"orders\"}}, turn); print(r[\"mode\"]); print(turn.charts[0].chart_view)'"
```

Expected: `direct` and a chart_view dict with `type=bar, x=category, y=['orders']`.

---

## Task 7: charts/chart_editor.py - the editor expander

**Files:**
- Create: `charts/chart_editor.py`

- [ ] **Step 1: Create the module**

Write the full file:

```python
from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from charts.chart_view import (
    ALL_TYPES,
    ALLOWED_FILTER_OPS,
    ALLOWED_UNITS,
    SINGLE_MEASURE_TYPES,
    ChartView,
    ChartViewError,
    ChartViewFilter,
    default_chart_view,
)
from features.loader import ColumnMeta, Feature


def render_chart_editor(
    chart_view: ChartView,
    df: pd.DataFrame,
    feature_columns: dict[str, ColumnMeta],
    feature: Feature,
    recipe_chart: dict | None,
    key_prefix: str,
) -> ChartView:
    """Render editor controls. Returns the updated chart_view (live)."""

    # Title
    title = st.text_input(
        "Title",
        value=chart_view.title,
        key=f"{key_prefix}_title",
    )

    type_col, x_col = st.columns(2)
    with type_col:
        ctype = st.selectbox(
            "Type",
            options=sorted(ALL_TYPES),
            index=sorted(ALL_TYPES).index(chart_view.type),
            key=f"{key_prefix}_type",
        )
    with x_col:
        # X options: dimension columns; for scatter type also allow measures.
        x_options = [
            c for c, m in feature_columns.items()
            if m.kind == "dimension" or ctype == "scatter"
        ]
        if not x_options:
            x_options = list(feature_columns.keys())
        x_idx = x_options.index(chart_view.x) if chart_view.x in x_options else 0
        x = st.selectbox("X axis", options=x_options, index=x_idx, key=f"{key_prefix}_x")

    # Y picker - multi-select for multi-measure types, selectbox for single-measure-only.
    measure_options = [c for c, m in feature_columns.items() if m.kind == "measure"]
    if not measure_options:
        measure_options = list(feature_columns.keys())

    is_single = ctype in SINGLE_MEASURE_TYPES
    if is_single:
        cur = chart_view.y[0] if chart_view.y else measure_options[0]
        y_idx = measure_options.index(cur) if cur in measure_options else 0
        y_single = st.selectbox(
            "Y measure",
            options=measure_options,
            index=y_idx,
            key=f"{key_prefix}_y_single",
        )
        y = [y_single]
    else:
        default_y = [c for c in chart_view.y if c in measure_options] or [measure_options[0]]
        y = st.multiselect(
            "Y measures",
            options=measure_options,
            default=default_y,
            key=f"{key_prefix}_y_multi",
        )
        if not y:
            st.warning("At least one Y measure is required; reverting.")
            y = default_y

    # Color (single-measure only)
    color: str | None = None
    if len(y) == 1:
        dimension_options = ["(none)"] + [c for c, m in feature_columns.items() if m.kind == "dimension"]
        cur_color = chart_view.color or "(none)"
        c_idx = dimension_options.index(cur_color) if cur_color in dimension_options else 0
        color_choice = st.selectbox(
            "Color (group by)",
            options=dimension_options,
            index=c_idx,
            key=f"{key_prefix}_color",
        )
        color = None if color_choice == "(none)" else color_choice

    # Column display labels
    referenced_cols = [x] + y
    st.markdown("**Column display labels**")
    column_labels: dict[str, str] = {}
    for col in referenced_cols:
        default = chart_view.column_labels.get(col, "")
        new_label = st.text_input(
            f"Label for `{col}`",
            value=default,
            placeholder=feature_columns[col].label if col in feature_columns else col,
            key=f"{key_prefix}_label_{col}",
        )
        if new_label.strip():
            column_labels[col] = new_label.strip()

    # Column unit overrides (Y columns only)
    st.markdown("**Column units**")
    column_units: dict[str, str] = {}
    for col in y:
        default_unit = (
            chart_view.column_units.get(col)
            or (feature_columns[col].unit if col in feature_columns else "number")
        )
        unit_options = sorted(ALLOWED_UNITS)
        unit_idx = unit_options.index(default_unit) if default_unit in unit_options else 0
        chosen = st.selectbox(
            f"Unit for `{col}`",
            options=unit_options,
            index=unit_idx,
            key=f"{key_prefix}_unit_{col}",
        )
        # Only persist as override if it differs from the feature default.
        feature_default = feature_columns[col].unit if col in feature_columns else None
        if chosen != feature_default:
            column_units[col] = chosen

    # Filters
    st.markdown("**Filters (post-execution, AND'd)**")
    filters = _render_filters(chart_view.filters, df, key_prefix)

    return ChartView(
        title=title,
        type=ctype,
        x=x,
        y=y,
        color=color,
        column_labels=column_labels,
        column_units=column_units,
        filters=filters,
    )


def _render_filters(
    existing: list[ChartViewFilter],
    df: pd.DataFrame,
    key_prefix: str,
) -> list[ChartViewFilter]:
    state_key = f"{key_prefix}_filters_state"
    if state_key not in st.session_state:
        st.session_state[state_key] = [f.to_dict() for f in existing]

    new_state: list[dict] = []
    column_options = list(df.columns)

    for i, flt in enumerate(st.session_state[state_key]):
        cols = st.columns([2, 1, 2, 0.5])
        with cols[0]:
            col_idx = column_options.index(flt["column"]) if flt.get("column") in column_options else 0
            col = st.selectbox(
                f"Column {i + 1}",
                options=column_options,
                index=col_idx,
                key=f"{key_prefix}_filt_col_{i}",
            )
        with cols[1]:
            op_options = sorted(ALLOWED_FILTER_OPS)
            op_idx = op_options.index(flt.get("op", "==")) if flt.get("op") in op_options else 0
            op = st.selectbox(
                f"Op {i + 1}",
                options=op_options,
                index=op_idx,
                key=f"{key_prefix}_filt_op_{i}",
                label_visibility="collapsed",
            )
        with cols[2]:
            cur_val = flt.get("value", "")
            if isinstance(cur_val, (list, tuple)):
                cur_val_str = ",".join(str(v) for v in cur_val)
            else:
                cur_val_str = str(cur_val) if cur_val is not None else ""
            val_str = st.text_input(
                f"Value {i + 1}",
                value=cur_val_str,
                key=f"{key_prefix}_filt_val_{i}",
                label_visibility="collapsed",
            )
            value: Any = _parse_filter_value(val_str, op)
        with cols[3]:
            if st.button("x", key=f"{key_prefix}_filt_rm_{i}"):
                continue  # skip this filter; effectively removes it

        new_state.append({"column": col, "op": op, "value": value})

    if st.button("+ Add filter", key=f"{key_prefix}_filt_add"):
        new_state.append({"column": column_options[0] if column_options else "", "op": "==", "value": ""})

    st.session_state[state_key] = new_state

    # Convert to ChartViewFilter, skipping invalid rows.
    out: list[ChartViewFilter] = []
    for entry in new_state:
        if not entry.get("column") or entry.get("value") in (None, ""):
            continue
        try:
            out.append(ChartViewFilter.from_dict(entry))
        except ChartViewError:
            continue
    return out


def _parse_filter_value(raw: str, op: str) -> Any:
    raw = raw.strip()
    if op == "in":
        return [_coerce(v.strip()) for v in raw.split(",") if v.strip()]
    if op == "between":
        parts = [p.strip() for p in raw.split(",")]
        if len(parts) != 2:
            return raw  # invalid; will be caught by validator
        return [_coerce(parts[0]), _coerce(parts[1])]
    return _coerce(raw)


def _coerce(s: str) -> Any:
    if not s:
        return s
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s
```

- [ ] **Step 2: Smoke check (import only)**

Run: `pwsh -Command ".venv/Scripts/python.exe -c 'from charts.chart_editor import render_chart_editor; print(\"ok\")'"` → `ok`.

---

## Task 8: dashboard/store.py - chart_view + update semantics

**Files:**
- Modify: `dashboard/store.py` (full replacement)

- [ ] **Step 1: Replace `dashboard/store.py`**

Replace the entire file with:

```python
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
    chart_view: dict
    created_at: str   # ISO-8601 UTC
    updated_at: str   # ISO-8601 UTC


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
            if not isinstance(item, dict):
                raise ValueError("Saved chart entry must be an object.")
            for required in ("id", "name", "recipe", "chart_view", "created_at"):
                if required not in item:
                    raise ValueError(f"Saved chart entry missing '{required}'.")
            if "updated_at" not in item:
                item["updated_at"] = item["created_at"]
            out.append(SavedChart(**item))
        return out
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        backup = _backup_corrupt_file(file_path)
        logger.error(
            "Corrupt saved charts file (%s). Backed up to %s. Treating as empty.",
            exc, backup,
        )
        return []


def save_chart(name: str, recipe: dict, chart_view: dict, path: str = DEFAULT_PATH) -> SavedChart:
    """Insert or update a saved chart by recipe_hash."""
    existing = load_saved_charts(path)
    fingerprint = recipe_hash(recipe)
    now = datetime.now(timezone.utc).isoformat()
    for sc in existing:
        if recipe_hash(sc.recipe) == fingerprint:
            sc.name = name
            sc.chart_view = chart_view
            sc.updated_at = now
            _atomic_write(existing, path)
            return sc

    new = SavedChart(
        id=str(uuid.uuid4()),
        name=name,
        recipe=recipe,
        chart_view=chart_view,
        created_at=now,
        updated_at=now,
    )
    existing.append(new)
    _atomic_write(existing, path)
    return new


def update_chart_view(saved_id: str, chart_view: dict, path: str = DEFAULT_PATH) -> SavedChart | None:
    existing = load_saved_charts(path)
    target = None
    now = datetime.now(timezone.utc).isoformat()
    for sc in existing:
        if sc.id == saved_id:
            sc.chart_view = chart_view
            sc.name = chart_view.get("title", sc.name)
            sc.updated_at = now
            target = sc
            break
    if target is None:
        return None
    _atomic_write(existing, path)
    return target


def rename_chart(saved_id: str, new_name: str, path: str = DEFAULT_PATH) -> SavedChart | None:
    existing = load_saved_charts(path)
    target = None
    now = datetime.now(timezone.utc).isoformat()
    for sc in existing:
        if sc.id == saved_id:
            sc.name = new_name
            sc.chart_view["title"] = new_name
            sc.updated_at = now
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

- [ ] **Step 2: Wipe legacy saved-charts file**

Run: `pwsh -Command "if (Test-Path 'data/saved_charts.json') { Remove-Item 'data/saved_charts.json' -Force; 'deleted' } else { 'absent' }"`

Expected: `deleted` or `absent`. The new schema requires `chart_view`; old entries are incompatible.

- [ ] **Step 3: Smoke check (insert + update)**

Run:

```
pwsh -Command ".venv/Scripts/python.exe -c 'from dashboard.store import save_chart, load_saved_charts; r = {\"sources\":[\"F001\"],\"ops\":[]}; v = {\"title\":\"T\",\"type\":\"line\",\"x\":\"month\",\"y\":[\"mrr_usd\"],\"color\":None,\"column_labels\":{},\"column_units\":{},\"filters\":[]}; sc1 = save_chart(\"A\", r, v, \"data/_smoke.json\"); v2 = dict(v, title=\"B\"); sc2 = save_chart(\"B\", r, v2, \"data/_smoke.json\"); items = load_saved_charts(\"data/_smoke.json\"); print(\"count:\", len(items), \"latest title:\", items[0].name); import os; os.remove(\"data/_smoke.json\")'"
```

Expected: `count: 1 latest title: B` (insert then update, dedup by recipe_hash).

---

## Task 9: charts/chart_actions.py - Edit expander, render fresh

**Files:**
- Modify: `charts/chart_actions.py` (full replacement)

- [ ] **Step 1: Replace `charts/chart_actions.py`**

Replace the entire file with:

```python
from __future__ import annotations

from typing import Callable

import pandas as pd
import streamlit as st

from agent.recipe import recipe_hash
from agent.tools import ChartMeta
from charts.chart_editor import render_chart_editor
from charts.chart_view import ChartView, ChartViewError, apply
from charts.renderer import ChartSpecError, render
from charts.source_data import render_raw_data_expander
from features.loader import load_features


def render_chart_with_actions(
    chart_meta: ChartMeta,
    message_index: int,
    on_save: Callable[[ChartMeta, ChartView], None],
    on_rename: Callable[[ChartMeta, str], None],
    saved_keys: set[str],
) -> None:
    """Render a SAVABLE direct chart card."""
    if chart_meta.mode != "direct":
        st.warning(
            f"render_chart_with_actions called with non-direct chart "
            f"(mode={chart_meta.mode!r}). This is a bug; please report."
        )
        return

    container = st.container(border=True)
    with container:
        key_prefix = f"chart_{message_index}_{chart_meta.chart_id}"

        # Resolve the in-memory chart_view (mutated by editor across reruns).
        view_state_key = f"{key_prefix}_view"
        if view_state_key not in st.session_state:
            st.session_state[view_state_key] = ChartView.from_dict(chart_meta.chart_view)
        view: ChartView = st.session_state[view_state_key]

        # Editable name (top of card) - mirrors view.title.
        new_name = st.text_input(
            "Chart name",
            value=view.title,
            key=f"{key_prefix}_name",
            label_visibility="collapsed",
        )
        if new_name != view.title:
            view.title = new_name
            on_rename(chart_meta, new_name)

        # Resolve the source feature + its data.
        feature_id = chart_meta.recipe.get("sources", [None])[0]
        catalog = load_features()
        feature = catalog.get(feature_id) if feature_id else None
        if chart_meta.data_columnar:
            df = pd.DataFrame(
                chart_meta.data_columnar["rows"],
                columns=chart_meta.data_columnar["columns"],
            )
        else:
            df = pd.DataFrame()

        # Render figure live from the current chart_view.
        feature_columns = feature.columns if feature else {}
        try:
            filtered_df, hints = apply(view, df, feature_columns)
            fig = render(view, filtered_df, hints)
            st.plotly_chart(fig, use_container_width=True, key=f"{key_prefix}_fig")
            invalid_msg: str | None = None
        except (ChartViewError, ChartSpecError) as exc:
            invalid_msg = str(exc)
            st.error(f"Chart cannot render: {invalid_msg}")

        # Source caption.
        if chart_meta.sources_used:
            src = chart_meta.sources_used[0]
            row_count = len(chart_meta.data_columnar["rows"]) if chart_meta.data_columnar else "?"
            st.caption(
                f"**Source:** `{src['id']}` ({src['name']}) - "
                f"{row_count} rows. Direct view (no transformations)."
            )

        # Raw data expander.
        render_raw_data_expander(
            data_columnar=chart_meta.data_columnar,
            name=view.title,
            key_suffix=f"direct_{message_index}_{chart_meta.chart_id}",
        )

        # Edit expander.
        edit_key = f"{key_prefix}_edit_open"
        if edit_key not in st.session_state:
            st.session_state[edit_key] = False
        with st.expander("Edit chart", expanded=st.session_state[edit_key]):
            new_view = render_chart_editor(
                chart_view=view,
                df=df,
                feature_columns=feature_columns,
                feature=feature,
                recipe_chart=chart_meta.recipe.get("chart"),
                key_prefix=key_prefix,
            )
            st.session_state[view_state_key] = new_view

            cols = st.columns([1, 1, 4])
            with cols[0]:
                if st.button("Reset", key=f"{key_prefix}_reset"):
                    if feature:
                        from charts.chart_view import default_chart_view
                        st.session_state[view_state_key] = default_chart_view(
                            feature, recipe_chart=chart_meta.recipe.get("chart")
                        )
                        st.rerun()
            with cols[1]:
                save_disabled = invalid_msg is not None
                if st.button("Save", key=f"{key_prefix}_save", disabled=save_disabled):
                    on_save(chart_meta, st.session_state[view_state_key])
                    st.rerun()

        # Saved-status indicator.
        already_saved = recipe_hash(chart_meta.recipe) in saved_keys
        if already_saved:
            st.caption("Saved to Dashboard.")
```

Note: the function signature changes - `on_save` now takes `(chart_meta, chart_view)`, and `saved_keys` takes plain `set[str]`. The conversation view needs updating in Task 11.

- [ ] **Step 2: Smoke check**

Run: `pwsh -Command ".venv/Scripts/python.exe -c 'from charts.chart_actions import render_chart_with_actions; print(\"ok\")'"` → `ok`.

---

## Task 10: charts/analysis_card.py - Edit + Save on source charts

**Files:**
- Modify: `charts/analysis_card.py` (full replacement)

- [ ] **Step 1: Replace `charts/analysis_card.py`**

Replace the entire file with:

```python
from __future__ import annotations

import json
from typing import Callable

import pandas as pd
import streamlit as st

from agent.tools import AnalysisCard, ChartMeta
from charts.chart_editor import render_chart_editor
from charts.chart_view import ChartView, ChartViewError, apply, default_chart_view
from charts.renderer import ChartSpecError, render
from charts.source_data import render_raw_data_expander
from features.loader import load_features


def render_analysis_card(
    card: AnalysisCard,
    source_charts: list[ChartMeta],
    message_index: int,
    on_save_source: Callable[[ChartMeta, ChartView], None],
) -> None:
    """Render the analysis block (methodology + recipe expander) and its source charts."""
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

    if not source_charts:
        return

    if len(source_charts) >= 2:
        cols = st.columns(2)
        for i, cm in enumerate(source_charts):
            with cols[i % 2]:
                _render_source_chart(cm, message_index, card.analysis_id, on_save_source)
    else:
        _render_source_chart(source_charts[0], message_index, card.analysis_id, on_save_source)


def _render_source_chart(
    cm: ChartMeta,
    message_index: int,
    analysis_id: int,
    on_save_source: Callable[[ChartMeta, ChartView], None],
) -> None:
    sub = st.container(border=True)
    with sub:
        key_prefix = f"src_{message_index}_{analysis_id}_{cm.chart_id}"

        view_state_key = f"{key_prefix}_view"
        if view_state_key not in st.session_state:
            st.session_state[view_state_key] = ChartView.from_dict(cm.chart_view)
        view: ChartView = st.session_state[view_state_key]

        st.markdown(f"**{view.title}**")

        catalog = load_features()
        feature_id = cm.recipe.get("sources", [None])[0]
        feature = catalog.get(feature_id) if feature_id else None
        if cm.data_columnar:
            df = pd.DataFrame(cm.data_columnar["rows"], columns=cm.data_columnar["columns"])
        else:
            df = pd.DataFrame()

        feature_columns = feature.columns if feature else {}
        invalid_msg: str | None = None
        try:
            filtered_df, hints = apply(view, df, feature_columns)
            fig = render(view, filtered_df, hints)
            st.plotly_chart(fig, use_container_width=True, key=f"{key_prefix}_fig")
        except (ChartViewError, ChartSpecError) as exc:
            invalid_msg = str(exc)
            st.error(f"Chart cannot render: {invalid_msg}")

        render_raw_data_expander(
            data_columnar=cm.data_columnar,
            name=view.title,
            key_suffix=key_prefix,
        )

        with st.expander("Edit chart", expanded=False):
            new_view = render_chart_editor(
                chart_view=view,
                df=df,
                feature_columns=feature_columns,
                feature=feature,
                recipe_chart=None,
                key_prefix=key_prefix,
            )
            st.session_state[view_state_key] = new_view

            cols = st.columns([1, 1, 4])
            with cols[0]:
                if st.button("Reset", key=f"{key_prefix}_reset"):
                    if feature:
                        st.session_state[view_state_key] = default_chart_view(feature, recipe_chart=None)
                        st.rerun()
            with cols[1]:
                if st.button("Save", key=f"{key_prefix}_save", disabled=(invalid_msg is not None)):
                    on_save_source(cm, st.session_state[view_state_key])
                    st.rerun()
```

Note: the function signature changes - `render_analysis_card` now takes `on_save_source` callback. Conversation view updates next.

- [ ] **Step 2: Smoke check**

Run: `pwsh -Command ".venv/Scripts/python.exe -c 'from charts.analysis_card import render_analysis_card; print(\"ok\")'"` → `ok`.

---

## Task 11: views/conversation.py - wire on_save callbacks

**Files:**
- Modify: `views/conversation.py`

- [ ] **Step 1: Replace `views/conversation.py`**

Replace the entire file with:

```python
from __future__ import annotations

import streamlit as st

from agent.client import AnalyticsAgent, AssistantTurn, ProgressUpdate
from agent.recipe import recipe_hash
from agent.tools import AnalysisCard, ChartMeta
from charts.analysis_card import render_analysis_card
from charts.chart_actions import render_chart_with_actions
from charts.chart_view import ChartView
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
    chart_by_id = {cm.chart_id: cm for cm in charts}
    chart_ids_in_cards: set[int] = set()
    for card in analysis_cards:
        chart_ids_in_cards.update(card.source_chart_ids)
        source_charts = [chart_by_id[cid] for cid in card.source_chart_ids if cid in chart_by_id]
        render_analysis_card(card, source_charts, message_index, on_save_source=_on_save_chart)

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
        on_save=_on_save_chart,
        on_rename=_on_rename,
        saved_keys=st.session_state.saved_chart_keys,
    )


def _on_save_chart(cm: ChartMeta, view: ChartView) -> None:
    saved = save_chart(
        name=view.title,
        recipe=cm.recipe,
        chart_view=view.to_dict(),
        path=SAVED_CHARTS_PATH,
    )
    st.session_state.saved_chart_keys.add(recipe_hash(saved.recipe))
    cm.saved_id = saved.id
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

Run: `pwsh -Command ".venv/Scripts/python.exe -c 'from views.conversation import render; print(\"ok\")'"` → `ok`.

---

## Task 12: views/dashboard.py - Edit, Raw data, no "How this was computed"

**Files:**
- Modify: `views/dashboard.py` (full replacement)

- [ ] **Step 1: Replace `views/dashboard.py`**

Replace the entire file with:

```python
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from agent.recipe import Recipe, RecipeValidationError
from agent.recipe_executor import RecipeExecutionError, execute
from charts.chart_editor import render_chart_editor
from charts.chart_view import ChartView, ChartViewError, apply, default_chart_view
from charts.renderer import ChartSpecError, render
from charts.source_data import render_raw_data_expander
from dashboard.store import (
    DEFAULT_PATH as SAVED_CHARTS_PATH,
    SavedChart,
    delete_chart,
    load_saved_charts,
    rename_chart,
    update_chart_view,
)
from features.loader import load_features, reload_features


def render() -> None:
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
            "Save on a chart in the Edit panel."
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
        key_prefix = f"saved_{sc.id}"

        # Resolve current chart_view from session state (preserves edits across reruns).
        view_state_key = f"{key_prefix}_view"
        if view_state_key not in st.session_state:
            try:
                st.session_state[view_state_key] = ChartView.from_dict(sc.chart_view)
            except ChartViewError as exc:
                st.error(f"Saved chart has an invalid chart_view: {exc}")
                if st.button("Delete", key=f"{key_prefix}_delete_invalid"):
                    delete_chart(sc.id, SAVED_CHARTS_PATH)
                    st.rerun()
                return
        view: ChartView = st.session_state[view_state_key]

        new_name = st.text_input(
            "Chart name",
            value=view.title,
            key=f"{key_prefix}_name",
            label_visibility="collapsed",
        )
        if new_name != view.title:
            view.title = new_name
            rename_chart(sc.id, new_name, SAVED_CHARTS_PATH)

        # Re-run the recipe to get the result df.
        recipe_error: str | None = None
        result = None
        try:
            recipe = Recipe.from_dict(sc.recipe)
            result = execute(recipe, catalog)
        except (RecipeValidationError, RecipeExecutionError) as exc:
            recipe_error = str(exc)

        if recipe_error:
            st.error(f"Could not refresh: {recipe_error}")
            with st.expander("View saved recipe", expanded=False):
                st.code(json.dumps(sc.recipe, indent=2), language="json")
            cols = st.columns([4, 1])
            with cols[1]:
                if st.button("Delete", key=f"{key_prefix}_delete"):
                    delete_chart(sc.id, SAVED_CHARTS_PATH)
                    st.rerun()
            return

        df = result.df

        # Determine which feature's column metadata to use.
        feature_id = sc.recipe.get("sources", [None])[0]
        feature = catalog.get(feature_id)
        feature_columns = feature.columns if feature else {}

        # Render figure live.
        invalid_msg: str | None = None
        try:
            filtered_df, hints = apply(view, df, feature_columns)
            fig = render(view, filtered_df, hints)
            st.plotly_chart(fig, use_container_width=True, key=f"{key_prefix}_fig")
        except (ChartViewError, ChartSpecError) as exc:
            invalid_msg = str(exc)
            st.error(f"Chart cannot render: {invalid_msg}")

        # Source caption.
        if result.sources_used:
            src = result.sources_used[0]
            st.caption(
                f"**Source:** `{src['id']}` ({src['name']}) - "
                f"{len(df)} rows."
            )

        # Raw data expander.
        data_columnar = {
            "columns": list(df.columns),
            "rows": df.values.tolist(),
        }
        render_raw_data_expander(
            data_columnar=data_columnar,
            name=view.title,
            key_suffix=key_prefix,
        )

        # Edit expander.
        with st.expander("Edit chart", expanded=False):
            new_view = render_chart_editor(
                chart_view=view,
                df=df,
                feature_columns=feature_columns,
                feature=feature,
                recipe_chart=sc.recipe.get("chart"),
                key_prefix=key_prefix,
            )
            st.session_state[view_state_key] = new_view

            cols = st.columns([1, 1, 4])
            with cols[0]:
                if st.button("Reset", key=f"{key_prefix}_reset"):
                    if feature:
                        st.session_state[view_state_key] = default_chart_view(
                            feature, recipe_chart=sc.recipe.get("chart")
                        )
                        st.rerun()
            with cols[1]:
                if st.button("Save", key=f"{key_prefix}_save", disabled=(invalid_msg is not None)):
                    update_chart_view(sc.id, st.session_state[view_state_key].to_dict(), SAVED_CHARTS_PATH)
                    st.toast(f"Updated '{st.session_state[view_state_key].title}'.")
                    st.rerun()

        # Delete (always last).
        cols = st.columns([4, 1])
        with cols[1]:
            if st.button("Delete", key=f"{key_prefix}_delete"):
                delete_chart(sc.id, SAVED_CHARTS_PATH)
                st.rerun()
```

- [ ] **Step 2: Smoke check**

Run: `pwsh -Command ".venv/Scripts/python.exe -c 'from views.dashboard import render; print(\"ok\")'"` → `ok`.

---

## Task 13: Full smoke test pass

Manual session walking through every smoke check. From `D:\chatbot-analytics`.

- [ ] **Step 1: Start the app**

`pwsh -Command "streamlit run app.py"` → open URL.

- [ ] **Step 2: Bug fix - no wrong `$`**

1. "Show me orders by category." (F012) → bar chart, Y axis shows counts (e.g., 12,450), NO `$` prefix.
2. "Plot GMV by category." → bar with `$` prefix correctly.
3. "Show return rate by category." → bar with `%` suffix, no `$`.

- [ ] **Step 3: Editor open + live preview + save**

4. "Show me MRR over time." → click Edit. Editor opens. Title=Monthly Recurring Revenue, Type=line, X=month, Y=[mrr_usd].
5. Change Title to "MRR Trend Q1-Q4". Chart title updates immediately.
6. Change Type to "bar". Chart re-renders as bar.
7. Click Save. Toast.
8. Switch to Dashboard. Saved chart shows as bar titled "MRR Trend Q1-Q4". No "How this was computed" expander; instead Source caption + Raw data expander + Edit chart expander.

- [ ] **Step 4: Multi-measure**

9. Open editor on a chart with multiple measure columns (e.g., "Show me NRR" - F011).
10. Y multi-select shows all 6 measures. Pick `[starting_mrr, ending_mrr]` → two lines, single shared $ axis.
11. Add `nrr_pct` to Y → dual-axis ($ left, % right).
12. Add `churned_mrr` (still 2 distinct units) → still works.
13. In Column units, override `starting_mrr` unit to `count` → 3 distinct units → inline error, Save disabled.
14. Reset override → Save re-enabled.

- [ ] **Step 5: Filters**

15. Edit a chart. Add filter `month >= 2024-06`. Chart updates to show June onward.
16. Add second filter `mrr_usd > 90000`. Chart updates with AND.
17. Click [x] on second filter. Chart updates.
18. Confirm Raw data expander shows ALL rows (filters are display-only).

- [ ] **Step 6: Column labels and units**

19. Edit MRR chart. Override `mrr_usd` label → "Recurring Revenue". Y-axis title updates.
20. Save. Switch to Dashboard. Reopen editor. Override persists.
21. Override `mrr_usd` unit → `number`. Y-axis loses `$`.
22. Reset override (clear or pick `usd`). Y-axis returns to `$`.

- [ ] **Step 7: Reset to default**

23. Make several changes. Click Reset. Editor reverts; chart re-renders to original.

- [ ] **Step 8: Editing derived source**

24. From a multi-feature derived analysis (e.g., "Compare MRR and Churn"), edit Churn source chart. Type → bar. Save.
25. New SavedChart created. Toast confirms. Switch to Dashboard. New tile appears.

- [ ] **Step 9: Saved-chart editing in dashboard**

26. From Dashboard, edit saved Churn chart from #25. Override `churn_rate_pct` label → "Monthly Churn %". Save.
27. Browser refresh. Open Dashboard. Edit same chart. Override persisted.

- [ ] **Step 10: Dedup on save**

28. "Show me DAU." Save. Toast.
29. Edit Title → "DAU Tracker". Save again.
30. Dashboard shows ONE DAU tile titled "DAU Tracker".

- [ ] **Step 11: Renaming**

31. Dashboard rename a tile via inline name input. Reopen editor → Title shows the rename.

- [ ] **Step 12: Edge - empty Y**

32. Editor: try to clear Y selection. Warning shown, selection reverts.

- [ ] **Step 13: Edge - chart_view incompatible with data**

33. Manually edit `data/saved_charts.json`: set a saved chart's `chart_view.x` to a column that doesn't exist. Open Dashboard. Tile shows red error: "Chart cannot render: …". Open editor, pick valid X. Tile renders.

- [ ] **Step 14: Metadata fallback**

34. Remove `columns` block from one feature in `data/features.json`. Reload features. That feature still charts correctly via inference. Editor dropdowns still populate.
35. Restore the `columns` block.

- [ ] **Step 15: Feature spot-check**

36. Direct chart for every feature. Verify Y-axis formatter:
    - F001 mrr_usd → `$`
    - F002 churn_rate_pct → `%`
    - F003 revenue_usd → `$`
    - F005 cac_usd → `$`
    - F007 ticket_count → count, no `$`
    - F008 dau → count
    - F012 orders → count, no `$` (THE BUG FIX)
    - F015 nps_score → number

- [ ] **Step 16: Reasoning trace + methodology**

37. After any turn, expand Reasoning trace and Methodology block. Both unchanged from v4.

- [ ] **Step 17: Sign-off**

If every check above passes, the implementation is done.

---

## Self-review notes

**Spec coverage:**
- Architecture (recipe + chart_view layered) → tasks 3, 6, 8, 9, 10, 12.
- features.json metadata extension → tasks 1, 2.
- Renderer (unit-aware, multi-measure, dual-axis, no `_apply_executive_theme`) → task 4.
- ChartView dataclass + defaults + apply → task 3.
- Editor UI (title, type, axes, multi-Y, color, labels, units, filters, reset, save) → task 7.
- ChartMeta.chart_view + drop figure → task 6.
- recipe_executor drops figure → task 5.
- SavedChart.chart_view + update_chart_view + dedup-by-recipe-hash with in-place update → task 8.
- Dashboard tiles: Source caption + Raw data + Edit + no "How this was computed" → task 12.
- Conversation save callback wires `(chart_meta, view)` → tasks 9, 11.
- Save derived source as direct recipe → tasks 6 (synthesizes recipe), 11 (saves it).
- Smoke tests for bug fix, editor, multi-measure, filters, dual-axis, dedup, dashboard editing → task 13.

**Type consistency:**
- `ChartView` defined in task 3, consumed in tasks 4, 6, 7, 9, 10, 11, 12.
- `ChartMeta(chart_view: dict, ...)` declared in task 6, consumed in tasks 9, 10, 11. The serialized form (dict) goes through ChartView.from_dict on the UI side.
- `default_chart_view(feature, recipe_chart=None)` defined in task 3, called from tasks 6, 9, 10, 12.
- `apply(view, df, feature_columns)` defined in task 3, called from tasks 9, 10, 12.
- `render(view, df, hints)` defined in task 4, called from tasks 9, 10, 12.
- `save_chart(name, recipe, chart_view, path)` declared in task 8, consumed in task 11.
- `update_chart_view(saved_id, chart_view, path)` declared in task 8, consumed in task 12.
- `on_save: Callable[[ChartMeta, ChartView], None]` in task 9, matched by `_on_save_chart(cm, view)` in task 11.

**No placeholders:** every step contains the actual code or command needed.
