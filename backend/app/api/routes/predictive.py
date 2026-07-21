from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.services import predictive as service

router = APIRouter(prefix="/predictive", tags=["predictive"])


# ─── Maintenance schedule CRUD ──────────────────────────────────────────────

ScheduleStatus = Literal["pending", "in_progress", "done", "skipped"]
SchedulePriority = Literal["immediate", "scheduled", "monitor"]


class ScheduleCreate(BaseModel):
    machine: str = Field(..., min_length=1)
    priority: SchedulePriority = "scheduled"
    action: str = Field(..., min_length=1)
    reason: str = ""
    scheduled_date: str | None = None
    assigned_to: str | None = None
    pm_hours: float = 8.0
    notes: str = ""


class ScheduleUpdate(BaseModel):
    priority: SchedulePriority | None = None
    action: str | None = None
    reason: str | None = None
    scheduled_date: str | None = None
    assigned_to: str | None = None
    status: ScheduleStatus | None = None
    pm_hours: float | None = None
    notes: str | None = None


class BulkScheduleRequest(BaseModel):
    items: list[ScheduleCreate]


class ScheduleResponse(BaseModel):
    id: uuid.UUID
    machine: str
    priority: str
    action: str
    reason: str
    scheduled_date: str | None
    assigned_to: str | None
    status: str
    pm_hours: float
    notes: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


@router.get("/schedules", response_model=list[ScheduleResponse])
async def list_schedules(current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await service.list_schedules(db, current.org_id)


@router.post("/schedules", response_model=ScheduleResponse, status_code=status.HTTP_201_CREATED)
async def create_schedule(payload: ScheduleCreate, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await service.create_schedule(db, current.org_id, current.id, **payload.model_dump())


@router.post("/schedules/bulk", response_model=list[ScheduleResponse], status_code=status.HTTP_201_CREATED)
async def bulk_create_schedules(payload: BulkScheduleRequest, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await service.bulk_create_schedules(db, current.org_id, current.id, [i.model_dump() for i in payload.items])


@router.patch("/schedules/{schedule_id}", response_model=ScheduleResponse)
async def update_schedule(schedule_id: uuid.UUID, payload: ScheduleUpdate, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    patch = payload.model_dump(exclude_unset=True)
    try:
        return await service.update_schedule(db, current.org_id, schedule_id, patch)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.delete("/schedules/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(schedule_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        await service.delete_schedule(db, current.org_id, schedule_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
