from __future__ import annotations

from typing import List, Optional, Tuple

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from ..models import Certificate, CertificateJob, Template, User

DEFAULT_LIMIT = 50


def _scope(user: User):
    """Restrict queries to the caller's jobs (admins see everything)."""
    if user.role == "admin":
        return None
    return CertificateJob.created_by == user.id


def _identity():
    """Participant identity: e-mail when known, otherwise the recipient name."""
    return func.coalesce(
        func.nullif(func.lower(Certificate.recipient_email), ""),
        func.concat("name:", Certificate.recipient_name),
    )


def list_jobs(
    session: Session,
    user: User,
    *,
    status: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> Tuple[int, List[CertificateJob]]:
    stmt = select(CertificateJob)
    scope = _scope(user)
    if scope is not None:
        stmt = stmt.where(scope)
    if status:
        stmt = stmt.where(CertificateJob.status == status)
    if search:
        pattern = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(CertificateJob.event_name.like(pattern), CertificateJob.template_name.like(pattern))
        )
    total = int(session.execute(select(func.count()).select_from(stmt.subquery())).scalar_one())
    rows = session.execute(
        stmt.order_by(CertificateJob.created_at.desc()).offset(offset).limit(limit)
    ).scalars()
    return total, list(rows)


def list_certificates(
    session: Session,
    user: User,
    *,
    job_id: Optional[str] = None,
    email: Optional[str] = None,
    status: Optional[str] = None,
    delivery_status: Optional[str] = None,
    revoked: Optional[bool] = None,
    search: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
) -> Tuple[int, List[Certificate]]:
    """Cross-job certificate listing ("previously generated certificates")."""
    stmt = select(Certificate).join(CertificateJob, CertificateJob.id == Certificate.job_id)
    scope = _scope(user)
    if scope is not None:
        stmt = stmt.where(scope)
    if job_id:
        stmt = stmt.where(Certificate.job_id == job_id)
    if email:
        stmt = stmt.where(Certificate.recipient_email == email.strip().lower())
    if status:
        stmt = stmt.where(Certificate.status == status)
    if delivery_status:
        stmt = stmt.where(Certificate.delivery_status == delivery_status)
    if revoked is not None:
        stmt = stmt.where(Certificate.revoked.is_(revoked))
    if search:
        pattern = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                Certificate.recipient_name.like(pattern),
                Certificate.recipient_email.like(pattern),
                Certificate.certificate_code.like(pattern),
            )
        )
    total = int(session.execute(select(func.count()).select_from(stmt.subquery())).scalar_one())
    rows = session.execute(
        stmt.order_by(Certificate.created_at.desc()).offset(offset).limit(limit)
    ).scalars()
    return total, list(rows)


def list_participants(
    session: Session,
    user: User,
    *,
    search: Optional[str] = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> Tuple[int, List[dict]]:
    identity = _identity()
    base = select(Certificate).join(CertificateJob, CertificateJob.id == Certificate.job_id)
    scope = _scope(user)
    if scope is not None:
        base = base.where(scope)
    if search:
        pattern = f"%{search.strip()}%"
        base = base.where(
            or_(Certificate.recipient_name.like(pattern), Certificate.recipient_email.like(pattern))
        )

    grouped = (
        base.with_only_columns(
            identity.label("pid"),
            func.min(Certificate.recipient_name).label("name"),
            func.max(Certificate.recipient_email).label("email"),
            func.count().label("certificate_count"),
            func.sum(case((Certificate.revoked.is_(True), 1), else_=0)).label("revoked_count"),
            func.max(Certificate.created_at).label("last_issued_at"),
        )
        .group_by(identity)
        .subquery()
    )
    total = int(session.execute(select(func.count()).select_from(grouped)).scalar_one())
    rows = session.execute(
        select(grouped)
        .order_by(grouped.c.last_issued_at.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    items = [
        {
            "id": row.pid,
            "name": row.name,
            "email": row.email,
            "certificate_count": int(row.certificate_count),
            "revoked_count": int(row.revoked_count or 0),
            "last_issued_at": row.last_issued_at,
        }
        for row in rows
    ]
    return total, items


def participant_certificates(
    session: Session, user: User, participant_id: str, *, limit: int = 200
) -> Optional[dict]:
    identity = _identity()
    stmt = (
        select(Certificate)
        .join(CertificateJob, CertificateJob.id == Certificate.job_id)
        .where(identity == participant_id)
    )
    scope = _scope(user)
    if scope is not None:
        stmt = stmt.where(scope)
    rows = list(session.execute(stmt.order_by(Certificate.created_at.desc()).limit(limit)).scalars())
    if not rows:
        return None
    return {
        "id": participant_id,
        "name": rows[0].recipient_name,
        "email": rows[0].recipient_email,
        "certificate_count": len(rows),
        "revoked_count": sum(1 for r in rows if r.revoked),
        "last_issued_at": rows[0].created_at,
        "certificates": rows,
    }


def dashboard_stats(session: Session, user: User) -> dict:
    scope = _scope(user)

    job_stmt = select(CertificateJob.status, func.count())
    cert_stmt = select(Certificate.status, func.count()).join(
        CertificateJob, CertificateJob.id == Certificate.job_id
    )
    delivery_stmt = select(Certificate.delivery_status, func.count()).join(
        CertificateJob, CertificateJob.id == Certificate.job_id
    )
    revoked_stmt = (
        select(func.count())
        .select_from(Certificate)
        .join(CertificateJob, CertificateJob.id == Certificate.job_id)
        .where(Certificate.revoked.is_(True))
    )
    participant_stmt = select(func.count(func.distinct(_identity()))).select_from(Certificate).join(
        CertificateJob, CertificateJob.id == Certificate.job_id
    )
    if scope is not None:
        job_stmt = job_stmt.where(scope)
        cert_stmt = cert_stmt.where(scope)
        delivery_stmt = delivery_stmt.where(scope)
        revoked_stmt = revoked_stmt.where(scope)
        participant_stmt = participant_stmt.where(scope)

    jobs_by_status = {row[0]: row[1] for row in session.execute(job_stmt.group_by(CertificateJob.status))}
    certs_by_status = {row[0]: row[1] for row in session.execute(cert_stmt.group_by(Certificate.status))}
    delivery_by_status = {
        row[0]: row[1] for row in session.execute(delivery_stmt.group_by(Certificate.delivery_status))
    }
    jobs_total = sum(jobs_by_status.values())
    certs_total = sum(certs_by_status.values())

    return {
        "jobs": {
            "total": jobs_total,
            "queued": jobs_by_status.get("queued", 0),
            "processing": jobs_by_status.get("processing", 0),
            "completed": jobs_by_status.get("completed", 0),
            "failed": jobs_by_status.get("failed", 0),
        },
        "certificates": {
            "total": certs_total,
            "pending": certs_by_status.get("pending", 0),
            "succeeded": certs_by_status.get("succeeded", 0),
            "failed": certs_by_status.get("failed", 0),
            "revoked": int(session.execute(revoked_stmt).scalar_one()),
        },
        "deliveries": {
            "sent": delivery_by_status.get("sent", 0),
            "failed": delivery_by_status.get("failed", 0),
            "pending": delivery_by_status.get("pending", 0),
            "skipped": delivery_by_status.get("skipped", 0),
        },
        "participants": int(session.execute(participant_stmt).scalar_one()),
        "templates": int(session.execute(select(func.count()).select_from(Template)).scalar_one()),
    }
