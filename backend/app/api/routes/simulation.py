from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.services import datasets as ds_service
from app.services import simulation as service
from app.services.reports import Report, Section, render
from app.services.simulation_engine import derive_baseline_from_rows, run_whatif

router = APIRouter(prefix="/simulation", tags=["simulation"])

_RUN_EXPORT_FORMATS = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


class SimulationRequest(BaseModel):
    scenario_name: str = Field(default="What-if Scenario")
    baseline: dict[str, Any] = Field(default_factory=dict)
    changes: dict[str, Any] = Field(default_factory=dict)


class BaselineRequest(BaseModel):
    dataset_id: uuid.UUID
    mapping: dict[str, str | None] | None = None
    cost_overrides: dict[str, Any] = Field(default_factory=dict)


class SimulationCreate(BaseModel):
    name: str = Field(..., min_length=1)
    description: str = ""
    dataset_id: uuid.UUID | None = None
    baseline: dict[str, Any] = Field(default_factory=dict)
    changes: dict[str, Any] = Field(default_factory=dict)


class SimulationUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    baseline: dict[str, Any] | None = None
    changes: dict[str, Any] | None = None


class SimulationResponse(BaseModel):
    id: uuid.UUID
    dataset_id: uuid.UUID | None
    name: str
    description: str
    baseline: dict[str, Any]
    changes: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SimulationRunRequest(BaseModel):
    changes: dict[str, Any] = Field(default_factory=dict)


class SimulationRunResponse(BaseModel):
    id: uuid.UUID
    changes: dict[str, Any]
    result: dict[str, Any]
    created_at: datetime

    model_config = {"from_attributes": True}


class SimulationCompareRequest(BaseModel):
    run_a_id: uuid.UUID
    run_b_id: uuid.UUID


class SimulationCompareResponse(BaseModel):
    run_a_id: uuid.UUID
    run_b_id: uuid.UUID
    delta: dict[str, dict[str, Any]]


def _wrap_not_found(exc: ValueError) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))


@router.post("/run")
def simulation_run(req: SimulationRequest, current: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    """Apply `changes` to `baseline` manufacturing parameters and return
    projected outcomes with human-readable insights. Nothing is persisted -
    for a saved, re-runnable version see POST /simulation then
    POST /simulation/{id}/run."""
    return run_whatif(scenario_name=req.scenario_name, baseline=req.baseline, changes=req.changes)


@router.post("/baseline")
async def simulation_baseline(
    req: BaselineRequest, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
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


@router.get("", response_model=list[SimulationResponse])
async def list_simulations(current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await service.list_simulations(db, current.org_id)


@router.post("", response_model=SimulationResponse, status_code=status.HTTP_201_CREATED)
async def create_simulation(
    payload: SimulationCreate, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    return await service.create_simulation(db, current.org_id, current.id, **payload.model_dump())


@router.get("/{simulation_id}", response_model=SimulationResponse)
async def get_simulation(
    simulation_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    try:
        return await service.get_simulation_or_404(db, current.org_id, simulation_id)
    except ValueError as exc:
        raise _wrap_not_found(exc) from exc


@router.put("/{simulation_id}", response_model=SimulationResponse)
async def update_simulation(
    simulation_id: uuid.UUID,
    payload: SimulationUpdate,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    patch = payload.model_dump(exclude_unset=True)
    try:
        return await service.update_simulation(db, current.org_id, simulation_id, patch)
    except ValueError as exc:
        raise _wrap_not_found(exc) from exc


@router.delete("/{simulation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_simulation(
    simulation_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    try:
        await service.delete_simulation(db, current.org_id, simulation_id)
    except ValueError as exc:
        raise _wrap_not_found(exc) from exc


@router.post("/{simulation_id}/run", response_model=SimulationRunResponse, status_code=status.HTTP_201_CREATED)
async def run_simulation(
    simulation_id: uuid.UUID,
    req: SimulationRunRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Re-run a saved simulation's baseline with `changes` merged on top of
    its stored defaults, and persist the result as a SimulationRun."""
    try:
        simulation = await service.get_simulation_or_404(db, current.org_id, simulation_id)
    except ValueError as exc:
        raise _wrap_not_found(exc) from exc
    return await service.execute_simulation(db, current.org_id, current.id, simulation, req.changes)


@router.get("/{simulation_id}/runs", response_model=list[SimulationRunResponse])
async def list_simulation_runs(
    simulation_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    try:
        await service.get_simulation_or_404(db, current.org_id, simulation_id)
    except ValueError as exc:
        raise _wrap_not_found(exc) from exc
    return await service.list_simulation_runs(db, current.org_id, simulation_id)


@router.get("/{simulation_id}/runs/{run_id}/export")
async def export_simulation_run(
    simulation_id: uuid.UUID,
    run_id: uuid.UUID,
    fmt: str = Query("csv", description="csv | xlsx"),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Download a past run's baseline/projected/delta table - built on this
    app's Report/Section renderers, same pattern as every other export."""
    if fmt not in _RUN_EXPORT_FORMATS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"fmt must be one of {sorted(_RUN_EXPORT_FORMATS)}")
    try:
        run = await service.get_simulation_run_or_404(db, current.org_id, simulation_id, run_id)
    except ValueError as exc:
        raise _wrap_not_found(exc) from exc

    result = run.result
    metrics = sorted(result["baseline"].keys())
    columns = ["Metric", "Baseline", "Projected", "Δ Absolute", "Δ %"]
    rows = [
        [
            m,
            str(result["baseline"][m]),
            str(result["projected"][m]),
            str(result["delta"][m]["absolute"]),
            f"{result['delta'][m]['pct']}%",
        ]
        for m in metrics
    ]
    report = Report(
        slug="simulation-run",
        title=f"Simulation run — {result.get('scenario_name', '')}",
        subtitle=f"{len(metrics)} metric(s)",
        org_name="",
        generated_at=datetime.now(timezone.utc),
        sections=[Section(title="Result", columns=columns, rows=rows)],
        notes=result.get("insights", []),
    )
    filename = report.filename(fmt)
    return Response(
        content=render(report, fmt),
        media_type=_RUN_EXPORT_FORMATS[fmt],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/compare", response_model=SimulationCompareResponse)
async def compare_simulations(
    req: SimulationCompareRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        delta = await service.compare_simulation_runs(db, current.org_id, req.run_a_id, req.run_b_id)
    except ValueError as exc:
        raise _wrap_not_found(exc) from exc
    return SimulationCompareResponse(run_a_id=req.run_a_id, run_b_id=req.run_b_id, delta=delta)
