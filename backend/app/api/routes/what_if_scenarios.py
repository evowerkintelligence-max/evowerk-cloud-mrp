from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.services import what_if_scenarios as service
from app.services.reports import Report, Section, render

router = APIRouter(prefix="/scenarios", tags=["scenarios"])

_EM_DASH = "—"
_RUN_EXPORT_FORMATS = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


class ScenarioCreate(BaseModel):
    name: str = Field(..., min_length=1)
    description: str = ""
    dataset_id: uuid.UUID
    base_query: dict
    parameters: dict = Field(default_factory=dict)


class ScenarioUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    base_query: dict | None = None
    parameters: dict | None = None


class ScenarioResponse(BaseModel):
    id: uuid.UUID
    dataset_id: uuid.UUID
    name: str
    description: str
    base_query: dict
    parameters: dict
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ScenarioExecuteRequest(BaseModel):
    param_sets: list[dict] = Field(default_factory=list)


class ScenarioRunResultResponse(BaseModel):
    params: dict
    result: dict


class ScenarioRunResponse(BaseModel):
    id: uuid.UUID
    params: dict
    result: dict
    created_at: datetime

    model_config = {"from_attributes": True}


class ScenarioCompareRequest(BaseModel):
    scenario_a_id: uuid.UUID
    params_a: dict = Field(default_factory=dict)
    scenario_b_id: uuid.UUID
    params_b: dict = Field(default_factory=dict)
    metric: str = Field(..., min_length=1)


class ScenarioCompareResponse(BaseModel):
    metric: str
    value_a: float | None
    value_b: float | None
    delta: float | None
    pct_change: float | None


def _wrap_not_found(exc: ValueError) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))


@router.get("", response_model=list[ScenarioResponse])
async def list_scenarios(current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await service.list_scenarios(db, current.org_id)


@router.get("/{scenario_id}", response_model=ScenarioResponse)
async def get_scenario(scenario_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        return await service.get_scenario_or_404(db, current.org_id, scenario_id)
    except ValueError as exc:
        raise _wrap_not_found(exc) from exc


@router.post("", response_model=ScenarioResponse, status_code=status.HTTP_201_CREATED)
async def create_scenario(payload: ScenarioCreate, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        return await service.create_scenario(db, current.org_id, current.id, **payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.put("/{scenario_id}", response_model=ScenarioResponse)
async def update_scenario(scenario_id: uuid.UUID, payload: ScenarioUpdate, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    patch = payload.model_dump(exclude_unset=True)
    try:
        return await service.update_scenario(db, current.org_id, scenario_id, patch)
    except ValueError as exc:
        raise _wrap_not_found(exc) from exc


@router.delete("/{scenario_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_scenario(scenario_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        await service.delete_scenario(db, current.org_id, scenario_id)
    except ValueError as exc:
        raise _wrap_not_found(exc) from exc


@router.post("/{scenario_id}/execute", response_model=list[ScenarioRunResultResponse])
async def execute_scenario(scenario_id: uuid.UUID, req: ScenarioExecuteRequest, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        scenario = await service.get_scenario_or_404(db, current.org_id, scenario_id)
    except ValueError as exc:
        raise _wrap_not_found(exc) from exc
    try:
        runs = await service.execute_scenario(db, current.org_id, current.id, scenario, req.param_sets)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return [ScenarioRunResultResponse(params=r.params, result=r.result) for r in runs]


@router.get("/{scenario_id}/runs", response_model=list[ScenarioRunResponse])
async def list_scenario_runs(
    scenario_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    try:
        await service.get_scenario_or_404(db, current.org_id, scenario_id)
    except ValueError as exc:
        raise _wrap_not_found(exc) from exc
    return await service.list_scenario_runs(db, current.org_id, scenario_id)


@router.get("/{scenario_id}/runs/{run_id}/export")
async def export_scenario_run(
    scenario_id: uuid.UUID,
    run_id: uuid.UUID,
    fmt: str = Query("csv", description="csv | xlsx"),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Download a past run's result table - ported from the desktop app's
    exports.py export_scenario_run, built on this app's Report/Section
    renderers instead of a bespoke writer."""
    if fmt not in _RUN_EXPORT_FORMATS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"fmt must be one of {sorted(_RUN_EXPORT_FORMATS)}")
    try:
        run = await service.get_scenario_run_or_404(db, current.org_id, scenario_id, run_id)
    except ValueError as exc:
        raise _wrap_not_found(exc) from exc

    columns = run.result.get("columns", [])
    rows = [[_EM_DASH if r.get(c) is None else str(r.get(c)) for c in columns] for r in run.result.get("rows", [])]
    report = Report(
        slug="scenario-run",
        title=f"Scenario run {run_id}",
        subtitle=f"{len(rows)} row(s)",
        org_name="",
        generated_at=datetime.now(timezone.utc),
        sections=[Section(title="Result", columns=columns, rows=rows)],
    )
    filename = report.filename(fmt)
    return Response(
        content=render(report, fmt),
        media_type=_RUN_EXPORT_FORMATS[fmt],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/compare", response_model=ScenarioCompareResponse)
async def compare_scenarios(req: ScenarioCompareRequest, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        scenario_a = await service.get_scenario_or_404(db, current.org_id, req.scenario_a_id)
        scenario_b = await service.get_scenario_or_404(db, current.org_id, req.scenario_b_id)
    except ValueError as exc:
        raise _wrap_not_found(exc) from exc
    try:
        result = await service.compare_scenarios(db, current.org_id, scenario_a, req.params_a, scenario_b, req.params_b, req.metric)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return ScenarioCompareResponse(**vars(result))
