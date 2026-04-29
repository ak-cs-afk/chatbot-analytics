from __future__ import annotations

from features.loader import Feature, load_features


def build_catalog_text(path: str | None = None) -> str:
    catalog = load_features(path) if path else load_features()
    lines: list[str] = []
    for feature in catalog.values():
        suggested = feature.suggested_chart or "none (model should infer)"
        axes_bits = []
        if feature.x_field:
            axes_bits.append(f"x={feature.x_field}")
        if feature.y_field:
            axes_bits.append(f"y={feature.y_field}")
        if feature.y_fields:
            axes_bits.append(f"y_fields={list(feature.y_fields)}")
        axes = ", ".join(axes_bits) if axes_bits else "no axis hints"

        tags = ", ".join(feature.tags) if feature.tags else "(none)"

        lines.append(
            f"{feature.id} - {feature.name} ({feature.category})\n"
            f"   {feature.description}\n"
            f"   Tags: {tags}. Suggested chart: {suggested} ({axes})."
        )
    return "\n\n".join(lines)


def find_features(query: str, path: str | None = None) -> list[Feature]:
    """Case-insensitive substring match across id, name, description, tags."""
    catalog = load_features(path) if path else load_features()
    needle = (query or "").strip().lower()
    if not needle:
        return []

    matches: list[Feature] = []
    for feature in catalog.values():
        haystacks = [
            feature.id.lower(),
            feature.name.lower(),
            feature.description.lower(),
            *(t.lower() for t in feature.tags),
        ]
        if any(needle in h for h in haystacks):
            matches.append(feature)
    return matches
