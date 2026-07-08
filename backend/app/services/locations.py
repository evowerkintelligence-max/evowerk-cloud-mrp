from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.location import Location


async def get_default_location_id(db: AsyncSession, org_id: uuid.UUID) -> uuid.UUID:
    """The org's single default location. Every org gets one at registration, so
    a missing one is a data-integrity error, not a normal 4xx."""
    location_id = (
        await db.execute(
            select(Location.id).where(Location.org_id == org_id, Location.is_default.is_(True))
        )
    ).scalar_one_or_none()
    if location_id is None:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "Org has no default location"
        )
    return location_id


async def resolve_location_id(db: AsyncSession, org_id: uuid.UUID, raw: str | None) -> uuid.UUID:
    """Turn an optional caller-supplied location_id into a validated one, falling
    back to the org's default location when omitted - so the location dimension
    stays optional for single-site callers and all existing (location-less) API
    usage keeps working against the default."""
    if raw is None:
        return await get_default_location_id(db, org_id)
    try:
        location_id = uuid.UUID(raw)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid location_id") from None
    exists = (
        await db.execute(
            select(Location.id).where(Location.id == location_id, Location.org_id == org_id)
        )
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Location not found")
    return location_id
