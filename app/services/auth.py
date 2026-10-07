from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import AuthToken, User

PBKDF2_ITERATIONS = 100_000
MIN_PASSWORD_LENGTH = 8
SYSTEM_USER_EMAIL = "api-key@system.local"


class AuthError(ValueError):
    """Auth/service level error mapped to HTTP 4xx by the routes."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------- passwords
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ITERATIONS,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_b64, digest_b64 = encoded.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
    except (ValueError, TypeError):
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
    return hmac.compare_digest(candidate, expected)


def check_password_policy(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")


def _normalize_email(email: str) -> str:
    return email.strip().lower()


# ------------------------------------------------------------------- users
def get_user(db: Session, user_id: str) -> Optional[User]:
    return db.get(User, user_id)


def get_user_by_email(db: Session, email: str) -> Optional[User]:
    return db.scalar(select(User).where(User.email == _normalize_email(email)))


def register_user(
    db: Session,
    *,
    email: str,
    name: str,
    password: str,
    role: Optional[str] = None,
) -> User:
    check_password_policy(password)
    email = _normalize_email(email)
    name = name.strip()
    if not name:
        raise AuthError("name must not be blank")
    if get_user_by_email(db, email) is not None:
        raise AuthError("a user with this e-mail already exists")

    is_first = db.scalar(select(User.id).limit(1)) is None
    resolved_role = role or ("admin" if is_first else "member")
    if resolved_role not in ("admin", "member"):
        raise AuthError("role must be 'admin' or 'member'")

    now = utcnow()
    user = User(
        id=uuid4().hex,
        email=email,
        name=name,
        password_hash=hash_password(password),
        role=resolved_role,
        active=True,
        created_at=now,
        updated_at=now,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate(db: Session, *, email: str, password: str) -> User:
    user = get_user_by_email(db, email)
    if user is None or not verify_password(password, user.password_hash):
        raise AuthError("invalid e-mail or password")
    if not user.active:
        raise AuthError("account is deactivated")
    return user


def update_profile(
    db: Session,
    user: User,
    *,
    name: Optional[str] = None,
    current_password: Optional[str] = None,
    password: Optional[str] = None,
) -> User:
    if password is not None:
        if not current_password or not verify_password(current_password, user.password_hash):
            raise AuthError("current password is incorrect")
        check_password_policy(password)
        user.password_hash = hash_password(password)
    if name is not None:
        cleaned = name.strip()
        if not cleaned:
            raise AuthError("name must not be blank")
        user.name = cleaned
    user.updated_at = utcnow()
    db.commit()
    db.refresh(user)
    return user


# ------------------------------------------------------------------ tokens
def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_token(db: Session, user: User, settings: Settings) -> Tuple[str, AuthToken]:
    plaintext = secrets.token_urlsafe(32)
    now = utcnow()
    record = AuthToken(
        id=uuid4().hex,
        user_id=user.id,
        token_hash=_hash_token(plaintext),
        created_at=now,
        expires_at=now + timedelta(hours=settings.token_ttl_hours),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return plaintext, record


def user_for_token(db: Session, token: str) -> Optional[User]:
    """Resolve a bearer token to an active, unexpired user (touching last_used_at)."""
    if not token:
        return None
    record = db.scalar(select(AuthToken).where(AuthToken.token_hash == _hash_token(token)))
    if record is None or record.revoked_at is not None:
        return None
    if record.expires_at < utcnow():
        return None
    user = db.get(User, record.user_id)
    if user is None or not user.active:
        return None
    record.last_used_at = utcnow()
    db.commit()
    return user


def revoke_token(db: Session, token_id: str, user: User) -> bool:
    record = db.get(AuthToken, token_id)
    if record is None or record.user_id != user.id:
        return False
    if record.revoked_at is None:
        record.revoked_at = utcnow()
        db.commit()
    return True


def list_tokens(db: Session, user: User) -> List[AuthToken]:
    rows = db.execute(
        select(AuthToken)
        .where(AuthToken.user_id == user.id, AuthToken.revoked_at.is_(None))
        .order_by(AuthToken.created_at.desc())
    )
    return list(rows.scalars())


def revoke_all_tokens(db: Session, user: User) -> int:
    rows = list_tokens(db, user)
    for record in rows:
        record.revoked_at = utcnow()
    db.commit()
    return len(rows)


# ------------------------------------------------------------- system user
def system_user(db: Session) -> User:
    """The virtual admin identity used for X-API-Key machine access."""
    user = get_user_by_email(db, SYSTEM_USER_EMAIL)
    if user is not None:
        return user
    now = utcnow()
    user = User(
        id=uuid4().hex,
        email=SYSTEM_USER_EMAIL,
        name="API Key",
        password_hash=hash_password(secrets.token_urlsafe(32)),
        role="admin",
        active=True,
        created_at=now,
        updated_at=now,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
