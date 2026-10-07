from __future__ import annotations

"""Renderer registry.

A *renderer* turns (job, certificate) into a PDF file on disk. New rendering
backends can be registered with :func:`register` without touching job
orchestration code.
"""

from pathlib import Path
from typing import Callable, Dict, Optional

from ..config import Settings
from ..models import Certificate, CertificateJob, Template
from . import generation, template_render
from .placeholders import build_context
from .qr import qr_png, verification_url

Renderer = Callable[..., Path]

_REGISTRY: Dict[str, Renderer] = {}


def register(name: str, renderer: Renderer) -> None:
    _REGISTRY[name] = renderer


def get_renderer(name: str) -> Renderer:
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        raise KeyError(f"unknown renderer {name!r}; registered: {', '.join(sorted(_REGISTRY))}") from exc


def render_certificate(
    settings: Settings,
    job: CertificateJob,
    certificate: Certificate,
    output_path: Path,
    *,
    template: Optional[Template] = None,
) -> Path:
    """Render one certificate using the uploaded template or the built-in one."""
    base_url = settings.public_base_url
    context = build_context(job, certificate, base_url)
    verify_url = verification_url(base_url, certificate.verify_token)
    qr = qr_png(verify_url) if job.qr_enabled else None

    if template is not None:
        renderer = get_renderer("uploaded-template")
        return renderer(
            settings,
            job,
            certificate,
            output_path,
            template=template,
            context=context,
            qr=qr,
            verify_url=verify_url,
        )
    if job.template_id:
        raise ValueError(f"template {job.template_id} not found for job {job.id}")

    renderer = get_renderer("builtin")
    return renderer(
        settings,
        job,
        certificate,
        output_path,
        context=context,
        qr=qr,
        verify_url=verify_url,
    )


def _builtin(
    settings: Settings,
    job: CertificateJob,
    certificate: Certificate,
    output_path: Path,
    *,
    context: dict,
    qr: Optional[bytes],
    verify_url: str,
) -> Path:
    return generation.render_certificate(
        output_path,
        data=dict(certificate.data or {}),
        fields=list(job.fields or []),
        event_name=job.event_name,
        event_date=job.event_date,
        certificate_id=certificate.id,
        template_name=job.template_name,
        certificate_code=certificate.certificate_code,
        qr_png=qr,
        verification_url=verify_url if job.qr_enabled else None,
        issued_on=certificate.created_at.date() if certificate.created_at else None,
    )


def _uploaded_template(
    settings: Settings,
    job: CertificateJob,
    certificate: Certificate,
    output_path: Path,
    *,
    template: Template,
    context: dict,
    qr: Optional[bytes],
    verify_url: str,
) -> Path:
    from ..storage import certificate_abs_path

    template_path = certificate_abs_path(settings.storage_dir, template.file_path)
    font_file = (
        certificate_abs_path(settings.storage_dir, template.font_file_path) if template.font_file_path else None
    )
    images = {"qr": qr} if qr else {}
    return template_render.render_uploaded_template(
        template_path,
        output_path,
        context=context,
        font_family=template.font_family,
        font_size=template.font_size,
        font_color=template.font_color,
        font_file=font_file,
        image_placeholders=images,
    )


register("builtin", _builtin)
register("uploaded-template", _uploaded_template)
