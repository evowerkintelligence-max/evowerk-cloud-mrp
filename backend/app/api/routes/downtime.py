from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.services import datasets as ds_service
from app.services import downtime as downtime_service

router = APIRouter(prefix="/analytics", tags=["analytics"])


class DowntimeAnalyzeRequest(BaseModel):
    dataset_id: str
    duration_col: str = Field(..., min_length=1)
    duration_unit: str = "minutes"  # "minutes" or "hours"
    category_col: Optional[str] = None
    machine_col: Optional[str] = None
    timestamp_col: Optional[str] = None


class DowntimeCategoryRow(BaseModel):
    category: str
    incidents: int
    total_minutes: float
    pct_of_total: float
    cumulative_pct: float
    invalid_cells: int = 0


class DowntimeMachineRow(BaseModel):
    machine: str
    incidents: int
    total_minutes: float
    mttr_minutes: float
    invalid_cells: int = 0


class DowntimeTrendPoint(BaseModel):
    date: str
    incidents: int
    total_minutes: float
    invalid_cells: int = 0


class DowntimeAnalyzeResponse(BaseModel):
    total_incidents: int
    total_minutes: float
    mttr_minutes: float
    top_category: Optional[str]
    invalid_cells: int = 0
    rows_with_issues: int = 0
    by_category: List[DowntimeCategoryRow] = []
    by_machine: List[DowntimeMachineRow] = []
    trend: List[DowntimeTrendPoint] = []


@router.post("/downtime", response_model=DowntimeAnalyzeResponse)
async def analyze_downtime(
    req: DowntimeAnalyzeRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    dataset = await ds_service.resolve_dataset(db, current.org_id, req.dataset_id)
    ds_service.validate_columns(dataset, [req.duration_col])
    return DowntimeAnalyzeResponse(**downtime_service.analyze_downtime(dataset, req))
