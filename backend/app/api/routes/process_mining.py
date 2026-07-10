"""Process mining endpoint — discovers the actual process flow from an event
log (case/activity/timestamp columns) in an existing uploaded Dataset.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.services import datasets as ds_service
from app.services.process_mining import analyze_process

router = APIRouter(prefix="/datasets/{dataset_id}/process-mining", tags=["process-mining"])


class ProcessMiningRequest(BaseModel):
    case_column: str = Field(..., description="Column that identifies each case/job/order")
    activity_column: str = Field(..., description="Column with the activity/step name")
    timestamp_column: str = Field(..., description="Column with the event timestamp")
    max_rows: int = Field(default=500_000, ge=1000, le=5_000_000)
    reference_sequence: Optional[List[str]] = Field(
        default=None,
        description="Explicit expected activity order for conformance checking. "
                     "Omit to auto-derive it as the most common full trace (majority path).",
    )


@router.post("/analyze")
async def process_mining_analyze(
    dataset_id: uuid.UUID,
    req: ProcessMiningRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    dataset = await ds_service.get_dataset_or_404(db, current.org_id, dataset_id)
    ds_service.validate_columns(dataset, [req.case_column, req.activity_column, req.timestamp_column])

    try:
        return analyze_process(
            dataset=dataset,
            case_column=req.case_column,
            activity_column=req.activity_column,
            timestamp_column=req.timestamp_column,
            max_rows=req.max_rows,
            reference_sequence=req.reference_sequence,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
