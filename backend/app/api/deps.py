from __future__ import annotations

import uuid
from typing import TypeVar

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_token
from app.db.base import Base
from app.db.session import get_db
from app.models.tenant import Org
from app.models.user import User

ModelT = TypeVar("ModelT", bound=Base)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


class CurrentUser:
    def __init__(self, user: User):
        self.user = user
        self.id = user.id
        self.org_id = user.org_id
        self.role = user.role


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> CurrentUser:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            raise credentials_error
        user_id = uuid.UUID(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise credentials_error from exc

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise credentials_error
    return CurrentUser(user)


def require_role(*roles: str):
    async def _check(current: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if current.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient permissions")
        return current

    return _check


def require_feature(key: str):
    async def _check(
        current: CurrentUser = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> CurrentUser:
        org_features = (
            await db.execute(select(Org.enabled_features).where(Org.id == current.org_id))
        ).scalar_one()
        if key not in org_features:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not included in your plan")
        return current

    return _check


async def require_staff(current: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not current.user.is_staff:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Staff only")
    return current


async def get_or_404(
    db: AsyncSession,
    model: type[ModelT],
    org_id: uuid.UUID,
    obj_id: uuid.UUID,
    not_found_msg: str,
    *extra_filters: ColumnElement[bool],
    for_update: bool = False,
) -> ModelT:
    query = select(model).where(model.id == obj_id, model.org_id == org_id, *extra_filters)
    if for_update:
        # Locks the row for the rest of this transaction so a second concurrent
        # request touching the same row (e.g. two submits/receives on one PO)
        # blocks until this one commits, then re-reads the post-commit state
        # instead of racing against it.
        query = query.with_for_update()
    result = await db.execute(query)
    obj = result.scalar_one_or_none()
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, not_found_msg)
    return obj
