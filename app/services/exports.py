from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

import fitz  # PyMuPDF

from ..config import Settings
from ..models import Certificate, CertificateJob
from ..storage import certificate_abs_path

PDF_PAGE_DPI = 144


def collect_pdf_paths(settings: Settings, job: CertificateJob, certificates: Sequence[Certificate]) -> List[Tuple[str, Path]]:
    """Return ``(filename, absolute path)`` for every successfully generated PDF."""
    result: List[Tuple[str, Path]] = []
    for cert in certificates:
        if not cert.file_path:
            continue
        abs_path = certificate_abs_path(settings.storage_dir, cert.file_path)
        if abs_path.is_file():
            result.append((abs_path.name, abs_path))
    return result


def build_zip(files: Iterable[Tuple[str, Path]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, path in files:
            archive.write(path, arcname=name)
    return buffer.getvalue()


def build_combined_pdf(files: Sequence[Tuple[str, Path]]) -> bytes:
    """Merge all generated PDFs into one multi-page PDF."""
    combined = fitz.open()
    for _, path in files:
        with fitz.open(str(path)) as source:
            combined.insert_pdf(source)
    try:
        return combined.tobytes()
    finally:
        combined.close()


def build_png_zip(files: Sequence[Tuple[str, Path]]) -> bytes:
    """Render page 1 of every certificate to PNG and pack them into a ZIP."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, path in files:
            with fitz.open(str(path)) as source:
                pix = source[0].get_pixmap(dpi=PDF_PAGE_DPI)
                archive.writestr(Path(name).stem + ".png", pix.tobytes("png"))
    return buffer.getvalue()


CSV_COLUMNS = (
    "certificate_code",
    "recipient_name",
    "recipient_email",
    "status",
    "error",
    "delivery_status",
    "delivery_error",
    "revoked",
    "revoke_reason",
    "verification_url",
    "created_at",
)


def build_results_csv(
    job: CertificateJob,
    certificates: Sequence[Certificate],
    *,
    base_url: str,
) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_COLUMNS)
    for cert in certificates:
        writer.writerow(
            [
                cert.certificate_code,
                cert.recipient_name,
                cert.recipient_email or "",
                cert.status,
                cert.error or "",
                cert.delivery_status,
                cert.delivery_error or "",
                "yes" if cert.revoked else "no",
                cert.revoke_reason or "",
                f"{base_url.rstrip('/')}/verify/{cert.verify_token}",
                cert.created_at.isoformat() if cert.created_at else "",
            ]
        )
    return buffer.getvalue()


def export_bytes(settings: Settings, job: CertificateJob, certificates: Sequence[Certificate], fmt: str) -> bytes:
    files = collect_pdf_paths(settings, job, certificates)
    if fmt == "zip":
        return build_zip(files)
    if fmt == "pdf":
        return build_combined_pdf(files)
    if fmt == "png":
        return build_png_zip(files)
    raise ValueError(f"unsupported export format: {fmt}")
