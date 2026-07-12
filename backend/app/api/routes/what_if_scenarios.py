from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.services import what_if_scenarios as service

router = APIRouter(prefix="/scenarios", tags=["scenarios"])


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
