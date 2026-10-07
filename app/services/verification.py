from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Certificate, CertificateJob
from ..schemas import VerifyOut


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def find_by_token(session: Session, token: str) -> Optional[Certificate]:
    if not token:
        return None
    return session.scalar(select(Certificate).where(Certificate.verify_token == token))


def set_revoked(certificate: Certificate, *, revoked: bool, reason: Optional[str]) -> None:
    certificate.revoked = revoked
    certificate.revoke_reason = reason if revoked else None
    certificate.revoked_at = utcnow() if revoked else None


def to_verify_out(certificate: Certificate, base_url: str, api_prefix: str) -> VerifyOut:
    job: CertificateJob = certificate.job
    base = base_url.rstrip("/")
    return VerifyOut(
        status="revoked" if certificate.revoked else "issued",
        certificate_code=certificate.certificate_code,
        event_name=job.event_name,
        event_date=job.event_date,
        recipient_name=certificate.recipient_name,
        data=dict(certificate.data or {}),
        template_name=job.template_name,
        issued_at=certificate.created_at,
        revoked_at=certificate.revoked_at,
        revoke_reason=certificate.revoke_reason,
        download_url=f"{api_prefix}/verify/{certificate.verify_token}/download",
    )
