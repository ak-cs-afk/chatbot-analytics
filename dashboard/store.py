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