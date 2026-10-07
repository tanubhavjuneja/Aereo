from __future__ import annotations

import csv
import io
import re
from typing import Any, Dict, List, Optional, Tuple

MAX_KEY_LENGTH = 40
IGNORE_MARKERS = ("", "-", "_")
EMAIL_HEADER_RE = re.compile(r"^(e-?mail|email_address|mail)$", re.IGNORECASE)
PRIMARY_KEYS = ("name", "full_name", "recipient_name", "participant", "participant_name", "student")


class CsvImportError(ValueError):
    """Raised for structurally invalid CSV uploads (HTTP 422 at the route)."""


def decode_csv(content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("latin-1")


def parse_recipients_csv(content: bytes, *, max_rows: int) -> Tuple[List[str], List[Dict[str, str]]]:
    """Parse a CSV file into (headers, rows). Blank rows are skipped."""
    if not content or not content.strip():
        raise CsvImportError("CSV file is empty")
    reader = csv.DictReader(io.StringIO(decode_csv(content)))
    raw_headers = reader.fieldnames
    if not raw_headers:
        raise CsvImportError("CSV has no header row")
    headers = [(h or "").strip() for h in raw_headers]
    if any(not h for h in headers):
        raise CsvImportError("CSV has an empty header name")
    lowered = [h.lower() for h in headers]
    if len(set(lowered)) != len(lowered):
        raise CsvImportError("CSV has duplicate header names")
    rows: List[Dict[str, str]] = []
    for raw in reader:
        if len(rows) >= max_rows:
            raise CsvImportError(f"CSV has more than {max_rows} rows")
        row = {(k or "").strip(): (v or "").strip() for k, v in raw.items() if k is not None}
        if not any(row.values()):
            continue
        rows.append(row)
    if not rows:
        raise CsvImportError("CSV has no recipient rows")
    return headers, rows


def normalize_key(header: str) -> str:
    key = re.sub(r"[^a-zA-Z0-9]+", "_", header.strip()).strip("_").lower()
    if not key:
        return "column"
    if not key[0].isalpha():
        key = f"f_{key}"
    return key[:MAX_KEY_LENGTH]


def _is_email_header(header: str) -> bool:
    return bool(EMAIL_HEADER_RE.match(header.strip()) or EMAIL_HEADER_RE.match(normalize_key(header)))


def build_fields_and_rows(
    headers: List[str],
    rows: List[Dict[str, str]],
    *,
    fields: Optional[List[dict]],
    column_map: Optional[Dict[str, Optional[str]]],
) -> Tuple[List[dict], List[dict]]:
    """Infer/apply field definitions and transform rows into recipient dicts.

    Raises :class:`CsvImportError` for structural problems so routes can 422
    before any job is created.
    """
    column_map = dict(column_map or {})

    if fields:
        field_keys = [f["key"] for f in fields]
        resolved: Dict[str, Optional[str]] = {}
        unmapped: List[str] = []
        for header in headers:
            if header in column_map:
                mapped = (column_map[header] or "").strip()
                if mapped in IGNORE_MARKERS:
                    resolved[header] = None
                    continue
                if mapped not in field_keys:
                    raise CsvImportError(
                        f"column '{header}' is mapped to unknown field '{mapped}'; "
                        f"known fields: {', '.join(field_keys)}"
                    )
                resolved[header] = mapped
                continue
            if header in field_keys:
                resolved[header] = header
                continue
            normalized = normalize_key(header)
            if normalized in field_keys:
                resolved[header] = normalized
                continue
            if _is_email_header(header) and "email" in field_keys:
                resolved[header] = "email"
                continue
            unmapped.append(header)
        if unmapped:
            columns = ", ".join(f"'{name}'" for name in unmapped)
            raise CsvImportError(
                f"column(s) {columns} do not match any field; map them with column_map "
                f"(known fields: {', '.join(field_keys)})"
            )
        field_dicts = [f for f in fields]
    else:
        resolved = {}
        field_dicts = []
        seen_keys: Dict[str, str] = {}
        for header in headers:
            if header in column_map:
                key = (column_map[header] or "").strip()
                if key in IGNORE_MARKERS:
                    resolved[header] = None
                    continue
            else:
                key = normalize_key(header)
            if key in seen_keys:
                raise CsvImportError(f"headers '{seen_keys[key]}' and '{header}' both map to field '{key}'")
            seen_keys[key] = header
            resolved[header] = key
            field_dicts.append(
                {
                    "key": key,
                    "label": header,
                    "required": False,
                    "role": "text",
                    "visible": True,
                }
            )
        # Refine roles: email detection first, then primary.
        for entry in field_dicts:
            if _is_email_header(str(entry["key"])):
                entry["role"] = "email"
                entry["required"] = True
                entry["visible"] = False
        primaries = [e for e in field_dicts if e["role"] == "primary"]
        if not primaries:
            primary = next(
                (e for e in field_dicts if str(e["key"]) in PRIMARY_KEYS),
                next((e for e in field_dicts if e["role"] == "text"), None),
            )
            if primary is not None:
                primary["role"] = "primary"
                primary["required"] = True
        if not field_dicts:
            raise CsvImportError("CSV produced no usable columns")

    recipient_rows: List[dict] = []
    for row in rows:
        recipient: Dict[str, Any] = {}
        for header, field_key in resolved.items():
            if field_key is None:
                continue
            recipient[field_key] = row.get(header, "")
        recipient_rows.append(recipient)
    return field_dicts, recipient_rows
