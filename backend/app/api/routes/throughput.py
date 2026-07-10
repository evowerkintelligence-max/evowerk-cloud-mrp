from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.services import datasets as ds_service
from app.services import throughput as throughput_service

router = APIRouter(prefix="/analytics", tags=["analytics"])


class ThroughputComputeRequest(BaseModel):
    dataset_id: str
    units_col: str = Field(..., min_length=1)
    date_col: Optional[str] = None
    machine_col: Optional[str] = None
    planned_units_col: Optional[str] = None


class ThroughputTrendPoint(BaseModel):
    period: str
    units: float
    planned: Optional[float] = None
    efficiency_pct: Optional[float] = None


class ThroughputMachineResult(BaseModel):
    machine: str
    total_units: float
    pct_of_total: float


class ThroughputComputeResponse(BaseModel):
    total_units: float
    period_count: int
    avg_units_per_period: float
    peak_units: float
    peak_period: Optional[str] = None
    total_planned: Optional[float] = None
    efficiency_pct: Optional[float] = None
    row_count: int
    invalid_cells: int = 0
    rows_with_issues: int = 0
    trend: List[ThroughputTrendPoint] = []
    by_machine: List[ThroughputMachineResult] = []


@router.post("/throughput", response_model=ThroughputComputeResponse)
async def compute_throughput(
    req: ThroughputComputeRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    dataset = await ds_service.resolve_dataset(db, current.org_id, req.dataset_id)
    # Throughput validates every provided column (unlike OEE, which ignores
    # unknown optionals) — matches the desktop's stricter behavior.
    ds_service.validate_columns(
        dataset, [req.units_col, req.date_col, req.machine_col, req.planned_units_col]
    )
    return ThroughputComputeResponse(**throughput_service.compute_throughput(dataset, req))
