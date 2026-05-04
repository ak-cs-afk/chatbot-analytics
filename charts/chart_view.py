from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

import pandas as pd

from charts.units import ALLOWED_UNITS, CURRENCY_UNITS
from features.loader import ColumnMeta, Feature

logger = logging.getLogger(__name__)


# ---------- Constants ----------

ALLOWED_FILTER_OPS = {"==", "!=", "<", "<=", ">", ">=", "in", "between"}

ALLOWED_SORT_DIRS = {"asc", "desc"}

SINGLE_MEASURE_TYPES = {
    "pie", "heatmap", "funnel", "histogram", "box", "horizontal_bar", "indicator",
}
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
    column_label_map: dict[str, str] = field(default_factory=dict)
    column_unit_map: dict[str, str] = field(default_factory=dict)


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
    subtitle: str = ""
    sort_by: str | None = None
    sort_dir: str = "desc"

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
            "subtitle": self.subtitle,
            "sort_by": self.sort_by,
            "sort_dir": self.sort_dir,
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

        sort_dir = raw.get("sort_dir", "desc") or "desc"
        if sort_dir not in ALLOWED_SORT_DIRS:
            raise ChartViewError(
                f"chart_view.sort_dir '{sort_dir}' not in {sorted(ALLOWED_SORT_DIRS)}."
            )

        return cls(
            title=title,
            type=ctype,
            x=x,
            y=list(y_raw),
            color=raw.get("color"),
            column_labels=dict(labels_raw),
            column_units=dict(units_raw),
            filters=filters,
            subtitle=raw.get("subtitle", "") or "",
            sort_by=raw.get("sort_by"),
            sort_dir=sort_dir,
        )


# ---------- Default chart_view factory ----------

def default_chart_view(
    feature: Feature,
    recipe_chart: dict | None = None,
) -> ChartView:
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
        subtitle="",
        sort_by=None,
        sort_dir="desc",
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
    """Filter, sort, and resolve axis hints + label/unit maps for the renderer."""
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

    if view.sort_by and view.sort_by in out.columns:
        out = out.sort_values(
            by=view.sort_by, ascending=(view.sort_dir == "asc")
        ).reset_index(drop=True)
    elif view.sort_by:
        logger.warning(
            "ChartView sort_by '%s' not in result columns; skipping.", view.sort_by
        )

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

    # Build complete maps for every column referenced by the view.
    referenced = {view.x, *view.y}
    if view.color:
        referenced.add(view.color)
    column_label_map: dict[str, str] = {}
    column_unit_map: dict[str, str] = {}
    for col in referenced:
        column_label_map[col] = _resolve_label(col, view, feature_columns)
        column_unit_map[col] = _resolve_unit(col, view, feature_columns)

    hints = AxisHints(
        x_unit=x_unit,
        x_label=x_label,
        left_y_unit=left_unit,
        left_y_label=left_y_label,
        right_y_unit=right_unit,
        right_y_label=right_y_label,
        column_label_map=column_label_map,
        column_unit_map=column_unit_map,
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
