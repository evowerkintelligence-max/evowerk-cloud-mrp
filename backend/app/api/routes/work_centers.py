from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_or_404, require_role
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.work_center import WorkCenter

router = APIRouter(prefix="/work-centers", tags=["work-centers"])

_STATUSES = {"active", "inactive", "retired"}


class WorkCenterCreateRequest(BaseModel):
    code: str
    name: str
    machine_capacity: int = 1
    default_cycle_time_seconds: float | None = None
    default_mtbf_hours: float | None = None
    default_mttr_hours: float | None = None


class WorkCenterUpdateRequest(BaseModel):
    name: str | None = None
    status: str | None = None
    machine_capacity: int | None = None
    default_cycle_time_seconds: float | None = None
    default_mtbf_hours: float | None = None
    default_mttr_hours: float | None = None


class WorkCenterResponse(BaseModel):
    id: str
    code: str
    name: str
    status: str
    machine_capacity: int
    default_cycle_time_seconds: float | None
    default_mtbf_hours: float | None
    default_mttr_hours: float | None


def _to_response(wc: WorkCenter) -> WorkCenterResponse:
    return WorkCenterResponse(
        id=str(wc.id),
        code=wc.code,
        name=wc.name,
        status=wc.status,
        machine_capacity=wc.machine_capacity,
        default_cycle_time_seconds=wc.default_cycle_time_seconds,
        default_mtbf_hours=wc.default_mtbf_hours,
        default_mttr_hours=wc.default_mttr_hours,
    )


@router.get("", response_model=list[WorkCenterResponse])
async def list_work_centers(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(WorkCenter).where(WorkCenter.org_id == current.org_id).order_by(WorkCenter.code)
    )
    return [_to_response(wc) for wc in result.scalars().all()]


@router.post("", response_model=WorkCenterResponse, status_code=status.HTTP_201_CREATED)
async def create_work_center(
    body: WorkCenterCreateRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    if body.machine_capacity < 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "machine_capacity must be at least 1")
    wc = WorkCenter(
        org_id=current.org_id,
        code=body.code,
        name=body.name,
        machine_capacity=body.machine_capacity,
        default_cycle_time_seconds=body.default_cycle_time_seconds,
        default_mtbf_hours=body.default_mtbf_hours,
        default_mttr_hours=body.default_mttr_hours,
    )
    db.add(wc)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "code already exists in this org") from None

    await log_event(
        db, action="work_center.created", org_id=current.org_id, actor_user_id=current.id,
        target=str(wc.id), metadata={"code": wc.code},
    )
    await db.commit()
    return _to_response(wc)


@router.patch("/{work_center_id}", response_model=WorkCenterResponse)
async def update_work_center(
    work_center_id: uuid.UUID,
    body: WorkCenterUpdateRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    wc = await get_or_404(db, WorkCenter, current.org_id, work_center_id, "Work center not found")
    if body.status is not None and body.status not in _STATUSES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"status must be one of {sorted(_STATUSES)}")
    if body.machine_capacity is not None and body.machine_capacity < 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "machine_capacity must be at least 1")

    fields: dict[str, Any] = body.model_dump(exclude_unset=True)
    for key, value in fields.items():
        setattr(wc, key, value)

    await log_event(
        db, action="work_center.updated", org_id=current.org_id, actor_user_id=current.id,
        target=str(wc.id), metadata=fields,
    )
    await db.commit()
    await db.refresh(wc)
    return _to_response(wc)
