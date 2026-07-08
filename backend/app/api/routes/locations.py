from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, require_role
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.location import Location

router = APIRouter(prefix="/locations", tags=["locations"])


class LocationCreateRequest(BaseModel):
    code: str
    name: str


class LocationResponse(BaseModel):
    id: str
    code: str
    name: str
    is_default: bool


def _to_response(location: Location) -> LocationResponse:
    return LocationResponse(
        id=str(location.id),
        code=location.code,
        name=location.name,
        is_default=location.is_default,
    )


@router.get("", response_model=list[LocationResponse])
async def list_locations(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Location).where(Location.org_id == current.org_id).order_by(Location.code)
    )
    return [_to_response(loc) for loc in result.scalars().all()]


@router.post("", response_model=LocationResponse, status_code=status.HTTP_201_CREATED)
async def create_location(
    body: LocationCreateRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    # New locations are never the default: the default is the org's original
    # location, created at registration, and there's exactly one.
    location = Location(org_id=current.org_id, code=body.code, name=body.name, is_default=False)
    db.add(location)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "code already exists in this org") from None

    await log_event(
        db,
        action="location.created",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(location.id),
        metadata={"code": location.code},
    )
    await db.commit()
    return _to_response(location)
