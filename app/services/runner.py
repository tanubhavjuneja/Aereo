from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from .. import storage
from ..config import Settings
from ..models import Certificate, CertificateJob
from . import renderers
from .email import (
    DEFAULT_BODY,
    DEFAULT_SUBJECT,
    build_context,
    build_email_backend,
    build_message,
    render_placeholders,
)
from .jobs import DEFAULT_FIELDS, get_counts, utcnow
from .validation import email_field, normalize_fields, primary_field, validate_recipient

logger = logging.getLogger(__name__)

_UNSET = object()


class JobRunner:
    """Bounded thread pool that processes certificate jobs in the background.

    For each job the runner walks the job's certificates in order and, per
    certificate: renders the PDF, then (if the job requested it) delivers the
    PDF to the recipient's e-mail address. Every step is committed separately
    so clients observe incremental progress, and any per-certificate failure
    (rendering or delivery) is recorded without stopping the rest of the job.
    """

    def __init__(
        self,
        session_factory: sessionmaker,
        settings: Settings,
        email_backend=_UNSET,  # EmailBackend | None | _UNSET
        concurrency: Optional[int] = None,
    ):
        self._session_factory = session_factory
        self._settings = settings
        self._email_backend = build_email_backend(settings) if email_backend is _UNSET else email_backend
        self._executor = ThreadPoolExecutor(
            max_workers=concurrency or settings.worker_concurrency,
            thread_name_prefix="cert-job",
        )

    @property
    def email_backend(self):
        return self._email_backend

    def submit(self, job_id: str, mode: str = "full") -> None:
        """Queue a job. ``mode`` is ``full`` (render + deliver) or ``delivery``."""
        self._executor.submit(self._process_job, job_id, mode)

    def shutdown(self, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait)

    # ------------------------------------------------------------------
    def _process_job(self, job_id: str, mode: str = "full") -> None:
        session = self._session_factory()
        try:
            job = session.get(CertificateJob, job_id)
            if job is None:  # pragma: no cover - job deleted before pickup
                logger.warning("job %s disappeared before processing", job_id)
                return

            job.status = "processing"
            if job.started_at is None:
                job.started_at = utcnow()
            session.commit()

            if mode == "delivery":
                pending = list(
                    session.execute(
                        select(Certificate)
                        .where(Certificate.job_id == job_id, Certificate.delivery_status == "pending")
                        .order_by(Certificate.sequence)
                    ).scalars()
                )
            else:
                pending = list(
                    session.execute(
                        select(Certificate)
                        .where(Certificate.job_id == job_id, Certificate.status == "pending")
                        .order_by(Certificate.sequence)
                    ).scalars()
                )

            for certificate in pending:
                if mode == "delivery":
                    self._deliver(certificate, job)
                else:
                    self._process_certificate(certificate, job)
                session.commit()

            counts = get_counts(session, job_id)
            job.status = "completed"
            job.finished_at = utcnow()
            session.commit()
            logger.info(
                "job %s finished (%s mode): %s succeeded, %s failed, %s sent, %s delivery failed",
                job_id,
                mode,
                counts.succeeded,
                counts.failed,
                counts.sent,
                counts.delivery_failed,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("job %s failed", job_id)
            session.rollback()
            job = session.get(CertificateJob, job_id)
            if job is not None:
                job.status = "failed"
                job.error = f"{type(exc).__name__}: {exc}"[:500]
                job.finished_at = utcnow()
                session.commit()
        finally:
            session.close()

    def _process_certificate(self, certificate: Certificate, job: CertificateJob) -> None:
        rel_path = storage.certificate_rel_path(certificate.job_id, certificate.id)
        abs_path = storage.certificate_abs_path(self._settings.storage_dir, rel_path)
        try:
            renderers.render_certificate(
                self._settings,
                job,
                certificate,
                abs_path,
                template=job.template if job.template_id else None,
            )
        except Exception as exc:
            certificate.status = "failed"
            certificate.error = f"{type(exc).__name__}: {exc}"[:500]
            certificate.file_path = None
            certificate.delivery_status = "skipped"
            certificate.delivery_error = "certificate generation failed"
            logger.warning("certificate %s failed: %s", certificate.id, certificate.error)
        else:
            certificate.status = "succeeded"
            certificate.error = None
            certificate.file_path = rel_path
            self._deliver(certificate, job)
        certificate.updated_at = utcnow()

    # ------------------------------------------------------------------
    def reissue_certificate(
        self,
        certificate_id: str,
        updates: Optional[dict] = None,
        resend: bool = False,
    ) -> Certificate:
        """Fix recipient data (e.g. a typo), regenerate the PDF and optionally resend.

        Raises :class:`LookupError` when the certificate does not exist and
        :class:`ValueError` for validation problems (mapped to HTTP 404/422).
        """
        session = self._session_factory()
        try:
            certificate = session.get(Certificate, certificate_id)
            if certificate is None:
                raise LookupError(f"certificate '{certificate_id}' not found")
            job = certificate.job
            if job.status in ("queued", "processing"):
                raise ValueError("job is still being processed; wait for it to finish")

            merged = dict(certificate.data or {})
            for key, value in (updates or {}).items():
                merged[key] = value

            fields = normalize_fields(job.fields or DEFAULT_FIELDS)
            cleaned, error = validate_recipient(merged, fields, self._settings.max_recipient_name_length)
            if error:
                raise ValueError(error)
            cleaned = cleaned or {}

            primary_key = primary_field(fields)["key"]
            email_def = email_field(fields)
            certificate.data = cleaned
            display = cleaned.get(primary_key)
            if display is not None and str(display).strip():
                certificate.recipient_name = str(display).strip()
            certificate.recipient_email = cleaned.get(email_def["key"]) if email_def else None

            rel_path = storage.certificate_rel_path(certificate.job_id, certificate.id)
            abs_path = storage.certificate_abs_path(self._settings.storage_dir, rel_path)
            try:
                renderers.render_certificate(
                    self._settings,
                    job,
                    certificate,
                    abs_path,
                    template=job.template if job.template_id else None,
                )
            except Exception as exc:
                certificate.status = "failed"
                certificate.error = f"{type(exc).__name__}: {exc}"[:500]
                certificate.file_path = None
                certificate.delivery_status = "skipped"
                certificate.delivery_error = "certificate generation failed"
            else:
                certificate.status = "succeeded"
                certificate.error = None
                certificate.file_path = rel_path
                if resend:
                    certificate.delivery_status = "pending"
                    certificate.delivery_error = None
                    self._deliver(certificate, job)

            certificate.updated_at = utcnow()
            session.commit()
            session.refresh(certificate)
            return certificate
        except (LookupError, ValueError):
            session.rollback()
            raise
        except Exception as exc:  # pragma: no cover - defensive
            session.rollback()
            raise ValueError(f"{type(exc).__name__}: {exc}") from exc
        finally:
            session.close()

    def _deliver(self, certificate: Certificate, job: CertificateJob) -> None:
        """Send the generated PDF to the recipient; never raises."""
        if not job.send_email:
            self._skip(certificate, "email delivery not requested")
            return
        if self._email_backend is None:
            self._skip(certificate, "email delivery disabled (CERT_EMAIL_BACKEND=none)")
            return
        if not certificate.recipient_email:
            self._skip(certificate, "no e-mail address for recipient")
            return
        if not certificate.file_path:
            certificate.delivery_status = "failed"
            certificate.delivery_error = "certificate file is not available"
            return

        try:
            abs_path = storage.certificate_abs_path(self._settings.storage_dir, certificate.file_path)
            if not abs_path.is_file():
                raise FileNotFoundError(f"certificate file missing: {certificate.file_path}")
            context = build_context(job, certificate, self._settings.public_base_url)
            subject = render_placeholders(job.email_subject or DEFAULT_SUBJECT, context)
            body = render_placeholders(job.email_body or DEFAULT_BODY, context)
            message = build_message(
                to=certificate.recipient_email,
                sender=self._settings.email_from,
                subject=subject,
                body=body,
                attachments=[(storage.safe_filename(certificate.recipient_name, certificate.id), abs_path.read_bytes())],
            )
            self._email_backend.send(message, ref=f"{job.id}/{certificate.id}")
        except Exception as exc:
            certificate.delivery_status = "failed"
            certificate.delivery_error = f"{type(exc).__name__}: {exc}"[:500]
            logger.warning("delivery for certificate %s failed: %s", certificate.id, certificate.delivery_error)
        else:
            certificate.delivery_status = "sent"
            certificate.delivery_error = None

    @staticmethod
    def _skip(certificate: Certificate, reason: str) -> None:
        certificate.delivery_status = "skipped"
        certificate.delivery_error = reason
