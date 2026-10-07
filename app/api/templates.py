from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import Certificate, CertificateJob, Template
from ..schemas import (
    DEFAULT_FIELDS,
    PreviewIn,
    TemplateListOut,
    TemplateOut,
)
from ..services import jobs as job_service
from ..services import renderers
from ..services import templates as template_service
from ..services.placeholders import uncovered_placeholders
from ..services.template_render import TemplateRenderError
from ..storage import certificate_abs_path, safe_filename
from .common import get_db
from .deps import require_user

router = APIRouter(dependencies=[Depends(require_user)])


def _template_out(template: Template, api_prefix: str) -> TemplateOut:
    return TemplateOut(
        id=template.id,
        name=template.name,
        email_subject=template.email_subject,
        email_body=template.email_body,
        font_family=template.font_family,
        font_size=template.font_size,
        font_color=template.font_color,
        has_custom_font=bool(template.font_file_path),
        placeholders=list(template.placeholders or []),
        download_url=f"{api_prefix}/templates/{template.id}/download",
        created_at=template.created_at,
    )


@router.post(
    "/templates",
    response_model=TemplateOut,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a PDF certificate template (placeholders like {name}, optional font + e-mail defaults)",
)
async def upload_template(
    request: Request,
    response: Response,
    file: UploadFile = File(..., description="PDF file containing {placeholder} markers"),
    name: str = Form(..., min_length=1, max_length=100),
    email_subject: Optional[str] = Form(default=None, max_length=200),
    email_body: Optional[str] = Form(default=None, max_length=5000),
    font_family: str = Form(default="helvetica"),
    font_size: Optional[int] = Form(default=None, ge=6, le=400),
    font_color: Optional[str] = Form(default=None, pattern=r"^#[0-9A-Fa-f]{6}$"),
    font: Optional[UploadFile] = File(default=None, description="Optional TTF/OTF used for replacement text"),
    db: Session = Depends(get_db),
) -> TemplateOut:
    settings: Settings = request.app.state.settings
    content = await file.read()
    font_content = await font.read() if font is not None else None
    try:
        template = template_service.create_template(
            db,
            settings,
            name=name,
            content=content,
            email_subject=email_subject,
            email_body=email_body,
            font_family=font_family,
            font_size=font_size,
            font_color=font_color,
            font=font_content,
        )
    except template_service.TemplateValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    response.headers["Location"] = f"{settings.api_prefix}/templates/{template.id}"
    return _template_out(template, settings.api_prefix)


@router.get("/templates", response_model=TemplateListOut, summary="List uploaded templates")
def list_templates(request: Request, db: Session = Depends(get_db)) -> TemplateListOut:
    settings: Settings = request.app.state.settings
    templates = list(db.execute(select(Template).order_by(Template.created_at.desc())).scalars())
    return TemplateListOut(
        total=len(templates),
        items=[_template_out(t, settings.api_prefix) for t in templates],
    )


@router.get("/templates/{template_id}", response_model=TemplateOut, summary="Get one template")
def get_template(template_id: str, request: Request, db: Session = Depends(get_db)) -> TemplateOut:
    settings: Settings = request.app.state.settings
    template = db.get(Template, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="template not found")
    return _template_out(template, settings.api_prefix)


@router.get("/templates/{template_id}/download", summary="Download the uploaded template PDF")
def download_template(template_id: str, request: Request, db: Session = Depends(get_db)) -> Response:
    settings: Settings = request.app.state.settings
    template = db.get(Template, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="template not found")
    abs_path = certificate_abs_path(settings.storage_dir, template.file_path)
    if not abs_path.is_file():
        raise HTTPException(status_code=410, detail="template file is missing on disk")
    filename = safe_filename(template.name, suffix=".pdf")
    return Response(
        content=abs_path.read_bytes(),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _preview_job(
    settings: Settings, spec: PreviewIn, template: Optional[Template], field_dicts: List[dict]
) -> CertificateJob:
    return CertificateJob(
        id="preview",
        event_name=spec.event_name,
        event_date=spec.event_date,
        template_id=template.id if template else None,
        template_name=template.name if template else settings.template_name,
        fields=field_dicts,
        send_email=False,
        id_prefix="PREVIEW",
        qr_enabled=True,
        status="completed",
        created_at=_utcnow(),
    )


@router.post(
    "/previews",
    summary="Render a preview certificate PDF (no job, no persistence) for given sample data",
)
def render_preview(payload: PreviewIn, request: Request, db: Session = Depends(get_db)) -> Response:
    settings: Settings = request.app.state.settings

    template: Optional[Template] = None
    if payload.template_id:
        template = db.get(Template, payload.template_id)
        if template is None:
            raise HTTPException(status_code=422, detail=f"template '{payload.template_id}' not found")

    field_dicts: List[dict[str, Any]]
    if payload.data:
        field_dicts = [
            {"key": key, "label": key.replace("_", " ").title(), "required": False, "role": "text", "visible": True}
            for key in payload.data.keys()
        ]
        field_dicts[0]["role"] = "primary"
        if template is not None:
            unknown = uncovered_placeholders(list(template.placeholders or []), [f["key"] for f in field_dicts])
            if unknown:
                raise HTTPException(
                    status_code=422,
                    detail="template uses unknown placeholders: "
                    + ", ".join(f"{{{name}}}" for name in unknown),
                )
    else:
        field_dicts = [dict(f) for f in DEFAULT_FIELDS]

    job = _preview_job(settings, payload, template, field_dicts)
    data = {key: str(value) for key, value in (payload.data or {}).items()}
    primary: dict[str, Any] = next((f for f in field_dicts if f.get("role") == "primary"), field_dicts[0])
    primary_value = str(data.get(str(primary["key"]), "")).strip() or "Preview Recipient"
    certificate = Certificate(
        id=uuid4().hex,
        job_id="preview",
        sequence=0,
        certificate_code="PREVIEW-0001",
        verify_token="preview",
        recipient_name=primary_value,
        recipient_email=None,
        data=data,
        status="succeeded",
        delivery_status="skipped",
        revoked=False,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )

    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    tmp.close()
    tmp_path = Path(tmp.name)
    try:
        renderers.render_certificate(settings, job, certificate, tmp_path, template=template)
        content = tmp_path.read_bytes()
    except TemplateRenderError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    finally:
        tmp_path.unlink(missing_ok=True)

    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="preview.pdf"'},
    )
