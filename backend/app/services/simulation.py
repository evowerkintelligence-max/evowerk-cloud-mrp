from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.simulation import Simulation, SimulationRun
from app.services import datasets as ds_service
from app.services.simulation_engine import compare_snapshots, run_whatif


async def get_simulation_or_404(db: AsyncSession, org_id: uuid.UUID, simulation_id: uuid.UUID) -> Simulation:
    result = await db.execute(select(Simulation).where(Simulation.org_id == org_id, Simulation.id == simulation_id))
    simulation = result.scalar_one_or_none()
    if simulation is None:
        raise ValueError("Simulation not found")
    return simulation


async def list_simulations(db: AsyncSession, org_id: uuid.UUID) -> list[Simulation]:
    result = await db.execute(
        select(Simulation).where(Simulation.org_id == org_id).order_by(Simulation.created_at.desc())
    )
    return list(result.scalars().all())


async def create_simulation(
    db: AsyncSession, org_id: uuid.UUID, created_by: uuid.UUID,
    *, name: str, description: str, dataset_id: uuid.UUID | None, baseline: dict, changes: dict,
) -> Simulation:
    if dataset_id is not None:
        await ds_service.get_dataset_or_404(db, org_id, dataset_id)
    simulation = Simulation(
        org_id=org_id, dataset_id=dataset_id, name=name, description=description,
        baseline=baseline or {}, changes=changes or {}, created_by=created_by,
    )
    db.add(simulation)
    await db.commit()
    await db.refresh(simulation)
    return simulation


# NOT NULL columns patchable via SimulationUpdate that have a sensible
# "cleared" value - an explicit null in the patch means "clear it", not
# "store NULL" (which would fail the commit). name has no safe clear value
# (required non-empty on create), so an explicit null for it is a validation
# error instead.
_CLEARABLE_FIELDS = {"baseline": dict, "changes": dict, "description": str}


class InvalidPatchError(ValueError):
    """Raised when a patch value can't be applied to a NOT NULL column."""


async def update_simulation(db: AsyncSession, org_id: uuid.UUID, simulation_id: uuid.UUID, patch: dict) -> Simulation:
    simulation = await get_simulation_or_404(db, org_id, simulation_id)
    for key, value in patch.items():
        if value is None:
            if key in _CLEARABLE_FIELDS:
                value = _CLEARABLE_FIELDS[key]()
            else:
                raise InvalidPatchError(f"{key} cannot be null")
        setattr(simulation, key, value)
    await db.commit()
    await db.refresh(simulation)
    return simulation


async def delete_simulation(db: AsyncSession, org_id: uuid.UUID, simulation_id: uuid.UUID) -> None:
    simulation = await get_simulation_or_404(db, org_id, simulation_id)
    await db.delete(simulation)
    await db.commit()


async def execute_simulation(
    db: AsyncSession, org_id: uuid.UUID, created_by: uuid.UUID, simulation: Simulation, changes_override: dict,
) -> SimulationRun:
    merged_changes = {**(simulation.changes or {}), **(changes_override or {})}
    result = run_whatif(scenario_name=simulation.name, baseline=simulation.baseline, changes=merged_changes)
    run = SimulationRun(
        org_id=org_id, simulation_id=simulation.id, changes=merged_changes, result=result, created_by=created_by
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def list_simulation_runs(db: AsyncSession, org_id: uuid.UUID, simulation_id: uuid.UUID) -> list[SimulationRun]:
    result = await db.execute(
        select(SimulationRun)
        .where(SimulationRun.org_id == org_id, SimulationRun.simulation_id == simulation_id)
        .order_by(SimulationRun.created_at.desc())
    )
    return list(result.scalars().all())


async def get_simulation_run_or_404(
    db: AsyncSession, org_id: uuid.UUID, simulation_id: uuid.UUID, run_id: uuid.UUID
) -> SimulationRun:
    result = await db.execute(
        select(SimulationRun).where(
            SimulationRun.org_id == org_id, SimulationRun.simulation_id == simulation_id, SimulationRun.id == run_id
        )
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise ValueError("Simulation run not found")
    return run


async def get_run_or_404(db: AsyncSession, org_id: uuid.UUID, run_id: uuid.UUID) -> SimulationRun:
    result = await db.execute(select(SimulationRun).where(SimulationRun.org_id == org_id, SimulationRun.id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        raise ValueError("Simulation run not found")
    return run


class RunsNotComparableError(ValueError):
    """Raised when two runs don't belong to the same simulation."""


async def compare_simulation_runs(
    db: AsyncSession, org_id: uuid.UUID, run_a_id: uuid.UUID, run_b_id: uuid.UUID
) -> dict[str, dict]:
    run_a = await get_run_or_404(db, org_id, run_a_id)
    run_b = await get_run_or_404(db, org_id, run_b_id)
    if run_a.simulation_id != run_b.simulation_id:
        raise RunsNotComparableError("Runs must belong to the same simulation")
    return compare_snapshots(run_a.result["projected"], run_b.result["projected"])
