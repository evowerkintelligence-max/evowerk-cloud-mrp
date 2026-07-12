from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.services import datasets as ds_service
from app.services import predictive as service

analyze_router = APIRouter(prefix="/datasets/{dataset_id}/predictive", tags=["predictive"])
router = APIRouter(prefix="/predictive", tags=["predictive"])


class PredictiveRequest(BaseModel):
    date_col: str = Field(..., min_length=1)
    machine_col: str = Field(..., min_length=1)
    planned_time_col: str = Field(..., min_length=1)
    operating_time_col: str = Field(..., min_length=1)
    total_units_col: str = Field(..., min_length=1)
    good_units_col: str = Field(..., min_length=1)
    target_rate: float | None = None
    failure_col: str | None = None
    downtime_category_col: str | None = None
    hourly_value: float = 1000.0


class MachineRiskResponse(BaseModel):
    machine: str
    current_oee: float
    trend_slope: float
    failure_probability: float
    risk_level: str
    top_driver: str
    rul_days: float | None
    last_failure_days_ago: float | None
    ml_powered: bool
    ml_drivers: list[dict]


class ModelMetaResponse(BaseModel):
    algorithm: str
    accuracy_pct: float
    n_training_samples: int
    top_features: list[dict]


class MaintenanceRecResponse(BaseModel):
    machine: str
    priority: str
    action: str
    reason: str
    estimated_pm_hours: float
    avoided_downtime_hours: float
    net_saving: float


class CostSummaryResponse(BaseModel):
    total_avoided_cost: float
    total_pm_cost: float
    net_savings: float
    roi_pct: float
    machines_at_risk: int


class PredictiveResponse(BaseModel):
    machines: list[MachineRiskResponse]
    recommendations: list[MaintenanceRecResponse]
    cost_summary: CostSummaryResponse
    model_meta: ModelMetaResponse | None


@analyze_router.post("/analyze", response_model=PredictiveResponse)
async def analyze_predictive(
    dataset_id: uuid.UUID,
    req: PredictiveRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PredictiveResponse:
    dataset = await ds_service.get_dataset_or_404(db, current.org_id, dataset_id)
    result = service.analyze(dataset, **req.model_dump())
    return PredictiveResponse(
        machines=[MachineRiskResponse(**vars(m)) for m in result.machines],
        recommendations=[MaintenanceRecResponse(**vars(r)) for r in result.recommendations],
        cost_summary=CostSummaryResponse(**vars(result.cost_summary)),
        model_meta=ModelMetaResponse(**vars(result.model_meta)) if result.model_meta else None,
    )


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
