from __future__ import annotations

from typing import Iterator

from fastapi import Depends
from sqlalchemy.orm import Session

from ..database import session_scope
from ..models import Certificate
from ..schemas import CertificateOut


def get_db() -> Iterator[Session]:
    with session_scope() as session:
        yield session


def certificate_out(certificate: Certificate, api_prefix: str, base_url: str = "") -> CertificateOut:
    download_url = (
        f"{api_prefix}/certificate-jobs/{certificate.job_id}"
        f"/certificates/{certificate.id}/download"
        if certificate.status == "succeeded" and certificate.file_path
        else None
    )
    verify_url = f"{base_url.rstrip('/')}/verify/{certificate.verify_token}" if base_url else None
    return CertificateOut(
        id=certificate.id,
        job_id=certificate.job_id,
        event_name=certificate.job.event_name if certificate.job else None,
        certificate_code=certificate.certificate_code,
        verification_url=verify_url,
        recipient_name=certificate.recipient_name,
        recipient_email=certificate.recipient_email,
        data=dict(certificate.data or {}),
        status=certificate.status,
        error=certificate.error,
        delivery_status=certificate.delivery_status,
        delivery_error=certificate.delivery_error,
        revoked=certificate.revoked,
        revoke_reason=certificate.revoke_reason,
        download_url=download_url,
        created_at=certificate.created_at,
        updated_at=certificate.updated_at,
    )
