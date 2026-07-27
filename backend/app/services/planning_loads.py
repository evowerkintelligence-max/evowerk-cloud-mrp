"""Query loaders shared by app.services.mrp, app.services.work_orders and
app.services.planning_grid. A true leaf: it imports nothing from any of the
three, on purpose, so that all three can import from it at module level with
no risk of a cycle.

Before this module existed, mrp.py needed load_production_supply and
load_scheduled_component_demand (defined in work_orders.py) and
resolve_grid_window (defined in planning_grid.py); planning_grid.py needed
component_requirements/reserved_by_higher_priority (defined in
work_orders.py); and work_orders.py needed resolve_grid_window (defined in
planning_grid.py). None of those could be a normal top-of-file import
without inverting an edge that already existed the other way, so all three
were function-level imports deferred to call time - three live cycles held
apart only by never letting Python load them at the same moment. That made
it possible for the two engine entry points (compute_mrp_plan and
compute_planning_grid) to seed a scheduled job's supply and demand from two
different call sites that could silently drift apart, which is what
happened in C1 (a build reported short of materials needed nothing because
one engine path counted the job's supply without seeding its demand).
Pulling the shared primitives down into a module beneath all three makes
that particular drift structurally impossible: there is only one
load_production_supply and one load_scheduled_component_demand, and every
caller imports the same one.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.models.work_order import WorkOrder, WorkOrderComponent

# The grid never shows a horizon control - a non-technical user should not
# have to pick one. 90 days matches the engine default
# (services.mrp.resolve_horizon), so the grid and a raw /mrp/plan call agree.
GRID_HORIZON_DAYS = 90


def resolve_grid_window(as_of: date | None = None) -> tuple[date, date]:
    start = as_of if as_of is not None else datetime.now(timezone.utc).date()
    return start, start + timedelta(days=GRID_HORIZON_DAYS)


# Only these two statuses reach the plan. A done job has already delivered its
# units into stock and a cancelled one never will, so counting either would
# promise the shop something twice.
PLANNED_STATUSES = ("scheduled", "in_progress")

# An undated scheduled job outranks every dated one for stock (it could run
# any time, including right now), so it sorts first rather than last.
_UNDATED_RANKS_FIRST = date.min


def _rank() -> ColumnElement[date]:
    return func.coalesce(WorkOrder.scheduled_start, _UNDATED_RANKS_FIRST)


async def load_production_supply(
    db: AsyncSession, org_id: uuid.UUID, horizon_end: date
) -> dict[uuid.UUID, Decimal]:
    """Incoming supply per item from jobs in flight.

    A scheduled job counts only if it is due inside the window; a running job
    counts whatever its schedule said, because it is happening now. There is
    deliberately no lower bound on the scheduled case: a job that should have
    started last week is still going to happen, matching the rule Phase 2
    settled for late sales orders.
    """
    rows = (
        await db.execute(
            select(WorkOrder.item_id, func.sum(WorkOrder.quantity_planned))
            .where(
                WorkOrder.org_id == org_id,
                WorkOrder.status.in_(PLANNED_STATUSES),
                or_(WorkOrder.status == "in_progress", _rank() <= horizon_end),
            )
            .group_by(WorkOrder.item_id)
        )
    ).all()
    return {item_id: total for item_id, total in rows if total is not None and total > 0}


async def load_scheduled_component_demand(
    db: AsyncSession, org_id: uuid.UUID, horizon_end: date
) -> tuple[dict[uuid.UUID, Decimal], dict[uuid.UUID, date]]:
    """What jobs not yet started will need, and the earliest scheduled_start
    among the jobs that need each component.

    Only `scheduled` jobs: a running job's components have already left stock,
    so on_hand reflects them and counting them again would buy them twice. This
    must always move together with load_production_supply's scheduled half, or
    the plan under-orders components (supply counted, its demand dropped) or
    over-orders them (the reverse).

    An undated job (scheduled_start is None) contributes quantity but no date -
    func.min ignores NULLs, so a component reached only by undated jobs gets no
    date here either, same as a forecast/committed source with nothing to date
    against. Without this, a scheduled job's own components landed in to_buy
    with urgency "unknown" no matter how soon the job itself was scheduled.
    """
    rows = (
        await db.execute(
            select(
                WorkOrderComponent.item_id,
                func.sum(
                    WorkOrderComponent.quantity_required - WorkOrderComponent.quantity_consumed
                ),
                func.min(WorkOrder.scheduled_start),
            )
            .join(WorkOrder, WorkOrder.id == WorkOrderComponent.wo_id)
            .where(
                WorkOrder.org_id == org_id,
                WorkOrder.status == "scheduled",
                _rank() <= horizon_end,
            )
            .group_by(WorkOrderComponent.item_id)
        )
    ).all()
    quantities = {
        item_id: total for item_id, total, _ in rows if total is not None and total > 0
    }
    dates = {
        item_id: earliest
        for item_id, total, earliest in rows
        if total is not None and total > 0 and earliest is not None
    }
    return quantities, dates
