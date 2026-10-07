from __future__ import annotations

import secrets
from typing import Optional

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import CertificateJob, User
from ..services import auth as auth_service
from .common import get_db

BEARER_PREFIX = "Bearer "


def _bearer_token(request: Request) -> Optional[str]:
    header = request.headers.get("Authorization", "")
    if header.startswith(BEARER_PREFIX):
        return header[len(BEARER_PREFIX) :].strip()
    return header.strip() if header and " " not in header else None


def require_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Authenticate the caller: valid X-API-Key (machine) or Bearer token (user).

    Raises 401 when neither credential is present/valid. Applied as a router
    level dependency to every management endpoint; verification, health,
    register and login stay public.
    """
    settings: Settings = request.app.state.settings

    api_key = request.headers.get("X-API-Key")
    if settings.api_key and api_key and secrets.compare_digest(api_key, settings.api_key):
        request.state.api_key_auth = True
        return auth_service.system_user(db)

    token = _bearer_token(request)
    if token:
        user = auth_service.user_for_token(db, token)
        if user is not None:
            request.state.api_key_auth = False
            return user

    raise HTTPException(
        status_code=401,
        detail="not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_admin(user: User = Depends(require_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="admin privileges required")
    return user


def ensure_job_access(user: User, job: CertificateJob) -> None:
    """Members only see their own jobs; admins see everything (404 to hide existence)."""
    if user.role == "admin":
        return
    if job.created_by and job.created_by != user.id:
        raise HTTPException(status_code=404, detail="job not found")


def job_scope_filter(user: User):
    """SQLAlchemy filter restricting rows to the caller's jobs (no-op for admins)."""
    if user.role == "admin":
        return None
    return CertificateJob.created_by == user.id
