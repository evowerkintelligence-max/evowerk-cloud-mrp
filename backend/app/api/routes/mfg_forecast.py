from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.services import datasets as ds_service
from app.services import mfg_forecast as service

router = APIRouter(prefix="/datasets/{dataset_id}/mfg-forecast", tags=["mfg-forecast"])


class ForecastRequest(BaseModel):
    date_col: str = Field(..., min_length=1)
    target_col: str = Field(..., min_length=1)
    machine_col: str | None = None
    machine_filter: str | None = None
    horizon: int = Field(default=14, ge=1, le=90)
    aggregate: str = "mean"  # "mean" | "sum"


class ForecastPointResponse(BaseModel):
    date: str
    actual: float | None
    predicted: float
    lower: float
    upper: float


class PrescriptiveInsightResponse(BaseModel):
    rank: int
    label: str
    detail: str
    importance_pct: float
    direction: str
    icon: str


class TrackRecordPointResponse(BaseModel):
    date: str
    actual: float
    predicted: float
    lower: float
    upper: float
    made_at: str
    error_pct: float | None


class ForecastResponse(BaseModel):
    target_col: str
    horizon: int
    mape_pct: float
    r2: float
    n_training_points: int
    series: list[ForecastPointResponse]
    prescriptive: list[PrescriptiveInsightResponse]
    summary: str
    machines: list[str]
    gap_count: int
    outliers_clipped: int
    track_record: list[TrackRecordPointResponse]
    track_record_mape: float | None


@router.post("/run", response_model=ForecastResponse)
async def run_forecast(
    dataset_id: uuid.UUID,
    req: ForecastRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ForecastResponse:
    dataset = await ds_service.get_dataset_or_404(db, current.org_id, dataset_id)
    try:
        result = service.run_forecast(dataset, **req.model_dump())
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    actual_by_date = {p.date: p.actual for p in result.series if p.actual is not None}
    track_record = await service.get_track_record(db, current.org_id, req.target_col, req.machine_filter, actual_by_date)
    await service.save_snapshot(db, current.org_id, dataset_id, req.target_col, req.machine_filter, result.series)
    track_errors = [tr.error_pct for tr in track_record if tr.error_pct is not None]
    track_record_mape = round(sum(track_errors) / len(track_errors), 2) if track_errors else None

    return ForecastResponse(
        target_col=result.target_col, horizon=result.horizon, mape_pct=result.mape_pct, r2=result.r2,
        n_training_points=result.n_training_points,
        series=[ForecastPointResponse(**vars(p)) for p in result.series],
        prescriptive=[PrescriptiveInsightResponse(**vars(p)) for p in result.prescriptive],
        summary=result.summary, machines=result.machines,
        gap_count=result.gap_count, outliers_clipped=result.outliers_clipped,
        track_record=[TrackRecordPointResponse(**vars(p)) for p in track_record],
        track_record_mape=track_record_mape,
    )
