from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import Certificate, User
from ..schemas import (
    CERTIFICATE_STATUSES,
    DELIVERY_STATUSES,
    EXPORT_FORMATS,
    CertificateJobCreate,
    CertificateJobSpec,
    CertificateListOut,
    CertificateOut,
    CertificatePatch,
    JobOut,
)
from ..services import exports as export_service
from ..services import jobs as job_service
from ..services.csv_import import CsvImportError, build_fields_and_rows, parse_recipients_csv
from ..storage import certificate_abs_path, safe_filename
from .common import certificate_out, get_db
from .deps import ensure_job_access, require_user

router = APIRouter(dependencies=[Depends(require_user)])


def _map_creation_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (job_service.TooManyRecipientsError, job_service.UnknownPlaceholderError, CsvImportError)):
        return HTTPException(status_code=422, detail=[{"loc": ["body"], "msg": str(exc), "type": "value_error"}])
    if isinstance(exc, job_service.TemplateNotFoundError):
        return HTTPException(
            status_code=422,
            detail=[{"loc": ["body", "template_id"], "msg": str(exc), "type": "value_error"}],
        )
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail=[{"loc": ["body"], "msg": str(exc), "type": "value_error"}])
    raise exc


@router.post(
    "/certificate-jobs",
    response_model=JobOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create a bulk certificate generation + delivery job (JSON)",
)
def create_certificate_job(
    payload: CertificateJobCreate,
    request: Request,
    response: Response,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> JobOut:
    settings: Settings = request.app.state.settings
    try:
        job = job_service.create_job(db, settings, payload, payload.recipients, created_by=user.id)
    except (ValueError, LookupError) as exc:
        raise _map_creation_error(exc)
    request.app.state.runner.submit(job.id)
    response.headers["Location"] = f"{settings.api_prefix}/certificate-jobs/{job.id}"
    return job_service.to_job_out(db, job)


@router.post(
    "/certificate-jobs/csv",
    response_model=JobOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create a job from a CSV file (headers become fields, optional column mapping)",
)
async def create_certificate_job_from_csv(
    request: Request,
    response: Response,
    file: UploadFile = File(..., description="CSV file with a header row and one row per recipient"),
    payload: str = Form(..., description="JSON-encoded CertificateJobSpec (job settings without recipients)"),
    column_map: Optional[str] = Form(
        default=None,
        description='JSON object mapping CSV header -> field key, e.g. {"Full Name": "name"}',
    ),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> JobOut:
    settings: Settings = request.app.state.settings

    try:
        spec_data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=422,
            detail=[{"loc": ["body", "payload"], "msg": f"invalid JSON: {exc}", "type": "value_error"}],
        )
    try:
        spec = CertificateJobSpec.model_validate(spec_data)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors())

    mapping = None
    if column_map:
        try:
            mapping = json.loads(column_map)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=422,
                detail=[{"loc": ["body", "column_map"], "msg": f"invalid JSON: {exc}", "type": "value_error"}],
            )
        if not isinstance(mapping, dict):
            raise HTTPException(
                status_code=422,
                detail=[{"loc": ["body", "column_map"], "msg": "must be a JSON object", "type": "value_error"}],
            )

    content = await file.read()
    try:
        headers, rows = parse_recipients_csv(content, max_rows=settings.max_recipients_per_job)
        fields, recipient_rows = build_fields_and_rows(
            headers,
            rows,
            fields=[f.model_dump() for f in spec.fields] if spec.fields else None,
            column_map=mapping,
        )
        if fields and not spec.fields:
            spec = CertificateJobSpec.model_validate({**spec_data, "fields": fields})
        job = job_service.create_job(db, settings, spec, recipient_rows, created_by=user.id)
    except (CsvImportError, ValueError, LookupError) as exc:
        raise _map_creation_error(exc)

    request.app.state.runner.submit(job.id)
    response.headers["Location"] = f"{settings.api_prefix}/certificate-jobs/{job.id}"
    return job_service.to_job_out(db, job)


