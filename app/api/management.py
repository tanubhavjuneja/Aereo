from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import Certificate, CertificateJob, User
from ..schemas import (
    CERTIFICATE_STATUSES,
    DELIVERY_STATUSES,
    EMAIL_PATTERN,
    JOB_STATUSES,
    CertificateOut,
    CertificateSearchOut,
    DashboardStats,
    JobListOut,
    JobOut,
    ParticipantDetailOut,
    ParticipantListOut,
    ParticipantOut,
)
from ..services import jobs as job_service
from ..services import management as mgmt
from .common import certificate_out, get_db
from .deps import ensure_job_access, require_user

router = APIRouter(dependencies=[Depends(require_user)])


@router.get(
    "/certificate-jobs",
    response_model=JobListOut,
    summary="List certificate jobs (newest first, filterable by status/search)",
)
def list_jobs(
    request: Request,
    user: User = Depends(require_user),
    status_filter: Optional[str] = Query(default=None, alias="status", pattern="^(" + "|".join(JOB_STATUSES) + ")$"),
    search: Optional[str] = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> JobListOut:
    total, jobs = mgmt.list_jobs(
        db, user, status=status_filter, search=search, limit=limit, offset=offset
    )
    return JobListOut(total=total, items=[job_service.to_job_out(db, job) for job in jobs])


@router.get(
    "/certificates",
    response_model=CertificateSearchOut,
    summary="List/search previously generated certificates across jobs",
)
def list_certificates(
    request: Request,
    user: User = Depends(require_user),
    email: Optional[str] = Query(default=None, min_length=3, max_length=320, pattern=EMAIL_PATTERN),
    job_id: Optional[str] = Query(default=None, max_length=32),
    status_filter: Optional[str] = Query(default=None, alias="status", pattern="^(" + "|".join(CERTIFICATE_STATUSES) + ")$"),
    delivery_status: Optional[str] = Query(default=None, pattern="^(" + "|".join(DELIVERY_STATUSES) + ")$"),
    revoked: Optional[bool] = None,
    search: Optional[str] = Query(default=None, max_length=100, description="Name, e-mail or certificate code"),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> CertificateSearchOut:
    settings: Settings = request.app.state.settings
    total, certificates = mgmt.list_certificates(
        db,
        user,
        job_id=job_id,
        email=email,
        status=status_filter,
        delivery_status=delivery_status,
        revoked=revoked,
        search=search,
        limit=limit,
        offset=offset,
    )
    return CertificateSearchOut(
        total=total,
        items=[certificate_out(c, settings.api_prefix, settings.public_base_url) for c in certificates],
    )


@router.get(
    "/certificates/{certificate_id}",
    response_model=CertificateOut,
    summary="Fetch one previously generated certificate by id",
)
def get_certificate(
    certificate_id: str,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> CertificateOut:
    settings: Settings = request.app.state.settings
    certificate = db.get(Certificate, certificate_id)
    if certificate is None:
        raise HTTPException(status_code=404, detail="certificate not found")
    ensure_job_access(user, certificate.job)
    return certificate_out(certificate, settings.api_prefix, settings.public_base_url)


@router.get(
    "/participants",
    response_model=ParticipantListOut,
    summary="List participants (unique people) with their certificate counts",
)
def list_participants(
    user: User = Depends(require_user),
    search: Optional[str] = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> ParticipantListOut:
    total, items = mgmt.list_participants(db, user, search=search, limit=limit, offset=offset)
    return ParticipantListOut(total=total, items=[ParticipantOut(**item) for item in items])


@router.get(
    "/participants/{participant_id}",
    response_model=ParticipantDetailOut,
    summary="Fetch one participant with every certificate issued to them",
)
def get_participant(
    participant_id: str,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> ParticipantDetailOut:
    settings: Settings = request.app.state.settings
    detail = mgmt.participant_certificates(db, user, participant_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="participant not found")
    certificates = detail.pop("certificates")
    return ParticipantDetailOut(
        **detail,
        certificates=[
            certificate_out(c, settings.api_prefix, settings.public_base_url) for c in certificates
        ],
    )


@router.get(
    "/dashboard/stats",
    response_model=DashboardStats,
    summary="Aggregate counters: jobs, certificates, deliveries, participants",
)
def dashboard(user: User = Depends(require_user), db: Session = Depends(get_db)) -> DashboardStats:
    return DashboardStats(**mgmt.dashboard_stats(db, user))
