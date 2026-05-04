from __future__ import annotations


CURRENCY_UNITS: set[str] = {"usd", "inr", "eur", "gbp", "jpy", "cad", "aud"}

CURRENCY_SYMBOLS: dict[str, str] = {
    "usd": "$",
    "inr": "₹",
    "eur": "€",
    "gbp": "£",
    "jpy": "¥",
    "cad": "CA$",
    "aud": "A$",
}

NON_CURRENCY_UNITS: set[str] = {
    "pct", "count", "hours", "days", "date", "string", "number",
}

ALLOWED_UNITS: set[str] = CURRENCY_UNITS | NON_CURRENCY_UNITS


# ---- Format / Currency UI split ----

FORMAT_LABELS: list[str] = [
    "Number", "Currency", "Percent", "Count", "Hours", "Days", "Date", "Text",
]

_NON_CURRENCY_FORMAT_TO_UNIT: dict[str, str] = {
    "Number": "number",
    "Percent": "pct",
    "Count": "count",
    "Hours": "hours",
    "Days": "days",
    "Date": "date",
    "Text": "string",
}

_NON_CURRENCY_UNIT_TO_FORMAT: dict[str, str] = {
    v: k for k, v in _NON_CURRENCY_FORMAT_TO_UNIT.items()
}


def unit_to_format(unit: str) -> tuple[str, str]:
    """Map a unit string to (format_label, currency_code).

    For currency units, returns ("Currency", "<unit>").
    For others, returns (format_label, "").
    """
    if unit in CURRENCY_UNITS:
        return ("Currency", unit)
    return (_NON_CURRENCY_UNIT_TO_FORMAT.get(unit, "Number"), "")


def format_to_unit(format_label: str, currency_code: str = "") -> str:
    """Inverse of unit_to_format."""
    if format_label == "Currency":
        return currency_code if currency_code in CURRENCY_UNITS else "usd"
    return _NON_CURRENCY_FORMAT_TO_UNIT.get(format_label, "number")


def currency_label(unit: str) -> str:
    """Human-readable currency label like 'USD ($)' for selectbox display."""
    if unit in CURRENCY_SYMBOLS:
        return f"{unit.upper()} ({CURRENCY_SYMBOLS[unit]})"
    return unit.upper()
