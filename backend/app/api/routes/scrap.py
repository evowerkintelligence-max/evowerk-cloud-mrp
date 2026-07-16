from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.services import datasets as ds_service
from app.services import scrap as scrap_service

router = APIRouter(prefix="/analytics", tags=["analytics"])


class ScrapComputeRequest(BaseModel):
    dataset_id: str
    total_units_col: str = Field(..., min_length=1)
    scrap_col: str = Field(..., min_length=1)
    reason_col: Optional[str] = None
    machine_col: Optional[str] = None
    date_col: Optional[str] = None


class ScrapReasonResult(BaseModel):
    reason: str
    count: float
    pct_of_total: float
    cumulative_pct: float


class ScrapMachineResult(BaseModel):
    machine: str
    total_units: float
    scrap_units: float
    scrap_rate_pct: float


class ScrapTrendPoint(BaseModel):
    period: str
    total_units: float
    scrap_units: float
    scrap_rate_pct: float


class ScrapComputeResponse(BaseModel):
    total_units: float
    total_scrap: float
    scrap_rate_pct: float
    fpq_pct: float
    defect_rate_per_1000: float
    row_count: int
    by_reason: List[ScrapReasonResult] = []
    by_machine: List[ScrapMachineResult] = []
    trend: List[ScrapTrendPoint] = []
    warnings: List[str] = []


@router.post("/scrap", response_model=ScrapComputeResponse)
async def compute_scrap(
    req: ScrapComputeRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    dataset = await ds_service.resolve_dataset(db, current.org_id, req.dataset_id)
    ds_service.validate_columns(
        dataset, [req.total_units_col, req.scrap_col, req.reason_col, req.machine_col, req.date_col]
    )
    # Synchronous DuckDB aggregation — keep it off the event loop.
    result = await run_in_threadpool(scrap_service.compute_scrap, dataset, req)
    return ScrapComputeResponse(**result)
