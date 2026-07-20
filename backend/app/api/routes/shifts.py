from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.services import datasets as ds_service
from app.services import shift_schedule as schedule_service
from app.services import shifts as shifts_service

router = APIRouter(prefix="/analytics", tags=["analytics"])


class ShiftAnalyzeRequest(BaseModel):
    dataset_id: str
    shift_col: str = Field(..., min_length=1)
    units_col: Optional[str] = None
    planned_col: Optional[str] = None
    operating_col: Optional[str] = None
    good_units_col: Optional[str] = None
    total_units_col: Optional[str] = None
    scrap_col: Optional[str] = None
    target_rate: Optional[float] = None
    downtime_col: Optional[str] = None
    downtime_unit: str = "minutes"
    downtime_category_col: Optional[str] = None
    machine_col: Optional[str] = None
    timestamp_col: Optional[str] = None


class DowntimeCategoryBreakdown(BaseModel):
    category: str
    total_minutes: float
    pct: float


class ShiftResult(BaseModel):
    shift: str
    row_count: int
    total_units: Optional[float] = None
    total_scrap: Optional[float] = None
    scrap_rate_pct: Optional[float] = None
    fpq_pct: Optional[float] = None
    availability_pct: Optional[float] = None
    quality_pct: Optional[float] = None
    oee_pct: Optional[float] = None
    total_downtime_min: Optional[float] = None
    downtime_by_category: List[DowntimeCategoryBreakdown] = []


class ShiftMachineResult(BaseModel):
    shift: str
    machine: str
    total_units: Optional[float] = None
    total_scrap: Optional[float] = None
    scrap_rate_pct: Optional[float] = None
    fpq_pct: Optional[float] = None
    availability_pct: Optional[float] = None
    quality_pct: Optional[float] = None
    oee_pct: Optional[float] = None
    total_downtime_min: Optional[float] = None


class ShiftTrendPoint(BaseModel):
    period: str
    shift: str
    value: Optional[float] = None


class ShiftHourlyPoint(BaseModel):
    shift: str
    hour: str
    units: float


class ShiftAnalyzeResponse(BaseModel):
    shifts: List[ShiftResult]
    best_shift: Optional[str] = None
    worst_shift: Optional[str] = None
    metrics_available: List[str] = []
    row_count: int
    invalid_cells: int = 0
    rows_with_issues: int = 0
    unparsed_date_rows: int = 0
    by_machine: List[ShiftMachineResult] = []
    trend: List[ShiftTrendPoint] = []
    hourly: List[ShiftHourlyPoint] = []
    target_rate: Optional[float] = None


@router.post("/shifts", response_model=ShiftAnalyzeResponse)
async def analyze_shifts(
    req: ShiftAnalyzeRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    dataset = await ds_service.resolve_dataset(db, current.org_id, req.dataset_id)
    # Only the shift column is required; metric columns are ignored when absent
    # (the service raises 400 if no metric family is mappable).
    ds_service.validate_columns(
        dataset, [req.shift_col, req.machine_col, req.timestamp_col]
    )
    return ShiftAnalyzeResponse(**shifts_service.analyze_shifts(dataset, req))


class ShiftForecastSourceRequest(ShiftAnalyzeRequest):
    shift: str = Field(..., min_length=1)


class ShiftForecastSourceResponse(BaseModel):
    dataset_id: str
    target_column: str
    time_column: str
    metric_label: str
    rows: int


@router.post("/shifts/forecast-source", response_model=ShiftForecastSourceResponse)
async def shift_forecast_source(
    req: ShiftForecastSourceRequest,
    background_tasks: BackgroundTasks,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    dataset = await ds_service.resolve_dataset(db, current.org_id, req.dataset_id)
    ds_service.validate_columns(dataset, [req.shift_col, req.machine_col, req.timestamp_col])
    result = await shifts_service.prepare_forecast_source(
        db, dataset, req, shift=req.shift, org_id=current.org_id, created_by=current.id,
        background_tasks=background_tasks,
    )
    await db.commit()
    return ShiftForecastSourceResponse(
        dataset_id=str(result["dataset_id"]),
        target_column=result["target_column"],
        time_column=result["time_column"],
        metric_label=result["metric_label"],
        rows=result["rows"],
    )


class ShiftScheduleItem(BaseModel):
    shift_label: str = Field(..., min_length=1)
    start_time: str = Field(..., pattern=r"^\d{2}:\d{2}$")
    end_time: str = Field(..., pattern=r"^\d{2}:\d{2}$")


@router.get("/shifts/schedule", response_model=List[ShiftScheduleItem])
async def get_shift_schedule(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = await schedule_service.list_schedules(db, current.org_id)
    return [ShiftScheduleItem(shift_label=r.shift_label, start_time=r.start_time, end_time=r.end_time) for r in rows]


@router.put("/shifts/schedule", response_model=List[ShiftScheduleItem])
async def put_shift_schedule(
    items: List[ShiftScheduleItem],
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = await schedule_service.replace_schedules(db, current.org_id, [i.model_dump() for i in items])
    return [ShiftScheduleItem(shift_label=r.shift_label, start_time=r.start_time, end_time=r.end_time) for r in rows]