@router.get(
    "/certificate-jobs/{job_id}",
    response_model=JobOut,
    summary="Get job status, progress and result counts",
)
def get_certificate_job(
    job_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> JobOut:
    job = job_service.get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    ensure_job_access(user, job)
    return job_service.to_job_out(db, job)


@router.get(
    "/certificate-jobs/{job_id}/certificates",
    response_model=CertificateListOut,
    summary="List the certificates of a job (filter by generation/delivery status)",
)
def list_job_certificates(
    job_id: str,
    request: Request,
    status_filter: Optional[str] = Query(
        default=None, alias="status", pattern="^(" + "|".join(CERTIFICATE_STATUSES) + ")$"
    ),
    delivery_status: Optional[str] = Query(
        default=None, pattern="^(" + "|".join(DELIVERY_STATUSES) + ")$"
    ),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> CertificateListOut:
    job = job_service.get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    ensure_job_access(user, job)
    certificates = job_service.list_certificates(
        db, job_id, status=status_filter, delivery_status=delivery_status, limit=limit, offset=offset
    )
    total = job_service.count_certificates(db, job_id, status=status_filter, delivery_status=delivery_status)
    settings: Settings = request.app.state.settings
    return CertificateListOut(
        job_id=job_id,
        total=total,
        items=[certificate_out(c, settings.api_prefix, settings.public_base_url) for c in certificates],
    )


@router.post(
    "/certificate-jobs/{job_id}/redeliver",
    response_model=JobOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Retry e-mail delivery for certificates whose delivery failed",
)
def redeliver_certificates(
    job_id: str,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> JobOut:
    job = job_service.get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    ensure_job_access(user, job)
    if not job.send_email:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="job was created without e-mail delivery")
    if job.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="job must be completed before retrying delivery"
        )
    failed = job_service.count_certificates(db, job_id, delivery_status="failed")
    if failed == 0:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="no failed deliveries to retry")

    job_service.requeue_failed_deliveries(db, job_id)
    job.status = "queued"
    job.finished_at = None
    job.error = None
    db.commit()
    request.app.state.runner.submit(job_id, mode="delivery")
    return job_service.to_job_out(db, job)


@router.patch(
    "/certificate-jobs/{job_id}/certificates/{certificate_id}",
    response_model=CertificateOut,
    summary="Fix recipient data (e.g. a typo), regenerate the PDF and optionally resend",
)
def patch_certificate(
    job_id: str,
    certificate_id: str,
    payload: CertificatePatch,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> CertificateOut:
    certificate = db.get(Certificate, certificate_id)
    if certificate is None or certificate.job_id != job_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="certificate not found")
    ensure_job_access(user, certificate.job)
    if payload.data is None and not payload.resend:
        raise HTTPException(status_code=422, detail="provide data updates and/or resend=true")
    job = certificate.job
    if payload.resend and not job.send_email:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="job was created without e-mail delivery"
        )
    try:
        certificate = request.app.state.runner.reissue_certificate(
            certificate_id, updates=payload.data, resend=payload.resend
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    db.expire_all()
    certificate = db.get(Certificate, certificate_id) or certificate
    settings: Settings = request.app.state.settings
    return certificate_out(certificate, settings.api_prefix, settings.public_base_url)


_MEDIA_TYPES = {
    "zip": ("application/zip", "certificates-{job_id}.zip"),
    "pdf": ("application/pdf", "certificates-{job_id}.pdf"),
    "png": ("application/zip", "certificates-{job_id}-images.zip"),
}


@router.get(
    "/certificate-jobs/{job_id}/download",
    summary="Download all generated certificates as ZIP, one merged PDF, or a ZIP of PNG images",
)
def download_job_archive(
    job_id: str,
    request: Request,
    format: str = Query(default="zip", pattern="^(" + "|".join(EXPORT_FORMATS) + ")$"),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    settings: Settings = request.app.state.settings
    job = job_service.get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    ensure_job_access(user, job)

    certificates = job_service.list_certificates(db, job_id, status="succeeded", limit=100000)
    files = export_service.collect_pdf_paths(settings, job, certificates)
    if not files:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="no generated certificates available for this job yet",
        )

    media_type, filename_template = _MEDIA_TYPES[format]
    data = export_service.export_bytes(settings, job, certificates, format)
    return StreamingResponse(
        iter([data]),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename_template.format(job_id=job_id)}"'},
    )


@router.get(
    "/certificate-jobs/{job_id}/results.csv",
    summary="Download a CSV report (codes, statuses, delivery, verification links)",
)
def download_results_csv(
    job_id: str,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    settings: Settings = request.app.state.settings
    job = job_service.get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    ensure_job_access(user, job)
    certificates = job_service.list_certificates(db, job_id, limit=100000)
    if not certificates:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="job has no certificates")
    csv_text = export_service.build_results_csv(job, certificates, base_url=settings.public_base_url)
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="results-{job_id}.csv"'},
    )


@router.get(
    "/certificate-jobs/{job_id}/certificates/{certificate_id}/download",
    summary="Download a single generated certificate as PDF",
)
def download_certificate(
    job_id: str,
    certificate_id: str,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    settings: Settings = request.app.state.settings
    certificate = db.get(Certificate, certificate_id)
    if certificate is None or certificate.job_id != job_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="certificate not found")
    ensure_job_access(user, certificate.job)
    if certificate.status != "succeeded" or not certificate.file_path:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"certificate is not available (status: {certificate.status})",
        )

    abs_path = certificate_abs_path(settings.storage_dir, certificate.file_path)
    if not abs_path.is_file():
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="certificate file is missing on disk")

    job = job_service.get_job(db, job_id)
    filename = safe_filename(job.event_name if job else "certificate", certificate.recipient_name)
    return Response(
        content=abs_path.read_bytes(),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
