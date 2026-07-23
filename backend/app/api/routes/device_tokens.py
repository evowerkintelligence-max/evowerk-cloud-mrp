from __future__ import annotations

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.models.device_token import DeviceToken

router = APIRouter(prefix="/device-tokens", tags=["device-tokens"])


class DeviceTokenRequest(BaseModel):
    expo_push_token: str = Field(..., min_length=1)


@router.post("", status_code=status.HTTP_204_NO_CONTENT)
async def register_device_token(
    payload: DeviceTokenRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    existing = (
        await db.execute(select(DeviceToken).where(DeviceToken.expo_push_token == payload.expo_push_token))
    ).scalar_one_or_none()
    if existing is not None:
        existing.org_id = current.org_id
        existing.user_id = current.id
    else:
        db.add(DeviceToken(org_id=current.org_id, user_id=current.id, expo_push_token=payload.expo_push_token))
    await db.commit()


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def unregister_device_token(
    expo_push_token: str,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    existing = (
        await db.execute(
            select(DeviceToken).where(
                DeviceToken.expo_push_token == expo_push_token,
                DeviceToken.user_id == current.id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        await db.delete(existing)
        await db.commit()
