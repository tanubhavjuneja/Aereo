from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import List, Optional, Sequence
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import Certificate, CertificateJob, Template
from ..schemas import Counts, DEFAULT_FIELDS, JobOut
from .placeholders import uncovered_placeholders
from .template_render import extract_pdf_placeholders
from .validation import email_field, normalize_fields, primary_field, validate_recipient


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TooManyRecipientsError(ValueError):
    def __init__(self, limit: int, actual: int):
        super().__init__(f"a job may contain at most {limit} recipients (got {actual})")
        self.limit = limit
        self.actual = actual


class TemplateNotFoundError(LookupError):
    def __init__(self, template_id: str):
        super().__init__(f"template '{template_id}' not found")
        self.template_id = template_id


class UnknownPlaceholderError(ValueError):
    """Uploaded template uses ``{placeholders}`` the job's fields cannot fill."""

    def __init__(self, unknown: Sequence[str]):
        super().__init__(
            "template uses unknown placeholders: " + ", ".join(f"{{{name}}}" for name in unknown)
        )
        self.unknown = list(unknown)


def _new_token() -> str:
    return secrets.token_urlsafe(9)


def create_job(
    session: Session,
    settings: Settings,
    spec,
    recipients: Optional[Sequence[dict]] = None,
    *,
    created_by: Optional[str] = None,
) -> CertificateJob:
    """Create a job plus one certificate row per recipient.

    Rows failing business validation are stored with status ``failed`` and
    ``delivery_status`` ``skipped`` immediately; valid rows are queued for
    generation (and optional e-mail delivery).

    ``recipients`` defaults to ``spec.recipients`` (JSON endpoint); the CSV
    endpoint passes the rows parsed from the file instead.
    """
    rows = list(recipients if recipients is not None else spec.recipients)
    if len(rows) > settings.max_recipients_per_job:
        raise TooManyRecipientsError(settings.max_recipients_per_job, len(rows))

    template: Optional[Template] = None
    if spec.template_id:
        template = session.get(Template, spec.template_id)
        if template is None:
            raise TemplateNotFoundError(spec.template_id)

    fields = normalize_fields([field.model_dump() for field in spec.fields] if spec.fields else DEFAULT_FIELDS)
    primary_key = primary_field(fields)["key"]
    email_field_def = email_field(fields)

    if template is not None:
        template_path = _template_abs_path(settings, template)
        placeholders = extract_pdf_placeholders(template_path.read_bytes())
        unknown = uncovered_placeholders(placeholders, [f["key"] for f in fields])
        if unknown:
            raise UnknownPlaceholderError(unknown)

    now = utcnow()
    job = CertificateJob(
        id=uuid4().hex,
        event_name=spec.event_name.strip(),
        event_date=spec.event_date,
        template_id=template.id if template else None,
        template_name=template.name if template else settings.template_name,
        fields=fields,
        send_email=spec.send_email,
        email_subject=spec.email_subject,
        email_body=spec.email_body,
        id_prefix=spec.id_prefix or settings.default_id_prefix,
        qr_enabled=spec.qr_enabled,
        created_by=created_by,
        status="queued",
        created_at=now,
    )
    session.add(job)

    for index, row in enumerate(rows):
        cleaned, error = validate_recipient(row, fields, settings.max_recipient_name_length)
        raw_primary = row.get(primary_key) if isinstance(row, dict) else None
        display_name = raw_primary.strip() if isinstance(raw_primary, str) else ""
        if not display_name and isinstance(raw_primary, (int, float)) and not isinstance(raw_primary, bool):
            display_name = str(raw_primary)
        session.add(
            Certificate(
                id=uuid4().hex,
                job_id=job.id,
                sequence=index,
                certificate_code=f"{job.id_prefix}-{index + 1:04d}",
                verify_token=_new_token(),
                recipient_name=display_name or f"recipient-{index + 1}",
                recipient_email=(cleaned or {}).get(email_field_def["key"]) if email_field_def else None,
                data=cleaned or {},
                status="failed" if error else "pending",
                error=error,
                delivery_status="pending" if not error else "skipped",
                created_at=now,
                updated_at=now,
            )
        )

    session.commit()
    session.refresh(job)
    return job


def _template_abs_path(settings: Settings, template: Template):
    from ..storage import certificate_abs_path

    return certificate_abs_path(settings.storage_dir, template.file_path)


def get_job(session: Session, job_id: str) -> Optional[CertificateJob]:
    return session.get(CertificateJob, job_id)


def get_counts(session: Session, job_id: str) -> Counts:
    generation_rows = session.execute(
        select(Certificate.status, func.count())
        .where(Certificate.job_id == job_id)
        .group_by(Certificate.status)
    ).all()
    delivery_rows = session.execute(
        select(Certificate.delivery_status, func.count())
        .where(Certificate.job_id == job_id)
        .group_by(Certificate.delivery_status)
    ).all()

    counts = Counts()
    for status, total in generation_rows:
        counts.total += total
        if status == "pending":
            counts.pending = total
        elif status == "succeeded":
            counts.succeeded = total
        elif status == "failed":
            counts.failed = total
    for status, total in delivery_rows:
        if status == "sent":
            counts.sent = total
        elif status == "failed":
            counts.delivery_failed = total
    counts.revoked = int(
        session.execute(
            select(func.count())
            .select_from(Certificate)
            .where(Certificate.job_id == job_id, Certificate.revoked.is_(True))
        ).scalar_one()
    )
    return counts


def list_certificates(
    session: Session,
    job_id: str,
    status: Optional[str] = None,
    delivery_status: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
) -> List[Certificate]:
    stmt = select(Certificate).where(Certificate.job_id == job_id)
    if status:
        stmt = stmt.where(Certificate.status == status)
    if delivery_status:
        stmt = stmt.where(Certificate.delivery_status == delivery_status)
    stmt = stmt.order_by(Certificate.sequence).offset(offset).limit(limit)
    return list(session.execute(stmt).scalars())


def count_certificates(
    session: Session, job_id: str, status: Optional[str] = None, delivery_status: Optional[str] = None
) -> int:
    stmt = select(func.count()).select_from(Certificate).where(Certificate.job_id == job_id)
    if status:
        stmt = stmt.where(Certificate.status == status)
    if delivery_status:
        stmt = stmt.where(Certificate.delivery_status == delivery_status)
    return int(session.execute(stmt).scalar_one())


def requeue_failed_deliveries(session: Session, job_id: str) -> int:
    """Mark failed deliveries as pending again so the runner can retry them."""
    rows = session.execute(
        select(Certificate).where(Certificate.job_id == job_id, Certificate.delivery_status == "failed")
    ).scalars()
    count = 0
    now = utcnow()
    for certificate in rows:
        certificate.delivery_status = "pending"
        certificate.delivery_error = None
        certificate.updated_at = now
        count += 1
    session.commit()
    return count


def to_job_out(session: Session, job: CertificateJob) -> JobOut:
    return JobOut(
        id=job.id,
        status=job.status,
        event_name=job.event_name,
        event_date=job.event_date,
        template_id=job.template_id,
        template_name=job.template_name,
        send_email=job.send_email,
        id_prefix=job.id_prefix,
        qr_enabled=job.qr_enabled,
        counts=get_counts(session, job.id),
        error=job.error,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )
