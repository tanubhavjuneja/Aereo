from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy.orm import Session

from ..config import Settings
from ..models import Template
from ..storage import certificate_abs_path, font_rel_path, template_rel_path
from .template_render import FONT_FAMILIES, TemplateRenderError, extract_pdf_placeholders, validate_pdf

MAX_FONT_BYTES = 5 * 1024 * 1024
_FONT_MAGIC = {
    b"\x00\x01\x00\x00": ".ttf",  # TrueType
    b"OTTO": ".otf",  # OpenType/CFF
    b"true": ".ttf",  # TrueType (Apple)
    b"ttcf": ".ttf",  # TrueType collection
}


class TemplateValidationError(ValueError):
    """Raised when an uploaded template or its settings are invalid."""


def create_template(
    session: Session,
    settings: Settings,
    *,
    name: str,
    content: bytes,
    email_subject: Optional[str] = None,
    email_body: Optional[str] = None,
    font_family: str = "helvetica",
    font_size: Optional[int] = None,
    font_color: Optional[str] = None,
    font: Optional[bytes] = None,
) -> Template:
    """Validate an uploaded template, store it on disk and persist its metadata."""
    name = (name or "").strip()
    if not name:
        raise TemplateValidationError("template name must not be blank")

    font_extension: Optional[str] = None
    if font is not None:
        font_extension = validate_font(font)
        if font_family.strip().lower() not in FONT_FAMILIES:
            raise TemplateValidationError(
                f"unknown font_family {font_family!r}; expected one of: {', '.join(sorted(FONT_FAMILIES))}"
            )
    elif font_family.strip().lower() not in FONT_FAMILIES:
        raise TemplateValidationError(
            f"unknown font_family {font_family!r}; expected one of: {', '.join(sorted(FONT_FAMILIES))}"
        )
    if font_size is not None and not 6 <= font_size <= 400:
        raise TemplateValidationError("font_size must be between 6 and 400")
    resolved_color = font_color.strip() if font_color else "#000000"
    if not _valid_color(resolved_color):
        raise TemplateValidationError("font_color must look like #RRGGBB")

    try:
        validate_pdf(content)
        placeholders = extract_pdf_placeholders(content)
    except TemplateRenderError as exc:
        raise TemplateValidationError(str(exc)) from exc

    template_id = uuid4().hex
    template = Template(
        id=template_id,
        name=name,
        file_path=template_rel_path(template_id),
        font_file_path=font_rel_path(template_id, font_extension) if font_extension else None,
        email_subject=(email_subject or "").strip() or None,
        email_body=(email_body or "").strip() or None,
        font_family=font_family.strip().lower(),
        font_size=font_size,
        font_color=resolved_color,
        placeholders=placeholders,
        created_at=datetime.utcnow(),
    )

    abs_path = certificate_abs_path(settings.storage_dir, template.file_path)
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(content)

    if font is not None and template.font_file_path:
        font_abs = certificate_abs_path(settings.storage_dir, template.font_file_path)
        font_abs.parent.mkdir(parents=True, exist_ok=True)
        font_abs.write_bytes(font)

    session.add(template)
    session.commit()
    session.refresh(template)
    return template


def validate_font(content: bytes) -> str:
    """Validate an uploaded TTF/OTF file and return its canonical extension."""
    if not content:
        raise TemplateValidationError("font file is empty")
    if len(content) > MAX_FONT_BYTES:
        raise TemplateValidationError("font file exceeds 5 MB limit")
    extension = _FONT_MAGIC.get(content[:4])
    if extension is None:
        raise TemplateValidationError("font must be a TTF or OTF file")
    try:
        import fitz  # PyMuPDF

        fitz.Font(fontbuffer=content)
    except Exception as exc:
        raise TemplateValidationError("font file could not be loaded") from exc
    return extension


def _valid_color(value: str) -> bool:
    if len(value) != 7 or not value.startswith("#"):
        return False
    try:
        int(value[1:], 16)
    except ValueError:
        return False
    return True
