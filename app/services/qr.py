from __future__ import annotations

import io

import qrcode


def verification_url(base_url: str, token: str) -> str:
    """Public URL of the verification/claim page for one certificate."""
    return f"{base_url.rstrip('/')}/verify/{token}"


def qr_png(url: str, box_size: int = 6, border: int = 2) -> bytes:
    """Render ``url`` as a PNG QR code (used both in PDFs and e-mails)."""
    qr = qrcode.QRCode(border=border, box_size=box_size, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(url)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
