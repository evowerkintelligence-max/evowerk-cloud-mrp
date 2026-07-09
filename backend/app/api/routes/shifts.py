from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.services import datasets as ds_service
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


class ShiftAnalyzeResponse(BaseModel):
    shifts: List[ShiftResult]
    best_shift: Optional[str] = None
    worst_shift: Optional[str] = None
    metrics_available: List[str] = []
    row_count: int
    invalid_cells: int = 0
    rows_with_issues: int = 0


@router.post("/shifts", response_model=ShiftAnalyzeResponse)
async def analyze_shifts(
    req: ShiftAnalyzeRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    dataset = await ds_service.resolve_dataset(db, current.org_id, req.dataset_id)
    # Only the shift column is required; metric columns are ignored when absent
    # (the service raises 400 if no metric family is mappable).
    ds_service.validate_columns(dataset, [req.shift_col])
    return ShiftAnalyzeResponse(**shifts_service.analyze_shifts(dataset, req))
