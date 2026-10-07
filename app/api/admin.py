from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import User
from ..schemas import UserAdminUpdateIn, UserCreateIn, UserOut, UsersListOut
from ..services import auth as auth_service
from .auth import _user_out
from .common import get_db
from .deps import require_admin

router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("/users", response_model=UsersListOut, summary="List all organizer accounts (admin)")
def list_users(
    admin: User = Depends(require_admin),
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> UsersListOut:
    total = int(db.execute(select(func.count()).select_from(User)).scalar_one())
    rows = db.execute(
        select(User).order_by(User.created_at.desc()).offset(offset).limit(min(limit, 500))
    ).scalars()
    return UsersListOut(total=total, items=[_user_out(user) for user in rows])


@router.post(
    "/users",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account on behalf of someone else (admin)",
)
def create_user(payload: UserCreateIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> UserOut:
    if auth_service.get_user_by_email(db, payload.email) is not None:
        raise HTTPException(status_code=409, detail="a user with this e-mail already exists")
    try:
        user = auth_service.register_user(
            db,
            email=payload.email,
            name=payload.name,
            password=payload.password,
            role=payload.role,
        )
    except auth_service.AuthError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _user_out(user)


@router.patch("/users/{user_id}", response_model=UserOut, summary="Update a user's role/status (admin)")
def update_user(
    user_id: str,
    payload: UserAdminUpdateIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> UserOut:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    if payload.name is not None:
        user.name = payload.name.strip() or user.name
    if payload.role is not None:
        if user.id == admin.id and payload.role != "admin":
            raise HTTPException(status_code=409, detail="admins cannot demote themselves")
        user.role = payload.role
    if payload.active is not None:
        if user.id == admin.id and not payload.active:
            raise HTTPException(status_code=409, detail="admins cannot deactivate themselves")
        user.active = payload.active
    user.updated_at = auth_service.utcnow()
    db.commit()
    db.refresh(user)
    return _user_out(user)
