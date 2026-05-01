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
    feature = catalog[recipe.sources[0]]
    name = feature.name
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
                data_columnar=feature.data_columnar,
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