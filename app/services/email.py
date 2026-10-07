from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import Optional, Protocol

from ..config import Settings
from .placeholders import PLACEHOLDER_RE, build_context as _build_context, render_placeholders

logger = logging.getLogger(__name__)

DEFAULT_SUBJECT = "Your certificate for {event_name}"
DEFAULT_BODY = """Dear {recipient},

Please find attached your certificate for "{event_name}".

Certificate ID: {certificate_code}

Verify online: {verification_url}

Best regards,
Certificates Team
"""


class EmailBackend(Protocol):
    """Transport used to deliver a certificate e-mail."""

    def send(self, message: EmailMessage, *, ref: str) -> None: ...


def build_context(job, certificate, base_url: str = "") -> dict[str, str]:
    """E-mail flavour of the shared placeholder context."""
    return _build_context(job, certificate, base_url)


__all__ = [
    "PLACEHOLDER_RE",
    "DEFAULT_SUBJECT",
    "DEFAULT_BODY",
    "EmailBackend",
    "OutboxBackend",
    "SmtpBackend",
    "build_context",
    "build_email_backend",
    "build_message",
    "render_placeholders",
]


def build_message(
    *,
    to: str,
    sender: str,
    subject: str,
    body: str,
    attachments: list[tuple[str, bytes]],
) -> EmailMessage:
    message = EmailMessage()
    message["To"] = to
    message["From"] = sender
    message["Subject"] = subject
    message.set_content(body)
    for filename, content in attachments:
        message.add_attachment(content, maintype="application", subtype="pdf", filename=filename)
    return message


class OutboxBackend:
    """Writes rendered ``.eml`` files to disk instead of hitting an SMTP server.

    Makes the delivery flow work (and be testable) with zero configuration;
    swap to :class:`SmtpBackend` for real delivery.
    """

    def __init__(self, outbox_dir: Path, sender: str):
        self._outbox_dir = outbox_dir
        self._sender = sender

    @property
    def outbox_dir(self) -> Path:
        return self._outbox_dir

    def send(self, message: EmailMessage, *, ref: str) -> None:
        path = self._outbox_dir / f"{ref}.eml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(message.as_bytes())


class SmtpBackend:
    def __init__(
        self,
        host: str,
        port: int = 587,
        username: str = "",
        password: str = "",
        starttls: bool = True,
        timeout: float = 15.0,
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.starttls = starttls
        self.timeout = timeout

    def send(self, message: EmailMessage, *, ref: str) -> None:
        with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as server:
            if self.starttls:
                server.starttls()
            if self.username:
                server.login(self.username, self.password)
            server.send_message(message)


def build_email_backend(settings: Settings) -> Optional[EmailBackend]:
    """Return the configured delivery backend, or ``None`` when delivery is disabled."""
    if settings.email_backend == "none":
        return None
    if settings.email_backend == "outbox":
        return OutboxBackend(settings.storage_dir / "outbox", settings.email_from)
    if settings.email_backend == "smtp":
        if not settings.smtp_host:
            raise RuntimeError("CERT_SMTP_HOST is required when CERT_EMAIL_BACKEND=smtp")
        return SmtpBackend(
            host=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password,
            starttls=settings.smtp_starttls,
        )
    raise ValueError(f"unknown CERT_EMAIL_BACKEND: {settings.email_backend!r}")
