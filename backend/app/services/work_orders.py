from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from statistics import median

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.core.time import ensure_utc
from app.models.bom import Bom, Item
from app.models.routing import Routing, RoutingStep
from app.models.work_order import WorkOrder, WorkOrderComponent
from app.services.mrp import load_bom_children
from app.services.planning_grid import resolve_grid_window

# A job with no scheduled_start is due now, so it outranks every dated job.
# date.min as the sort key expresses that without a second branch in SQL.
_UNDATED_RANKS_FIRST = date.min


async def active_bom_id(db: AsyncSession, org_id: uuid.UUID, item_id: uuid.UUID) -> uuid.UUID | None:
    """The item's currently active recipe, or None if it has none. A job
    snapshots this so a later BOM edit cannot rewrite what it committed to."""
    return await db.scalar(
        select(Bom.id).where(Bom.org_id == org_id, Bom.item_id == item_id, Bom.status == "active")
    )


async def component_requirements(
    db: AsyncSession, org_id: uuid.UUID, item_id: uuid.UUID, quantity: Decimal
) -> list[tuple[uuid.UUID, Decimal]]:
    """What a job for `quantity` of `item_id` needs, one entry per direct
    component.

    Deliberately ONE level deep, not a full explosion: a work order consumes
    what its own recipe lists. If a sub-assembly is itself manufactured, it
    is consumed as a part and gets its own job; exploding through it here
    would consume raw material the shop never touched for this job.

    load_bom_children already folds each line's scrap_pct into the per-unit
    figure, so this must not apply scrap a second time.
    """
    children = await load_bom_children(db, org_id)
    return [(child_id, qty_per * quantity) for child_id, qty_per in children.get(item_id, [])]


def _rank() -> ColumnElement[date]:
    return func.coalesce(WorkOrder.scheduled_start, _UNDATED_RANKS_FIRST)


def _outranks(org_id: uuid.UUID, job: WorkOrder, horizon_end: date) -> list[ColumnElement[bool]]:
    """Scheduled jobs at the same location whose claim on stock comes ahead of
    `job`. Shared by the reservation total and by the refusal message's lookup
    of who is holding the material, so the two can never disagree about who
    outranks whom.
    """
    rank = _rank()
    job_rank = job.scheduled_start or _UNDATED_RANKS_FIRST
    return [
        WorkOrder.org_id == org_id,
        WorkOrder.status == "scheduled",
        WorkOrder.id != job.id,
        WorkOrder.location_id == job.location_id,
        rank <= horizon_end,
        # Outranks this job: due sooner, or due the same day and raised
        # earlier. Spelled out rather than as a tuple comparison, which
        # SQLAlchemy does not render portably to SQLite.
        or_(rank < job_rank, and_(rank == job_rank, WorkOrder.wo_number < job.wo_number)),
    ]


async def reserved_by_higher_priority(
    db: AsyncSession, org_id: uuid.UUID, job: WorkOrder
) -> dict[uuid.UUID, Decimal]:
    """Component quantity per item held by scheduled jobs that outrank `job`.

    Ranking is by scheduled_start, earliest first, with wo_number breaking
    ties so the order is total and stable. Whoever clicks first decides
    nothing, which is what makes this a priority rule rather than a race.

    Only jobs inside the planning horizon hold stock, the same bound the
    engine applies. A job scheduled for next quarter is intent, not a claim
    on today's shelf, and letting it block this week's work would be the
    worst of both worlds since it is not even in the plan being looked at.
    """
    _, horizon_end = resolve_grid_window()
    rows = (
        await db.execute(
            select(
                WorkOrderComponent.item_id,
                func.sum(
                    WorkOrderComponent.quantity_required - WorkOrderComponent.quantity_consumed
                ),
            )
            .join(WorkOrder, WorkOrder.id == WorkOrderComponent.wo_id)
            .where(*_outranks(org_id, job, horizon_end))
            .group_by(WorkOrderComponent.item_id)
        )
    ).all()
    return {item_id: total for item_id, total in rows if total is not None and total > 0}


