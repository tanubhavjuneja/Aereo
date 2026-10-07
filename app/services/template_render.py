from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF

from .placeholders import extract_placeholders

logger = logging.getLogger(__name__)

#: User facing font names mapped to the PDF base-14 names used by PyMuPDF.
FONT_FAMILIES = {
    "helvetica": "helv",
    "helvetica-bold": "hebo",
    "helvetica-oblique": "heit",
    "helvetica-bold-oblique": "hebi",
    "times": "tiro",
    "times-bold": "tibo",
    "times-italic": "tiit",
    "times-bold-italic": "tibi",
    "courier": "cour",
    "courier-bold": "cobo",
    "courier-oblique": "coit",
}

MAX_TEMPLATE_BYTES = 10 * 1024 * 1024


class TemplateRenderError(ValueError):
    """Raised when an uploaded template cannot be read or rendered."""


def _color(value: str) -> tuple[float, float, float]:
    value = value.strip()
    if len(value) == 4:  # #rgb shorthand
        value = "#" + "".join(ch * 2 for ch in value[1:])
    if len(value) != 7 or not value.startswith("#"):
        raise TemplateRenderError(f"font_color must look like #RRGGBB (got {value!r})")
    try:
        return tuple(int(value[i : i + 2], 16) / 255 for i in (1, 3, 5))  # type: ignore[return-value]
    except ValueError as exc:
        raise TemplateRenderError(f"font_color must look like #RRGGBB (got {value!r})") from exc


def validate_pdf(content: bytes) -> None:
    """Basic structural validation of an uploaded template file."""
    if not content:
        raise TemplateRenderError("template file is empty")
    if len(content) > MAX_TEMPLATE_BYTES:
        raise TemplateRenderError(f"template file exceeds {MAX_TEMPLATE_BYTES // (1024 * 1024)} MB limit")
    if not content.startswith(b"%PDF"):
        raise TemplateRenderError("template must be a PDF file")
    try:
        document = fitz.open(stream=content, filetype="pdf")
    except Exception as exc:
        raise TemplateRenderError("template PDF could not be opened") from exc
    try:
        if document.needs_pass:
            raise TemplateRenderError("template PDF is password protected")
        if document.page_count < 1:
            raise TemplateRenderError("template PDF has no pages")
    finally:
        document.close()


def extract_pdf_placeholders(content: bytes) -> list[str]:
    """Find ``{placeholder}`` markers in the text of an uploaded PDF."""
    document = fitz.open(stream=content, filetype="pdf")
    try:
        text = "\n".join(page.get_text() for page in document)
    finally:
        document.close()
    return extract_placeholders(text)


def render_uploaded_template(
    template_path: Path,
    output_path: Path,
    *,
    context: dict[str, str],
    font_family: str = "helvetica",
    font_size: Optional[int] = None,
    font_color: str = "#000000",
    font_file: Optional[Path] = None,
    image_placeholders: Optional[dict[str, bytes]] = None,
) -> Path:
    """Copy the uploaded template PDF, replacing ``{placeholder}`` markers.

    Everything except the placeholders is preserved exactly as authored:
    each marker is located with a text search, the original marker glyphs are
    redacted away, and the recipient value is drawn in their place. Keys in
    ``image_placeholders`` (e.g. ``qr``) are drawn as a PNG image instead of
    text.
    """
    template_path = Path(template_path)
    output_path = Path(output_path)
    if not template_path.is_file():
        raise TemplateRenderError(f"template file not found: {template_path}")

    custom_font: Optional[fitz.Font] = None
    fontname: str
    if font_file is not None:
        font_file = Path(font_file)
        if not font_file.is_file():
            raise TemplateRenderError(f"font file not found: {font_file}")
        try:
            custom_font = fitz.Font(fontfile=str(font_file))
        except Exception as exc:
            raise TemplateRenderError("font file could not be loaded") from exc
        fontname = "custom"
    else:
        resolved = FONT_FAMILIES.get(font_family.strip().lower())
        if resolved is None:
            raise TemplateRenderError(
                f"unknown font_family {font_family!r}; expected one of: {', '.join(sorted(FONT_FAMILIES))}"
            )
        fontname = resolved
    color = _color(font_color)
    image_placeholders = image_placeholders or {}

    document = fitz.open(str(template_path))
    try:
        if document.needs_pass:
            raise TemplateRenderError("template PDF is password protected")

        for page in document:
            replacements: list[tuple[fitz.Rect, str]] = []
            images: list[tuple[fitz.Rect, bytes]] = []
            for key, value in context.items():
                if key in image_placeholders:
                    continue
                for rect in page.search_for(f"{{{key}}}"):
                    replacements.append((rect, str(value)))
            for key, payload in image_placeholders.items():
                for rect in page.search_for(f"{{{key}}}"):
                    images.append((rect, payload))
            if not replacements and not images:
                continue
            for rect, _value in replacements:
                page.add_redact_annot(rect, fill=None, cross_out=False)
            for rect, _payload in images:
                page.add_redact_annot(rect, fill=None, cross_out=False)
            page.apply_redactions(images=0)
            for rect, payload in images:
                try:
                    page.insert_image(rect, stream=payload, keep_proportion=True)
                except Exception:
                    logger.warning("failed to embed image placeholder in %s", template_path, exc_info=True)
            for rect, value in replacements:
                _draw_value(page, rect, value, fontname, font_size, color, custom_font=custom_font, font_file=font_file)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.exists():
            output_path.unlink()
        document.save(str(output_path))
    finally:
        document.close()
    return output_path


def _text_length(text: str, fontname: str, fontsize: float, custom_font: Optional[fitz.Font]) -> float:
    if custom_font is not None:
        return custom_font.text_length(text, fontsize=fontsize)
    return fitz.get_text_length(text, fontname=fontname, fontsize=fontsize)


def _draw_value(
    page: fitz.Page,
    rect: fitz.Rect,
    value: str,
    fontname: str,
    font_size: Optional[int],
    color: tuple[float, float, float],
    *,
    custom_font: Optional[fitz.Font] = None,
    font_file: Optional[Path] = None,
) -> None:
    lines = str(value).splitlines() or [""]
    lines = [line for line in lines if line.strip()] or [""]
    if not str(value).strip():
        return  # declared but empty optional value: leave the box blank

    if font_size:
        size = float(font_size)
    else:
        longest = max(lines, key=lambda line: _text_length(line, fontname, 100, custom_font))
        longest_width = _text_length(longest, fontname, 100, custom_font)
        width_size = 100.0 * (rect.width * 0.98) / max(longest_width, 1e-6)
        height_size = rect.height * 0.95
        size = max(4.0, min(width_size, height_size))

    line_height = size * 1.15
    block_height = line_height * len(lines)
    y = rect.y0 + max(0.0, (rect.height - block_height) / 2) + size * 0.8
    for line in lines:
        kwargs: dict = {"fontname": fontname, "fontsize": size, "color": color}
        if font_file is not None:
            kwargs["fontfile"] = str(font_file)
        page.insert_text((rect.x0, y), line, **kwargs)
        y += line_height
