from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, require_role
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.tenant import Org

router = APIRouter(prefix="/orgs", tags=["orgs"])


class OrgResponse(BaseModel):
    id: str
    name: str
    role: str
    user_id: str
    adjust_approval_threshold: Decimal
    enabled_features: list[str]


class OrgUpdateRequest(BaseModel):
    adjust_approval_threshold: Decimal


@router.get("/me", response_model=OrgResponse)
async def get_my_org(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Org).where(Org.id == current.org_id))
    org = result.scalar_one()
    return OrgResponse(
        id=str(org.id),
        name=org.name,
        role=current.role,
        user_id=str(current.id),
        adjust_approval_threshold=org.adjust_approval_threshold,
        enabled_features=org.enabled_features,
    )


@router.patch("/me", response_model=OrgResponse)
async def update_my_org(
    body: OrgUpdateRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    if body.adjust_approval_threshold < 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "threshold must be zero or positive")
    result = await db.execute(select(Org).where(Org.id == current.org_id))
    org = result.scalar_one()
    org.adjust_approval_threshold = body.adjust_approval_threshold
    await log_event(
        db,
        action="org.threshold_updated",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(org.id),
        metadata={"adjust_approval_threshold": str(body.adjust_approval_threshold)},
    )
    await db.commit()
    return OrgResponse(
        id=str(org.id),
        name=org.name,
        role=current.role,
        user_id=str(current.id),
        adjust_approval_threshold=org.adjust_approval_threshold,
        enabled_features=org.enabled_features,
    )
