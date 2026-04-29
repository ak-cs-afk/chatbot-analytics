from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

DEFAULT_PATH = "data/features.json"


class FeaturesValidationError(ValueError):
    """Raised when features.json is missing required fields."""


class FeatureNotFoundError(KeyError):
    """Raised when a feature_id is not in the catalog."""


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
    data_columnar: dict[str, list]   # {"columns": [...], "rows": [[...]]}
    raw_data: tuple[dict, ...]       # original list-of-dicts, kept for reference

    @property
    def row_count(self) -> int:
        return len(self.data_columnar["rows"])


@lru_cache(maxsize=1)
def load_features(path: str = DEFAULT_PATH) -> dict[str, Feature]:
    """Load and validate the catalog. Cached after first call."""
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
    """Clear the cache and reload. Used by sidebar 'Reload data' button."""
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
        raise FeaturesValidationError(
            f"Feature entry missing keys {missing}: {entry}"
        )

    rows = entry["data"]
    if not isinstance(rows, list) or not rows:
        raise FeaturesValidationError(
            f"Feature {entry['feature_id']} has empty or invalid data."
        )

    columns = _columns_from_rows(rows)
    data_columnar = {
        "columns": columns,
        "rows": [[row.get(c) for c in columns] for row in rows],
    }

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
    )


def _columns_from_rows(rows: list[dict]) -> list[str]:
    """Use the union of keys preserving first-appearance order."""
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