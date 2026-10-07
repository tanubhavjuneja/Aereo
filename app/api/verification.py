from __future__ import annotations

import html
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import Certificate
from ..services.verification import find_by_token, set_revoked, to_verify_out
from ..storage import certificate_abs_path, safe_filename
from .common import get_db

api_router = APIRouter()
page_router = APIRouter()


class RevokeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revoked: bool = Field(default=True, description="True to revoke, false to restore the certificate.")
    reason: Optional[str] = Field(default=None, max_length=300, description="Reason shown on the verification page.")


def _require_certificate(db: Session, token: str) -> Certificate:
    certificate = find_by_token(db, token)
    if certificate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="certificate not found")
    return certificate


@api_router.get(
    "/verify/{token}",
    response_model=None,
    summary="Public verification: issued/revoked status and certificate details (JSON)",
)
def verify_certificate(token: str, request: Request, db: Session = Depends(get_db)):
    certificate = _require_certificate(db, token)
    settings: Settings = request.app.state.settings
    return to_verify_out(certificate, settings.public_base_url, settings.api_prefix)


@api_router.patch(
    "/verify/{token}",
    summary="Revoke or restore a certificate using its secret verification token",
)
def revoke_certificate(token: str, payload: RevokeIn, db: Session = Depends(get_db)):
    certificate = _require_certificate(db, token)
    set_revoked(certificate, revoked=payload.revoked, reason=payload.reason)
    db.commit()
    return {"id": certificate.id, "revoked": certificate.revoked, "reason": certificate.revoke_reason}


@api_router.get(
    "/verify/{token}/download",
    summary="Download the certificate PDF through its verification link",
)
def download_via_verification(token: str, request: Request, db: Session = Depends(get_db)):
    certificate = _require_certificate(db, token)
    settings: Settings = request.app.state.settings
    if certificate.revoked:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="certificate has been revoked")
    if certificate.status != "succeeded" or not certificate.file_path:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"certificate is not available (status: {certificate.status})",
        )
    abs_path = certificate_abs_path(settings.storage_dir, certificate.file_path)
    if not abs_path.is_file():
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="certificate file is missing on disk")

    job = certificate.job
    filename = safe_filename(job.event_name if job else "certificate", certificate.recipient_name)
    return Response(
        content=abs_path.read_bytes(),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
 body {{ font-family: system-ui, sans-serif; margin: 0; background: #f4f5f7; color: #1f2a44; }}
 .card {{ max-width: 640px; margin: 48px auto; background: #fff; border-radius: 12px;
          padding: 32px; box-shadow: 0 4px 16px rgba(0,0,0,.08); }}
 h1 {{ font-size: 22px; margin-top: 0; }}
 .status {{ display: inline-block; padding: 4px 12px; border-radius: 999px; font-size: 13px; font-weight: 600; }}
 .issued {{ background: #e5f6ec; color: #157347; }}
 .revoked {{ background: #fdebec; color: #b02a37; }}
 dl {{ display: grid; grid-template-columns: 180px 1fr; gap: 8px 16px; margin: 24px 0; }}
 dt {{ color: #5a6478; }}
 dd {{ margin: 0; font-weight: 500; }}
 a.download {{ display: inline-block; background: #1f2a44; color: #fff; text-decoration: none;
               padding: 10px 20px; border-radius: 8px; }}
 footer {{ max-width: 640px; margin: 16px auto; color: #5a6478; font-size: 12px; text-align: center; }}
</style>
</head>
<body>
<div class="card">
  <h1>Certificate verification</h1>
  <p><span class="status {status_class}">{status_label}</span></p>
  <dl>
    <dt>Certificate code</dt><dd>{code}</dd>
    <dt>Recipient</dt><dd>{recipient}</dd>
    <dt>Course / event</dt><dd>{event}</dd>
    <dt>Event date</dt><dd>{event_date}</dd>
    <dt>Issued on</dt><dd>{issued}</dd>
    {extra_rows}
  </dl>
  {download}
</div>
<footer>Issued with Bulk Certificate Generator</footer>
</body>
</html>
"""


@page_router.get(
    "/verify/{token}",
    summary="Public verification / claim page (HTML)",
)
def verification_page(token: str, request: Request, db: Session = Depends(get_db)) -> Response:
    certificate = _require_certificate(db, token)
    settings: Settings = request.app.state.settings
    job = certificate.job
    revoked = certificate.revoked

    extra_rows = ""
    if revoked:
        extra_rows += f"<dt>Revoked at</dt><dd>{html.escape(str(certificate.revoked_at or ''))}</dd>"
        if certificate.revoke_reason:
            extra_rows += f"<dt>Reason</dt><dd>{html.escape(certificate.revoke_reason)}</dd>"

    if certificate.status == "succeeded" and certificate.file_path and not revoked:
        download = f'<a class="download" href="{settings.api_prefix}/verify/{token}/download">Download certificate (PDF)</a>'
    else:
        download = ""

    page = _PAGE.format(
        title=html.escape(f"Certificate {certificate.certificate_code}"),
        status_class="revoked" if revoked else "issued",
        status_label="REVOKED" if revoked else "VERIFIED · ISSUED",
        code=html.escape(certificate.certificate_code),
        recipient=html.escape(certificate.recipient_name),
        event=html.escape(job.event_name if job else ""),
        event_date=html.escape(str(job.event_date) if job and job.event_date else "—"),
        issued=html.escape(str(certificate.created_at)),
        extra_rows=extra_rows,
        download=download,
    )
    return Response(content=page, media_type="text/html; charset=utf-8")
