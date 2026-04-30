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