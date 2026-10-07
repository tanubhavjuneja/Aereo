from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///./data/certificates.db"
    storage_dir: Path = Path("./data/certificates")
    worker_concurrency: int = 2
    max_recipients_per_job: int = 1000
    max_recipient_name_length: int = 120
    template_name: str = "standard-award-v1"
    api_prefix: str = "/api/v1"
    email_backend: str = "outbox"  # outbox | smtp | none
    email_from: str = "certificates@example.com"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = True
    public_base_url: str = "http://localhost:8000"
    api_key: str = ""  # machine credential: sent as X-API-Key, bypasses user login
    default_id_prefix: str = "CERT"
    allow_registration: bool = True  # CERT_ALLOW_REGISTRATION=0 closes /auth/register
    token_ttl_hours: int = 720  # bearer token lifetime (10 days)


def _bool_env(name: str, default: str = "1") -> bool:
    return os.getenv(name, default).strip().lower() not in ("0", "false", "no")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        database_url=os.getenv("CERT_DATABASE_URL", "sqlite:///./data/certificates.db"),
        storage_dir=Path(os.getenv("CERT_STORAGE_DIR", "./data/certificates")),
        worker_concurrency=max(1, int(os.getenv("CERT_WORKER_CONCURRENCY", "2"))),
        max_recipients_per_job=max(1, int(os.getenv("CERT_MAX_RECIPIENTS_PER_JOB", "1000"))),
        email_backend=os.getenv("CERT_EMAIL_BACKEND", "outbox").strip().lower(),
        email_from=os.getenv("CERT_EMAIL_FROM", "certificates@example.com"),
        smtp_host=os.getenv("CERT_SMTP_HOST", ""),
        smtp_port=int(os.getenv("CERT_SMTP_PORT", "587")),
        smtp_username=os.getenv("CERT_SMTP_USERNAME", ""),
        smtp_password=os.getenv("CERT_SMTP_PASSWORD", ""),
        smtp_starttls=_bool_env("CERT_SMTP_STARTTLS"),
        public_base_url=os.getenv("CERT_PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/"),
        api_key=os.getenv("CERT_API_KEY", ""),
        default_id_prefix=os.getenv("CERT_DEFAULT_ID_PREFIX", "CERT"),
        allow_registration=_bool_env("CERT_ALLOW_REGISTRATION"),
        token_ttl_hours=max(1, int(os.getenv("CERT_TOKEN_TTL_HOURS", "720"))),
    )
