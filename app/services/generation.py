from __future__ import annotations

import io
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas as pdf_canvas

from .validation import primary_field

PAGE_WIDTH, PAGE_HEIGHT = landscape(A4)
INK = HexColor("#1F2A44")
ACCENT = HexColor("#B08D57")
MUTED = HexColor("#5A6478")

CONTENT_WIDTH = PAGE_WIDTH - 160
DETAIL_WIDTH = PAGE_WIDTH - 220
DETAIL_TOP = 265
DETAIL_BOTTOM = 175


def _fit_size(text: str, font: str, max_size: float, min_size: float, max_width: float) -> float:
    size = max_size
    while size > min_size and stringWidth(text, font, size) > max_width:
        size -= 1
    return size


def _detail_lines(data: dict, fields: Sequence[dict], primary_key: str) -> list[tuple[str, str]]:
    lines: list[tuple[str, str]] = []
    for field in fields:
        key = field["key"]
        if key == primary_key or not field.get("visible", True):
            continue
        value = str(data.get(key, "")).strip()
        if not value:
            continue
        lines.append((str(field["label"]), value))
    return lines


def render_certificate(
    output_path: Path,
    *,
    data: dict,
    fields: Sequence[dict],
    event_name: str,
    event_date: Optional[date],
    certificate_id: str,
    template_name: str,
    certificate_code: Optional[str] = None,
    qr_png: Optional[bytes] = None,
    verification_url: Optional[str] = None,
    issued_on: Optional[date] = None,
) -> Path:
    """Render one certificate PDF driven by the job's dynamic field definitions."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    primary = primary_field(fields)
    primary_key = primary["key"]
    primary_value = str(data.get(primary_key, "")).strip() or "—"

    canvas = pdf_canvas.Canvas(str(output_path), pagesize=(PAGE_WIDTH, PAGE_HEIGHT))
    canvas.setTitle(f"Certificate {certificate_id}")

    # --- borders -----------------------------------------------------------
    canvas.setStrokeColor(INK)
    canvas.setLineWidth(3)
    canvas.rect(24, 24, PAGE_WIDTH - 48, PAGE_HEIGHT - 48)
    canvas.setStrokeColor(ACCENT)
    canvas.setLineWidth(1)
    canvas.rect(34, 34, PAGE_WIDTH - 68, PAGE_HEIGHT - 68)

    # --- title -------------------------------------------------------------
    canvas.setFillColor(INK)
    canvas.setFont("Helvetica-Bold", 28)
    canvas.drawCentredString(PAGE_WIDTH / 2, PAGE_HEIGHT - 90, "CERTIFICATE OF ACHIEVEMENT")

    canvas.setStrokeColor(ACCENT)
    canvas.setLineWidth(2)
    canvas.line(PAGE_WIDTH / 2 - 120, PAGE_HEIGHT - 104, PAGE_WIDTH / 2 + 120, PAGE_HEIGHT - 104)

    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 13)
    canvas.drawCentredString(PAGE_WIDTH / 2, PAGE_HEIGHT - 140, "This is to certify that")

    # --- primary value (recipient name / team / ...) ------------------------
    name_size = _fit_size(primary_value, "Helvetica-Bold", 36, 16, CONTENT_WIDTH)
    canvas.setFillColor(INK)
    canvas.setFont("Helvetica-Bold", name_size)
    canvas.drawCentredString(PAGE_WIDTH / 2, PAGE_HEIGHT - 190, primary_value)

    canvas.setStrokeColor(ACCENT)
    canvas.setLineWidth(1)
    canvas.line(PAGE_WIDTH / 2 - 220, PAGE_HEIGHT - 206, PAGE_WIDTH / 2 + 220, PAGE_HEIGHT - 206)

    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 13)
    canvas.drawCentredString(PAGE_WIDTH / 2, PAGE_HEIGHT - 238, "has successfully participated in")

    # --- event -------------------------------------------------------------
    event_lines = simpleSplit(event_name, "Helvetica-Bold", 20, CONTENT_WIDTH)[:2]
    y = PAGE_HEIGHT - 270
    canvas.setFillColor(INK)
    for line in event_lines:
        canvas.setFont("Helvetica-Bold", 20)
        canvas.drawCentredString(PAGE_WIDTH / 2, y, line)
        y -= 26

    # --- dynamic detail fields ---------------------------------------------
    details = _detail_lines(data, fields, primary_key)
    truncated = 0
    if len(details) > 12:
        truncated = len(details) - 12
        details = details[:12]

    detail_y = min(y, DETAIL_TOP) - 6
    font_size = 11
    while font_size > 8 and not _details_fit(detail_y, details, font_size):
        font_size -= 1
    while details and not _details_fit(detail_y, details, font_size):
        details.pop()
        truncated += 1
    if truncated and details and _details_fit(detail_y, details + [("…", "")], font_size):
        details.append(("…", f"{truncated} more field(s) omitted"))

    y = detail_y
    for label, value in details:
        wrapped = simpleSplit(f"{label}: {value}", "Helvetica", font_size, DETAIL_WIDTH)[:2]
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica", font_size)
        for line in wrapped:
            canvas.drawCentredString(PAGE_WIDTH / 2, y, line)
            y -= font_size + 3
        y -= 4

    # --- date ---------------------------------------------------------------
    display_date = event_date or issued_on or datetime.now(timezone.utc).date()
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 13)
    canvas.drawCentredString(PAGE_WIDTH / 2, 155, f"Date: {display_date.isoformat()}")

    # --- verification QR -----------------------------------------------------
    if qr_png:
        from reportlab.lib.utils import ImageReader

        qr_size = 56
        canvas.drawImage(
            ImageReader(io.BytesIO(qr_png)),
            PAGE_WIDTH / 2 - qr_size / 2,
            64,
            width=qr_size,
            height=qr_size,
        )
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica", 7)
        canvas.drawCentredString(PAGE_WIDTH / 2, 126, "Scan to verify")
        if verification_url:
            canvas.setFont("Helvetica", 6)
            canvas.drawCentredString(PAGE_WIDTH / 2, 54, verification_url)

    # --- footer -------------------------------------------------------------
    canvas.setStrokeColor(INK)
    canvas.setLineWidth(1)
    canvas.line(90, 96, 290, 96)
    canvas.line(PAGE_WIDTH - 290, 96, PAGE_WIDTH - 90, 96)

    canvas.setFillColor(INK)
    canvas.setFont("Helvetica", 11)
    canvas.drawCentredString(190, 78, "Authorized Signatory")
    canvas.drawCentredString(PAGE_WIDTH - 190, 78, "Organization Representative")

    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 8)
    canvas.drawString(50, 44, f"Certificate ID: {certificate_code or certificate_id}")
    canvas.drawRightString(PAGE_WIDTH - 50, 44, f"Template: {template_name}")

    canvas.showPage()
    canvas.save()
    return output_path


def _details_height(details: list[tuple[str, str]], font_size: int) -> int:
    height = 0
    for label, value in details:
        lines = simpleSplit(f"{label}: {value}", "Helvetica", font_size, DETAIL_WIDTH)[:2]
        height += len(lines) * (font_size + 3) + 4
    return height


def _details_fit(y: int, details: list[tuple[str, str]], font_size: int) -> bool:
    return y - _details_height(details, font_size) >= DETAIL_BOTTOM
