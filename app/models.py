from __future__ import annotations

from datetime import date, datetime
from typing import List, Optional

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class User(Base):
    """An organizer account. The first registered user becomes the admin."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="member")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class AuthToken(Base):
    """A revocable bearer token (stored hashed)."""

    __tablename__ = "auth_tokens"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    user: Mapped[User] = relationship()


class Template(Base):
    """An organizer-uploaded certificate template (PDF + e-mail/visual defaults)."""

    __tablename__ = "templates"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    font_file_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    email_subject: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    email_body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    font_family: Mapped[str] = mapped_column(String(32), nullable=False, default="helvetica")
    font_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    font_color: Mapped[str] = mapped_column(String(7), nullable=False, default="#000000")
    placeholders: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class CertificateJob(Base):
    __tablename__ = "certificate_jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    event_name: Mapped[str] = mapped_column(String(200), nullable=False)
    event_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    template_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("templates.id"), nullable=True, index=True
    )
    template_name: Mapped[str] = mapped_column(String(64), nullable=False)
    fields: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    send_email: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    email_subject: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    email_body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    id_prefix: Mapped[str] = mapped_column(String(20), nullable=False, default="CERT")
    qr_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("users.id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    template: Mapped[Optional[Template]] = relationship()

    certificates: Mapped[List["Certificate"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="Certificate.sequence",
    )


class Certificate(Base):
    __tablename__ = "certificates"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    job_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("certificate_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    certificate_code: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    verify_token: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    recipient_name: Mapped[str] = mapped_column(String(500), nullable=False)
    recipient_email: Mapped[Optional[str]] = mapped_column(String(320), nullable=True)
    data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    delivery_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    delivery_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    revoke_reason: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    file_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    job: Mapped[CertificateJob] = relationship(back_populates="certificates")
