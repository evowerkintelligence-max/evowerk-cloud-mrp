"""What-if scenarios: a saved DatasetQueryRequest (see app.services.dataset_query)
with default parameter overrides, re-run on demand and diffable against another
scenario/param-set pair. Ported from the desktop app's scenarios.py, adapted
onto the cloud app's ad-hoc dataset query engine instead of the desktop's
Runs/query-engine system (no direct cloud equivalent).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.scenario import Scenario, ScenarioRun
from app.services import datasets as ds_service
from app.services.dataset_query import DatasetQueryRequest, run_query


def _merge_params(base: dict, params: dict) -> dict:
    merged = dict(base or {})
    merged.update(params or {})
    return merged


async def get_scenario_or_404(db: AsyncSession, org_id: uuid.UUID, scenario_id: uuid.UUID) -> Scenario:
    result = await db.execute(select(Scenario).where(Scenario.org_id == org_id, Scenario.id == scenario_id))
    scenario = result.scalar_one_or_none()
    if scenario is None:
        raise ValueError("Scenario not found")
    return scenario


async def list_scenarios(db: AsyncSession, org_id: uuid.UUID) -> list[Scenario]:
    result = await db.execute(select(Scenario).where(Scenario.org_id == org_id).order_by(Scenario.created_at.desc()))
    return list(result.scalars().all())


async def create_scenario(
    db: AsyncSession, org_id: uuid.UUID, created_by: uuid.UUID,
    *, name: str, description: str, dataset_id: uuid.UUID, base_query: dict, parameters: dict,
) -> Scenario:
    dataset = await ds_service.get_dataset_or_404(db, org_id, dataset_id)
    # Fail fast on an invalid base_query rather than at first execute.
    DatasetQueryRequest.model_validate(base_query)
    scenario = Scenario(
        org_id=org_id, dataset_id=dataset.id, name=name, description=description,
        base_query=base_query, parameters=parameters or {}, created_by=created_by,
    )
    db.add(scenario)
    await db.commit()
    await db.refresh(scenario)
    return scenario


async def update_scenario(db: AsyncSession, org_id: uuid.UUID, scenario_id: uuid.UUID, patch: dict) -> Scenario:
    scenario = await get_scenario_or_404(db, org_id, scenario_id)
    if "base_query" in patch:
        DatasetQueryRequest.model_validate(patch["base_query"])
    for key, value in patch.items():
        setattr(scenario, key, value)
    await db.commit()
    await db.refresh(scenario)
    return scenario


async def delete_scenario(db: AsyncSession, org_id: uuid.UUID, scenario_id: uuid.UUID) -> None:
    scenario = await get_scenario_or_404(db, org_id, scenario_id)
    await db.delete(scenario)
    await db.commit()


@dataclass
class ScenarioRunResult:
    params: dict
    result: dict


async def execute_scenario(
    db: AsyncSession, org_id: uuid.UUID, created_by: uuid.UUID, scenario: Scenario, param_sets: list[dict],
) -> list[ScenarioRunResult]:
    dataset = await ds_service.get_dataset_or_404(db, org_id, scenario.dataset_id)
    out: list[ScenarioRunResult] = []
    for params in param_sets or [{}]:
        merged = _merge_params(scenario.base_query, params)
        req = DatasetQueryRequest.model_validate(merged)
        result = await run_query(db, dataset, org_id, req)
        db.add(ScenarioRun(org_id=org_id, scenario_id=scenario.id, params=params or {}, result=result, created_by=created_by))
        out.append(ScenarioRunResult(params=params or {}, result=result))
    await db.commit()
    return out


@dataclass
class ScenarioCompareResult:
    metric: str
    value_a: float | None
    value_b: float | None
    delta: float | None
    pct_change: float | None


async def compare_scenarios(
    db: AsyncSession, org_id: uuid.UUID,
    scenario_a: Scenario, params_a: dict, scenario_b: Scenario, params_b: dict, metric: str,
) -> ScenarioCompareResult:
    """Runs both scenarios' merged queries and diffs a single named output
    column (an alias in their `select`) taken from each result's first row."""
    dataset_a = await ds_service.get_dataset_or_404(db, org_id, scenario_a.dataset_id)
    dataset_b = await ds_service.get_dataset_or_404(db, org_id, scenario_b.dataset_id)

    req_a = DatasetQueryRequest.model_validate(_merge_params(scenario_a.base_query, params_a))
    req_b = DatasetQueryRequest.model_validate(_merge_params(scenario_b.base_query, params_b))

    result_a = await run_query(db, dataset_a, org_id, req_a)
    result_b = await run_query(db, dataset_b, org_id, req_b)

    def _extract(result: dict) -> float | None:
        if not result["rows"]:
            return None
        value = result["rows"][0].get(metric)
        return float(value) if isinstance(value, (int, float)) else None

    value_a, value_b = _extract(result_a), _extract(result_b)
    delta = (value_b - value_a) if (value_a is not None and value_b is not None) else None
    pct_change = (delta / value_a * 100.0) if (delta is not None and value_a not in (None, 0)) else None

    return ScenarioCompareResult(metric=metric, value_a=value_a, value_b=value_b, delta=delta, pct_change=pct_change)
