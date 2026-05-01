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