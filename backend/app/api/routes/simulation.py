from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.services import datasets as ds_service
from app.services.simulation_engine import derive_baseline_from_rows, run_whatif

router = APIRouter(prefix="/simulation", tags=["simulation"])


class SimulationRequest(BaseModel):
    scenario_name: str = Field(default="What-if Scenario")
    baseline: dict[str, Any] = Field(default_factory=dict)
    changes: dict[str, Any] = Field(default_factory=dict)


class BaselineRequest(BaseModel):
    dataset_id: uuid.UUID
    mapping: dict[str, str | None] | None = None
    cost_overrides: dict[str, Any] = Field(default_factory=dict)


@router.post("/run")
def simulation_run(
    req: SimulationRequest,
    current: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Apply `changes` to `baseline` manufacturing parameters and return
    projected outcomes with human-readable insights. Nothing is persisted."""
    return run_whatif(scenario_name=req.scenario_name, baseline=req.baseline, changes=req.changes)


@router.post("/baseline")
async def simulation_baseline(
    req: BaselineRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Auto-derive a ready-to-simulate baseline from an uploaded dataset's
    actual rows (OEE, defect rate, throughput/shift, shifts/day) so the
    what-if simulator opens already knowing the plant."""
    dataset = await ds_service.get_dataset_or_404(db, current.org_id, req.dataset_id)
    df = ds_service.dataframe_for(dataset)
    rows = df.to_dict("records")
    if not rows:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Dataset has no rows to derive a baseline from.")
    try:
        out = derive_baseline_from_rows(
            headers=list(df.columns), rows=rows, mapping=req.mapping, cost_overrides=req.cost_overrides
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    out["dataset_id"] = str(dataset.id)
    return out
