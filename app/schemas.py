from __future__ import annotations

from datetime import date, datetime
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

JOB_STATUSES = ("queued", "processing", "completed", "failed")
CERTIFICATE_STATUSES = ("pending", "succeeded", "failed")
DELIVERY_STATUSES = ("pending", "sent", "failed", "skipped")
EXPORT_FORMATS = ("zip", "pdf", "png")

#: Schema used when the request does not declare its own ``fields``.
DEFAULT_FIELDS: List[dict] = [
    {"key": "name", "label": "Participant Name", "required": True, "role": "primary", "visible": True},
    {"key": "email", "label": "Email", "required": True, "role": "email", "visible": False},
]


class FieldDef(BaseModel):
    """Definition of one dynamic column of a certificate/recipients."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(
        max_length=40,
        pattern=r"^[a-zA-Z][a-zA-Z0-9_]*$",
        description="Identifier used in recipient rows, CSV headers and {placeholders}.",
    )
    label: str = Field(min_length=1, max_length=60, description="Label printed on the certificate.")
    required: bool = Field(default=True, description="Every recipient row must carry a non-empty value.")
    role: Literal["primary", "email", "text"] = Field(
        default="text",
        description=(
            "'primary' is rendered prominently (only one allowed); "
            "'email' is where the certificate is delivered (only one allowed); "
            "'text' is a normal detail field."
        ),
    )
    visible: bool = Field(default=True, description="Whether the value is printed on the certificate.")


class CertificateJobSpec(BaseModel):
    """Job parameters shared by the JSON and CSV creation endpoints."""

    model_config = ConfigDict(extra="forbid")

    event_name: str = Field(min_length=1, max_length=200, description="Course/event the certificate is for.")
    event_date: Optional[date] = Field(default=None, description="Event date (ISO, e.g. 2026-10-07).")
    template_id: Optional[str] = Field(
        default=None,
        max_length=32,
        description="Id of an uploaded template to render. Omit for the built-in template.",
    )
    fields: Optional[List[FieldDef]] = Field(
        default=None,
        max_length=30,
        description="Dynamic column definitions. Defaults to name + email when omitted.",
    )
    send_email: bool = Field(default=True, description="Deliver the generated PDF to each recipient's e-mail.")
    email_subject: Optional[str] = Field(default=None, max_length=200, description="Supports {placeholder} syntax.")
    email_body: Optional[str] = Field(default=None, max_length=5000, description="Supports {placeholder} syntax.")
    id_prefix: str = Field(
        default="CERT",
        max_length=20,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
        description="Prefix for human readable certificate numbers, e.g. CERT-0007.",
    )
    qr_enabled: bool = Field(default=True, description="Draw a QR code linking to the public verification page.")

    @field_validator("event_name")
    @classmethod
    def _event_name_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("event_name must not be blank")
        return value

    @model_validator(mode="after")
    def _check_field_definitions(self):
        if not self.fields:
            return self
        keys = [f.key for f in self.fields]
        if len(set(keys)) != len(keys):
            raise ValueError("field keys must be unique")
        roles = [f.role for f in self.fields]
        if roles.count("primary") > 1:
            raise ValueError("only one field may have role 'primary'")
        if roles.count("email") > 1:
            raise ValueError("only one field may have role 'email'")
        if self.send_email and "email" not in roles:
            raise ValueError("send_email=true requires a field with role 'email'")
        return self


class CertificateJobCreate(CertificateJobSpec):
    model_config = ConfigDict(extra="forbid")

    recipients: List[dict[str, Any]] = Field(
        min_length=1,
        description="One object per participant; keys must match the declared field keys.",
    )


class CertificatePatch(BaseModel):
    """Fix recipient data for one certificate (e.g. a typo) and optionally resend."""

    model_config = ConfigDict(extra="forbid")

    data: Optional[dict[str, Any]] = Field(
        default=None, description="Partial field updates, merged into the existing recipient data."
    )
    resend: bool = Field(default=False, description="Re-send the certificate by e-mail after regenerating it.")


class Counts(BaseModel):
    total: int = 0
    pending: int = 0
    succeeded: int = 0
    failed: int = 0
    sent: int = 0
    delivery_failed: int = 0
    revoked: int = 0


class JobOut(BaseModel):
    id: str
    status: str
    event_name: str
    event_date: Optional[date]
    template_id: Optional[str] = None
    template_name: str
    send_email: bool
    id_prefix: str
    qr_enabled: bool
    counts: Counts
    error: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class CertificateOut(BaseModel):
    id: str
    job_id: str
    event_name: Optional[str] = None
    certificate_code: str
    verification_url: Optional[str] = None
    recipient_name: str
    recipient_email: Optional[str]
    data: dict
    status: str
    error: Optional[str]
    delivery_status: str
    delivery_error: Optional[str]
    revoked: bool
    revoke_reason: Optional[str]
    download_url: Optional[str]
    created_at: datetime
    updated_at: datetime


class CertificateListOut(BaseModel):
    job_id: str
    total: int
    items: List[CertificateOut]


class CertificateSearchOut(BaseModel):
    total: int
    items: List[CertificateOut]


class TemplateOut(BaseModel):
    id: str
    name: str
    email_subject: Optional[str]
    email_body: Optional[str]
    font_family: str
    font_size: Optional[int]
    font_color: str
    has_custom_font: bool = False
    placeholders: List[str]
    download_url: str
    created_at: datetime


class TemplateListOut(BaseModel):
    total: int
    items: List[TemplateOut]


class PreviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_id: Optional[str] = Field(default=None, max_length=32)
    event_name: str = Field(default="Preview", min_length=1, max_length=200)
    event_date: Optional[date] = None
    data: dict[str, Any] = Field(default_factory=dict, description="Sample recipient values for the placeholders.")


class VerifyOut(BaseModel):
    status: Literal["issued", "revoked"]
    certificate_code: str
    event_name: str
    event_date: Optional[date]
    recipient_name: str
    data: dict
    template_name: str
    issued_at: datetime
    revoked_at: Optional[datetime] = None
    revoke_reason: Optional[str] = None
    download_url: str


# --------------------------------------------------------------------- auth
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$"


class RegisterIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=5, max_length=320, pattern=EMAIL_PATTERN)
    name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=8, max_length=128)


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=5, max_length=320, pattern=EMAIL_PATTERN)
    password: str = Field(min_length=1, max_length=128)


class UserOut(BaseModel):
    id: str
    email: str
    name: str
    role: Literal["admin", "member"]
    active: bool
    created_at: datetime


class TokenOut(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    user: UserOut


class UserUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    current_password: Optional[str] = Field(default=None, max_length=128)
    password: Optional[str] = Field(default=None, min_length=8, max_length=128)


class UserCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=5, max_length=320, pattern=EMAIL_PATTERN)
    name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=8, max_length=128)
    role: Literal["admin", "member"] = "member"


class UserAdminUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    role: Optional[Literal["admin", "member"]] = None
    active: Optional[bool] = None


class TokenInfoOut(BaseModel):
    id: str
    created_at: datetime
    expires_at: datetime
    last_used_at: Optional[datetime] = None
    current: bool = False


class TokenListOut(BaseModel):
    total: int
    items: List[TokenInfoOut]


# -------------------------------------------------------------- management
class JobListOut(BaseModel):
    total: int
    items: List[JobOut]


class ParticipantOut(BaseModel):
    id: str
    name: str
    email: Optional[str] = None
    certificate_count: int
    revoked_count: int
    last_issued_at: Optional[datetime] = None


class ParticipantListOut(BaseModel):
    total: int
    items: List[ParticipantOut]


class ParticipantDetailOut(ParticipantOut):
    certificates: List[CertificateOut] = Field(default_factory=list)


class DashboardStats(BaseModel):
    jobs: dict
    certificates: dict
    deliveries: dict
    participants: int
    templates: int


class UsersListOut(BaseModel):
    total: int
    items: List[UserOut]
