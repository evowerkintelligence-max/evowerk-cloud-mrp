from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.services import datasets as ds_service
from app.services import oee as oee_service

router = APIRouter(prefix="/analytics", tags=["analytics"])


# ─── Request / Response (mirrors the desktop app's OeeCompute contract) ────────

class OeeComputeRequest(BaseModel):
    dataset_id: str
    planned_time_col: str = Field(..., min_length=1)
    operating_time_col: str = Field(..., min_length=1)
    total_units_col: str = Field(..., min_length=1)
    good_units_col: str = Field(..., min_length=1)
    target_rate: Optional[float] = None
    date_col: Optional[str] = None
    machine_col: Optional[str] = None
    unit_price: Optional[float] = None
    machine_rates: Optional[Dict[str, float]] = None
    unplanned_downtime_col: Optional[str] = None
    planned_downtime_col: Optional[str] = None
    minor_stops_col: Optional[str] = None
    startup_rejects_col: Optional[str] = None


class OeeTrendPoint(BaseModel):
    date: str
    availability: float
    performance: float
    quality: float
    oee: float
    total_units: float
    invalid_cells: int = 0
    blank_cells: int = 0


class OeeMachineResult(BaseModel):
    machine: str
    availability: float
    performance: float
    quality: float
    oee: float
    total_units: float
    good_units: float
    total_planned: float
    total_operating: float
    revenue_lost: Optional[float] = None
    invalid_cells: int = 0
    blank_cells: int = 0
    rate_used: Optional[float] = None
    utilization: Optional[float] = None
    teep: Optional[float] = None


class RevenueImpactMachine(BaseModel):
    machine: str
    total_lost: float
    availability_lost: float
    performance_lost: float
    quality_lost: float


class RevenueImpact(BaseModel):
    unit_price: float
    total_lost: float
    availability_lost: float
    performance_lost: float
    quality_lost: float
    daily_rate: float
    monthly_rate: float
    benchmark_saving: float
    period_days: float
    period_days_estimated: bool = False
    performance_estimated: bool = False
    per_machine: List[RevenueImpactMachine] = []


class TeepResult(BaseModel):
    teep: float
    utilization: float
    calendar_days: int
    calendar_minutes: float
    machine_count: int
    machine_count_assumed: bool


class SixLossBucket(BaseModel):
    key: str
    label: str
    family: str
    minutes: float
    units: Optional[float] = None
    cost: Optional[float] = None
    pct_of_loss: float = 0.0


class SixBigLosses(BaseModel):
    total_loss_minutes: float
    buckets: List[SixLossBucket]
    availability_split: bool
    performance_split: bool
    quality_split: bool
    performance_estimated: bool


class OeeComputeResponse(BaseModel):
    availability: float
    performance: float
    quality: float
    oee: float
    performance_estimated: bool
    unrated_rows: int = 0
    total_planned: float
    total_operating: float
    total_units: float
    good_units: float
    row_count: int
    invalid_cells: int = 0
    blank_cells: int = 0
    rows_with_issues: int = 0
    trend: List[OeeTrendPoint] = []
    machines: List[OeeMachineResult] = []
    revenue_impact: Optional[RevenueImpact] = None
    teep: Optional[TeepResult] = None
    six_big_losses: Optional[SixBigLosses] = None


@router.post("/oee", response_model=OeeComputeResponse)
async def compute_oee(
    req: OeeComputeRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    dataset = await ds_service.resolve_dataset(db, current.org_id, req.dataset_id)
    # Only the four required columns are hard-validated; optional cols (date /
    # machine / six-loss splits) are ignored when absent, matching the desktop.
    ds_service.validate_columns(
        dataset,
        [req.planned_time_col, req.operating_time_col, req.total_units_col, req.good_units_col],
    )
    result = oee_service.compute_oee(dataset, req)
    return OeeComputeResponse(**result)
