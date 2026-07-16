"""Quality analytics endpoint — defect rate/DPU, worst batches, feature
correlations, trend, clustering. Runs over an existing uploaded Dataset.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.services import datasets as ds_service
from app.services.quality_engine import analyze_quality

router = APIRouter(prefix="/datasets/{dataset_id}/quality", tags=["quality"])


class QualityRequest(BaseModel):
    defect_column: str = Field(..., description="Column with defect flag (0/1) or defect count")
    batch_column: Optional[str] = Field(default=None, description="Column with batch/lot ID")
    timestamp_column: Optional[str] = Field(default=None, description="Column with timestamp for trend analysis")
    feature_columns: Optional[List[str]] = Field(default=None, description="Process parameter columns to analyze")
    n_clusters: int = Field(default=3, ge=2, le=10)
    max_rows: int = Field(default=500_000, ge=100, le=5_000_000)


@router.post("/analyze")
async def quality_analyze(
    dataset_id: uuid.UUID,
    req: QualityRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    dataset = await ds_service.get_dataset_or_404(db, current.org_id, dataset_id)
    ds_service.validate_columns(
        dataset, [req.defect_column, req.batch_column, req.timestamp_column, *(req.feature_columns or [])]
    )

    try:
        # analyze_quality is synchronous pandas/sklearn over up to 5M rows;
        # run it off the event loop so one analysis doesn't stall the worker.
        return await run_in_threadpool(
            analyze_quality,
            dataset=dataset,
            defect_column=req.defect_column,
            batch_column=req.batch_column,
            timestamp_column=req.timestamp_column,
            feature_columns=req.feature_columns,
            n_clusters=req.n_clusters,
            max_rows=req.max_rows,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