# Only these two statuses reach the plan. A done job has already delivered its
# units into stock and a cancelled one never will, so counting either would
# promise the shop something twice.
PLANNED_STATUSES = ("scheduled", "in_progress")


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
) -> dict[uuid.UUID, Decimal]:
    """What jobs not yet started will need.

    Only `scheduled` jobs: a running job's components have already left stock,
    so on_hand reflects them and counting them again would buy them twice. This
    must always move together with load_production_supply's scheduled half, or
    the plan under-orders components (supply counted, its demand dropped) or
    over-orders them (the reverse).
    """
    rows = (
        await db.execute(
            select(
                WorkOrderComponent.item_id,
                func.sum(
                    WorkOrderComponent.quantity_required - WorkOrderComponent.quantity_consumed
                ),
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
    return {item_id: total for item_id, total in rows if total is not None and total > 0}


async def blocking_job(
    db: AsyncSession, org_id: uuid.UUID, job: WorkOrder, item_id: uuid.UUID
) -> WorkOrder | None:
    """The most urgent job holding `item_id` ahead of `job`, so a refusal can
    say who took the stock rather than only that it is gone. Called on the
    refusal path only, so the happy path pays nothing for it.
    """
    _, horizon_end = resolve_grid_window()
    return await db.scalar(
        select(WorkOrder)
        .join(WorkOrderComponent, WorkOrderComponent.wo_id == WorkOrder.id)
        .where(
            *_outranks(org_id, job, horizon_end),
            WorkOrderComponent.item_id == item_id,
            WorkOrderComponent.quantity_required > WorkOrderComponent.quantity_consumed,
        )
        .order_by(_rank(), WorkOrder.wo_number)
        .limit(1)
    )


# Used only by a shop with neither routing times nor one completed job of an
# item. It stops being used the moment the shop has told the app anything
# better, which is what makes it a cold start rather than a policy.
DEFAULT_JOB_DAYS = 7
# How many completed jobs of an item the median is taken over.
DURATION_SAMPLE = 5
# Stalled once elapsed exceeds max(FACTOR x expected, expected + FLOOR). The
# additive floor is the same lesson as the over-run guard's +10: doubling alone
# is far too tight at small values and would call a two-hour job stalled after
# four.
STALL_FACTOR = 2
STALL_FLOOR = timedelta(days=1)


async def expected_duration_seconds(
    db: AsyncSession, org_id: uuid.UUID, item_id: uuid.UUID, quantity: Decimal
) -> float:
    """How long a job like this should take, from the best evidence there is.

    In order: the item's routing, which is the shop's own stated intent and is
    already recorded; else the median per-unit duration of its recently
    completed jobs, median rather than mean so one job left open over a holiday
    does not move the bar; else a cold-start constant.

    Per item, not per org, because a shop making both a five-minute bracket and
    a three-week assembly has no single sensible number, which is precisely the
    problem a constant cannot solve.
    """
    setup_total, cycle_total = (
        await db.execute(
            select(
                func.sum(func.coalesce(RoutingStep.setup_time_seconds, 0)),
                func.sum(func.coalesce(RoutingStep.cycle_time_seconds, 0)),
            )
            .join(Routing, Routing.id == RoutingStep.routing_id)
            .where(
                Routing.org_id == org_id,
                Routing.item_id == item_id,
                Routing.status == "active",
            )
        )
    ).one()
    if setup_total or cycle_total:
        return float(setup_total or 0) + float(cycle_total or 0) * float(quantity)

    rows = (
        await db.execute(
            select(WorkOrder.started_at, WorkOrder.finished_at, WorkOrder.quantity_planned)
            .where(
                WorkOrder.org_id == org_id,
                WorkOrder.item_id == item_id,
                WorkOrder.status == "done",
                WorkOrder.started_at.is_not(None),
                WorkOrder.finished_at.is_not(None),
            )
            .order_by(WorkOrder.finished_at.desc())
            .limit(DURATION_SAMPLE)
        )
    ).all()
    per_unit = [
        (ensure_utc(finished) - ensure_utc(started)).total_seconds() / float(planned)
        for started, finished, planned in rows
        if planned and planned > 0
    ]
    if per_unit:
        return median(per_unit) * float(quantity)

    return DEFAULT_JOB_DAYS * 24 * 3600


@dataclass
class JobAlertLine:
    wo_id: uuid.UUID
    wo_number: int
    item_sku: str
    item_name: str
    # late_to_start | stalled
    reason: str
    days_over: float


async def compute_job_alerts(db: AsyncSession, org_id: uuid.UUID) -> list[JobAlertLine]:
    """Jobs that have gone quiet: scheduled past their date, or running far
    longer than a job like them should. A flag on a page nobody opens is not
    chasing anything, so these ride the morning brief the shop already reads."""
    now = datetime.now(timezone.utc)
    today = now.date()
    rows = (
        await db.execute(
            select(WorkOrder, Item)
            .join(Item, Item.id == WorkOrder.item_id)
            .where(WorkOrder.org_id == org_id, WorkOrder.status.in_(PLANNED_STATUSES))
            .order_by(WorkOrder.wo_number)
        )
    ).all()

    alerts: list[JobAlertLine] = []
    for job, item in rows:
        if job.status == "scheduled":
            # An undated job has nothing to be late against. It outranks every
            # dated job for stock, but flagging it would put a permanent alert
            # on the brief that no action can clear.
            if job.scheduled_start is not None and job.scheduled_start < today:
                alerts.append(
                    JobAlertLine(
                        wo_id=job.id,
                        wo_number=job.wo_number,
                        item_sku=item.sku,
                        item_name=item.name,
                        reason="late_to_start",
                        days_over=(today - job.scheduled_start).days,
                    )
                )
            continue

        if job.started_at is None:
            continue
        expected = timedelta(
            seconds=await expected_duration_seconds(db, org_id, job.item_id, job.quantity_planned)
        )
        limit = max(expected * STALL_FACTOR, expected + STALL_FLOOR)
        elapsed = now - ensure_utc(job.started_at)
        if elapsed > limit:
            alerts.append(
                JobAlertLine(
                    wo_id=job.id,
                    wo_number=job.wo_number,
                    item_sku=item.sku,
                    item_name=item.name,
                    reason="stalled",
                    days_over=(elapsed - limit).total_seconds() / 86400,
                )
            )
    return alerts
