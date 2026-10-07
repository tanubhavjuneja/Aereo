from __future__ import annotations

import re
from typing import Any, Optional, Sequence

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

MAX_VALUE_LENGTH = 300


def primary_field(fields: Sequence[dict]) -> dict:
    for field in fields:
        if field.get("role") == "primary":
            return field
    return fields[0]


def email_field(fields: Sequence[dict]) -> Optional[dict]:
    for field in fields:
        if field.get("role") == "email":
            return field
    return None


def normalize_fields(field_defs: Sequence[dict]) -> list[dict]:
    """Return field definitions with exactly one ``primary`` role."""
    fields = [dict(field) for field in field_defs]
    if not any(field.get("role") == "primary" for field in fields):
        fields[0]["role"] = "primary"
    return fields


def _coerce(value: Any) -> Optional[str]:
    """Coerce a JSON scalar to the text stored on the certificate."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value.strip()
    return None


def validate_recipient(
    row: dict,
    fields: Sequence[dict],
    max_primary_length: int = 120,
) -> tuple[Optional[dict[str, str]], Optional[str]]:
    """Validate one recipient row against the declared field definitions.

    Returns ``(cleaned_data, None)`` for a valid row, otherwise
    ``(partial_or_empty_data, error_message)``.
    """
    known_keys = {field["key"] for field in fields}
    for key in row:
        if key not in known_keys:
            return {}, f"unknown field '{key}'"

    cleaned: dict[str, str] = {}
    primary_key = primary_field(fields)["key"]

    for field in fields:
        key = field["key"]
        raw = row.get(key)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            if field.get("required", True):
                return {}, f"field '{key}' is required"
            continue

        value = _coerce(raw)
        if value is None:
            return {}, f"field '{key}' must be a text or number value"

        max_length = max_primary_length if key == primary_key else MAX_VALUE_LENGTH
        if len(value) > max_length:
            return {}, f"field '{key}' is too long (maximum {max_length} characters)"
        if CONTROL_CHARS_RE.search(value):
            return {}, f"field '{key}' contains unsupported control characters"
        if field.get("role") == "email":
            if not EMAIL_RE.match(value):
                return {}, f"field '{key}' is not a valid e-mail address"
            value = value.lower()

        cleaned[key] = value

    return cleaned, None
