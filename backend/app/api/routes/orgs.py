from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.models.tenant import Org

router = APIRouter(prefix="/orgs", tags=["orgs"])


class OrgResponse(BaseModel):
    id: str
    name: str
    role: str


@router.get("/me", response_model=OrgResponse)
async def get_my_org(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Org).where(Org.id == current.org_id))
    org = result.scalar_one()
    return OrgResponse(id=str(org.id), name=org.name, role=current.role)
