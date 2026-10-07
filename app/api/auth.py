from __future__ import annotations

import hashlib
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import AuthToken, User
from ..schemas import (
    LoginIn,
    RegisterIn,
    TokenInfoOut,
    TokenListOut,
    TokenOut,
    UserOut,
    UserUpdateIn,
)
from ..services import auth as auth_service
from .common import get_db
from .deps import _bearer_token, require_user

router = APIRouter()


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        name=user.name,
        role=user.role,  # type: ignore[arg-type]
        active=user.active,
        created_at=user.created_at,
    )


def _token_out(plaintext: str, record, user: User, settings: Settings) -> TokenOut:
    expires_in = max(0, int((record.expires_at - auth_service.utcnow()).total_seconds()))
    return TokenOut(access_token=plaintext, expires_in=expires_in, user=_user_out(user))


@router.post(
    "/auth/register",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create an organizer account (the very first account becomes admin)",
)
def register(payload: RegisterIn, request: Request, db: Session = Depends(get_db)) -> UserOut:
    settings: Settings = request.app.state.settings
    if not settings.allow_registration:
        raise HTTPException(status_code=403, detail="registration is closed on this instance")
    if auth_service.get_user_by_email(db, payload.email) is not None:
        raise HTTPException(status_code=409, detail="a user with this e-mail already exists")
    try:
        user = auth_service.register_user(
            db, email=payload.email, name=payload.name, password=payload.password
        )
    except auth_service.AuthError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _user_out(user)


@router.post(
    "/auth/login",
    response_model=TokenOut,
    summary="Exchange e-mail + password for a bearer token",
)
def login(payload: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)) -> TokenOut:
    settings: Settings = request.app.state.settings
    try:
        user = auth_service.authenticate(db, email=payload.email, password=payload.password)
    except auth_service.AuthError as exc:
        raise HTTPException(
            status_code=401,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        )
    plaintext, record = auth_service.issue_token(db, user, settings)
    return _token_out(plaintext, record, user, settings)


@router.post("/auth/logout", summary="Revoke the token used for this request")
def logout(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    token = _bearer_token(request)
    revoked = False
    if token and not getattr(request.state, "api_key_auth", False):
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        record = db.scalar(select(AuthToken).where(AuthToken.token_hash == token_hash))
        if record is not None and record.user_id == user.id and record.revoked_at is None:
            record.revoked_at = auth_service.utcnow()
            db.commit()
            revoked = True
    return {"revoked": revoked}


@router.get("/auth/me", response_model=UserOut, summary="Fetch the current user's profile")
def me(user: User = Depends(require_user)) -> UserOut:
    return _user_out(user)


@router.patch("/auth/me", response_model=UserOut, summary="Update name and/or password")
def update_me(
    payload: UserUpdateIn,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> UserOut:
    if payload.name is None and payload.password is None:
        raise HTTPException(status_code=422, detail="nothing to update")
    if payload.password is not None and payload.current_password is None:
        raise HTTPException(status_code=422, detail="current_password is required to change the password")
    try:
        updated = auth_service.update_profile(
            db,
            user,
            name=payload.name,
            current_password=payload.current_password,
            password=payload.password,
        )
    except auth_service.AuthError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _user_out(updated)


@router.get("/auth/tokens", response_model=TokenListOut, summary="List the current user's active sessions")
def list_tokens(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)) -> TokenListOut:
    token = _bearer_token(request)
    current_hash = hashlib.sha256(token.encode("utf-8")).hexdigest() if token else None
    records = db.execute(
        select(AuthToken).where(AuthToken.user_id == user.id, AuthToken.revoked_at.is_(None))
    ).scalars()
    items = [
        TokenInfoOut(
            id=record.id,
            created_at=record.created_at,
            expires_at=record.expires_at,
            last_used_at=record.last_used_at,
            current=bool(current_hash and record.token_hash == current_hash),
        )
        for record in records
    ]
    return TokenListOut(total=len(items), items=items)


@router.delete("/auth/tokens/{token_id}", summary="Revoke one of the current user's sessions")
def revoke_token(token_id: str, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    if not auth_service.revoke_token(db, token_id, user):
        raise HTTPException(status_code=404, detail="token not found")
    return {"revoked": True, "id": token_id}
