from __future__ import annotations

import re
from pathlib import Path

_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def certificate_rel_path(job_id: str, certificate_id: str) -> str:
    """Path stored in the database, relative to the configured storage dir."""
    return f"{job_id}/{certificate_id}.pdf"


def template_rel_path(template_id: str) -> str:
    return f"templates/{template_id}.pdf"


def font_rel_path(template_id: str, extension: str) -> str:
    extension = extension if extension.startswith(".") else f".{extension}"
    if extension not in (".ttf", ".otf"):
        raise ValueError(f"unsupported font extension: {extension}")
    return f"fonts/{template_id}{extension}"


def certificate_abs_path(storage_root: Path, rel_path: str) -> Path:
    return (storage_root / rel_path).resolve()


def safe_filename(*parts: str, suffix: str = ".pdf") -> str:
    raw = "-".join(p for p in parts if p)
    cleaned = _UNSAFE_CHARS.sub("_", raw).strip("._")
    return (cleaned[:80] or "certificate") + suffix
