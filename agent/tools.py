from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

import numpy as np

from charts.renderer import ChartSpecError, spec_to_figure
from datasets.base import Dataset


MAX_ROWS = 1000
SQL_TIMEOUT_SECONDS = 5
ALLOWED_STATS_OPS = {"min", "max", "mean", "median", "std", "sum", "count", "p25", "p75"}

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "run_sql",
            "description": (
                "Run a read-only SELECT query against the active SQLite dataset. "
                "Returns up to 1000 rows. Multi-statement queries are rejected."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "A single SQL SELECT (or WITH ... SELECT) statement.",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "make_chart",
            "description": (
                "Render a Plotly chart from a spec and tabular data. "
                "Use this after run_sql when the user wants a visualization."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "spec": {
                        "type": "object",
                        "description": (
                            "Chart spec. Keys: type (bar|line|scatter|pie|histogram|box|heatmap), "
                            "x, y, color (optional), title, names+values (pie only), z (heatmap only)."
                        ),
                    },
                    "data": {
                        "type": "object",
                        "description": "Tabular data with keys 'columns' (list of names) and 'rows' (list of lists).",
                    },
                },
                "required": ["spec", "data"],
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
                    "values": {
                        "type": "array",
                        "items": {"type": "number"},
                    },
                    "ops": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["values", "ops"],
            },
        },
    },
]


def dispatch(name: str, args: dict, dataset: Dataset, turn) -> dict:
    """Route a tool call to the right impl. Returns a JSON-serializable dict."""
    try:
        if name == "run_sql":
            return run_sql(args.get("query", ""), dataset)
        if name == "make_chart":
            return make_chart(args.get("spec", {}), args.get("data", {}), turn)
        if name == "compute_stats":
            return compute_stats(args.get("values", []), args.get("ops", []))
        return {"ok": False, "error": f"Unknown tool: {name}"}
    except Exception as exc:  # last-resort guard so the loop keeps going
        return {"ok": False, "error": f"Tool {name} crashed: {exc}"}
    

# ---------- run_sql ----------

_SELECT_PATTERN = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)


def run_sql(query: str, dataset: Dataset) -> dict:
    if not isinstance(query, str) or not query.strip():
        return {"ok": False, "error": "Query is empty."}

    if not _SELECT_PATTERN.match(query):
        return {"ok": False, "error": "Only SELECT or WITH ... SELECT queries are allowed."}

    if _has_multiple_statements(query):
        return {"ok": False, "error": "Multiple SQL statements are not allowed."}

    conn = sqlite3.connect(f"file:{dataset.db_path}?mode=ro", uri=True)
    try:
        _install_timeout(conn, SQL_TIMEOUT_SECONDS)
        cursor = conn.execute(query)
        rows = cursor.fetchmany(MAX_ROWS + 1)
        truncated = len(rows) > MAX_ROWS
        rows = rows[:MAX_ROWS]
        columns = [d[0] for d in cursor.description] if cursor.description else []
        return {
            "ok": True,
            "columns": columns,
            "rows": [list(r) for r in rows],
            "row_count": len(rows),
            "truncated": truncated,
        }
    except sqlite3.OperationalError as exc:
        return {"ok": False, "error": f"SQL error: {exc}"}
    except sqlite3.DatabaseError as exc:
        return {"ok": False, "error": f"Database error: {exc}"}
    finally:
        conn.close()


def _has_multiple_statements(query: str) -> bool:
    # Strip trailing whitespace and a single trailing semicolon, then
    # if any other semicolons remain outside string literals, reject.
    stripped = query.strip().rstrip(";").strip()
    in_single = False
    in_double = False
    for ch in stripped:
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == ";" and not in_single and not in_double:
            return True
    return False


def _install_timeout(conn: sqlite3.Connection, seconds: int) -> None:
    import time

    deadline = time.monotonic() + seconds

    def _abort_if_overdue() -> int:
        return 1 if time.monotonic() > deadline else 0

    # progress handler is invoked every N opcodes; returning non-zero aborts.
    conn.set_progress_handler(_abort_if_overdue, 1000)


# ---------- make_chart ----------

def make_chart(spec: dict, data: dict, turn) -> dict:
    try:
        figure = spec_to_figure(spec, data)
    except ChartSpecError as exc:
        return {"ok": False, "error": f"Chart spec invalid: {exc}"}
    except Exception as exc:
        return {"ok": False, "error": f"Chart build failed: {exc}"}

    chart_id = len(turn.charts)
    turn.charts.append(figure)

    # Also stage the underlying table so the UI can offer a "raw data" expander.
    turn.tables.append(
        {
            "title": spec.get("title") or f"Chart {chart_id + 1} data",
            "columns": data.get("columns", []),
            "rows": data.get("rows", []),
        }
    )

    return {"ok": True, "chart_id": chart_id, "title": spec.get("title", "")}


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

    result: dict[str, Any] = {op: impl[op](arr) for op in ops}
    return {"ok": True, "stats": result}


# ---------- helpers ----------

def parse_tool_arguments(raw: str | dict | None) -> dict:
    """Azure OpenAI returns tool arguments as a JSON string. Parse defensively."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}