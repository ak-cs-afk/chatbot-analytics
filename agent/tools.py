from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import plotly.graph_objects as go

from charts.renderer import ChartSpecError, spec_to_figure
from features.catalog import find_features
from features.loader import (
    Feature,
    FeatureNotFoundError,
    load_features,
)


ALLOWED_STATS_OPS = {"min", "max", "mean", "median", "std", "sum", "count", "p25", "p75"}


# ---------- Public types ----------

@dataclass
class ChartMeta:
    chart_id: int
    name: str
    feature_id: str
    spec: dict
    figure: go.Figure


# ---------- Tool schemas (OpenAI function-calling format) ----------

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_feature_data",
            "description": (
                "Fetch the data rows and chart hints for a single feature by its ID "
                "(e.g. 'F001'). Returns columns, rows, and the suggested chart type/axes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "feature_id": {
                        "type": "string",
                        "description": "Feature ID like 'F001', 'F002', etc.",
                    }
                },
                "required": ["feature_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "make_chart",
            "description": (
                "Build a Plotly chart for a feature. Defaults to the feature's "
                "suggested chart and axes; pass spec_override to swap chart type, "
                "change axes, or add a color grouping. Always tied to a feature_id "
                "so saved dashboards re-render from latest data."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "feature_id": {"type": "string"},
                    "title": {
                        "type": "string",
                        "description": "Default name shown above the chart. Will be the chart's display name.",
                    },
                    "spec_override": {
                        "type": "object",
                        "description": (
                            "Optional partial spec to override defaults. Keys: type "
                            "(bar|line|scatter|pie|histogram|box|heatmap|funnel|"
                            "grouped_bar|horizontal_bar), x, y, y_fields, color, "
                            "names, values, z."
                        ),
                    },
                },
                "required": ["feature_id", "title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compute_stats",
            "description": (
                "Compute descriptive statistics on a list of numeric values. "
                "Allowed ops: min, max, mean, median, std, sum, count, p25, p75."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "values": {"type": "array", "items": {"type": "number"}},
                    "ops": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["values", "ops"],
            },
        },
    },
]


# ---------- Public dispatcher ----------

def dispatch(name: str, args: dict, turn) -> dict:
    """Route a tool call. Returns a JSON-serializable dict."""
    try:
        if name == "get_feature_data":
            return get_feature_data(args.get("feature_id", ""))
        if name == "make_chart":
            return make_chart(
                feature_id=args.get("feature_id", ""),
                title=args.get("title", ""),
                spec_override=args.get("spec_override"),
                turn=turn,
            )
        if name == "compute_stats":
            return compute_stats(args.get("values", []), args.get("ops", []))
        return {"ok": False, "error": f"Unknown tool: {name}"}
    except Exception as exc:
        return {"ok": False, "error": f"Tool {name} crashed: {exc}"}


# ---------- get_feature_data ----------

def get_feature_data(feature_id: str) -> dict:
    if not feature_id:
        return {"ok": False, "error": "feature_id is required."}
    catalog = load_features()
    if feature_id not in catalog:
        suggestions = [
            {"id": f.id, "name": f.name}
            for f in find_features(feature_id)[:5]
        ]
        return {
            "ok": False,
            "error": f"Unknown feature_id '{feature_id}'.",
            "suggestions": suggestions,
        }

    feature = catalog[feature_id]
    return {
        "ok": True,
        "feature": {
            "id": feature.id,
            "name": feature.name,
            "description": feature.description,
            "category": feature.category,
            "suggested_chart": feature.suggested_chart,
            "x_field": feature.x_field,
            "y_field": feature.y_field,
            "y_fields": list(feature.y_fields) if feature.y_fields else None,
        },
        "data": feature.data_columnar,
        "row_count": feature.row_count,
    }


# ---------- make_chart ----------

def make_chart(
    feature_id: str,
    title: str,
    spec_override: dict | None,
    turn,
) -> dict:
    if not feature_id:
        return {"ok": False, "error": "feature_id is required."}

    try:
        feature = load_features()[feature_id]
    except KeyError:
        suggestions = [
            {"id": f.id, "name": f.name}
            for f in find_features(feature_id)[:5]
        ]
        return {
            "ok": False,
            "error": f"Unknown feature_id '{feature_id}'.",
            "suggestions": suggestions,
        }

    spec = build_default_spec(feature)
    if spec_override:
        spec.update(spec_override)
    spec["title"] = title

    try:
        figure = spec_to_figure(spec, feature.data_columnar)
    except ChartSpecError as exc:
        return {"ok": False, "error": f"Chart spec invalid: {exc}"}

    chart_id = len(turn.charts)
    turn.charts.append(
        ChartMeta(
            chart_id=chart_id,
            name=title or feature.name,
            feature_id=feature.id,
            spec=spec,
            figure=figure,
        )
    )
    return {"ok": True, "chart_id": chart_id, "title": title}


def build_default_spec(feature: Feature) -> dict:
    spec: dict[str, Any] = {}
    if feature.suggested_chart:
        spec["type"] = feature.suggested_chart
    if feature.x_field:
        spec["x"] = feature.x_field
    if feature.y_field:
        spec["y"] = feature.y_field
    if feature.y_fields:
        spec["y_fields"] = list(feature.y_fields)
    return spec


# ---------- compute_stats ----------

def compute_stats(values: list, ops: list) -> dict:
    if not isinstance(values, list) or not values:
        return {"ok": False, "error": "'values' must be a non-empty list of numbers."}
    if not isinstance(ops, list) or not ops:
        return {"ok": False, "error": "'ops' must be a non-empty list of strings."}

    bad_ops = [op for op in ops if op not in ALLOWED_STATS_OPS]
    if bad_ops:
        return {
            "ok": False,
            "error": f"Unknown ops: {bad_ops}. Allowed: {sorted(ALLOWED_STATS_OPS)}",
        }

    try:
        arr = np.asarray(values, dtype=float)
    except (TypeError, ValueError) as exc:
        return {"ok": False, "error": f"Cannot convert values to numbers: {exc}"}

    impl = {
        "min": lambda a: float(np.min(a)),
        "max": lambda a: float(np.max(a)),
        "mean": lambda a: float(np.mean(a)),
        "median": lambda a: float(np.median(a)),
        "std": lambda a: float(np.std(a, ddof=1)) if a.size > 1 else 0.0,
        "sum": lambda a: float(np.sum(a)),
        "count": lambda a: int(a.size),
        "p25": lambda a: float(np.percentile(a, 25)),
        "p75": lambda a: float(np.percentile(a, 75)),
    }
    return {"ok": True, "stats": {op: impl[op](arr) for op in ops}}


# ---------- arg parser (kept from v1, used by client.py) ----------

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