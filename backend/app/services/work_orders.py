from __future__ import annotations

import uuid
from collections import defaultdict
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
from app.services.planning_loads import PLANNED_STATUSES, _UNDATED_RANKS_FIRST, _rank, resolve_grid_window


async def active_bom_id(db: AsyncSession, org_id: uuid.UUID, item_id: uuid.UUID) -> uuid.UUID | None:
    """The item's currently active recipe, or None if it has none. A job
    snapshots this so a later BOM edit cannot rewrite what it committed to."""
    return await db.scalar(
        select(Bom.id).where(Bom.org_id == org_id, Bom.item_id == item_id, Bom.status == "active")
    )


async def component_requirements(
    db: AsyncSession,
    org_id: uuid.UUID,
    item_id: uuid.UUID,
    quantity: Decimal,
    children: dict[uuid.UUID, list[tuple[uuid.UUID, Decimal]]] | None = None,
) -> list[tuple[uuid.UUID, Decimal]]:
    """What a job for `quantity` of `item_id` needs, one entry per direct
    component.

    Deliberately ONE level deep, not a full explosion: a work order consumes
    what its own recipe lists. If a sub-assembly is itself manufactured, it
    is consumed as a part and gets its own job; exploding through it here
    would consume raw material the shop never touched for this job.

    load_bom_children already folds each line's scrap_pct into the per-unit
    figure, so this must not apply scrap a second time.

    `children` is for a caller asking about many items at once (the planning
    grid asks per row): pass the map it already loaded rather than paying for
    the same query once per row.
    """
    if children is None:
        children = await load_bom_children(db, org_id)
    return [(child_id, qty_per * quantity) for child_id, qty_per in children.get(item_id, [])]


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


async def _load_routing_totals(
    db: AsyncSession, org_id: uuid.UUID, item_ids: set[uuid.UUID]
) -> dict[uuid.UUID, tuple[Decimal, Decimal]]:
    """Per item: (total setup seconds, total cycle seconds) over its active
    routing. One query for however many items are asked about, so a caller
    judging many jobs at once (compute_job_alerts) pays for this once rather
    than once per job."""
    if not item_ids:
        return {}
    rows = await db.execute(
        select(
            Routing.item_id,
            func.sum(func.coalesce(RoutingStep.setup_time_seconds, 0)),
            func.sum(func.coalesce(RoutingStep.cycle_time_seconds, 0)),
        )
        .join(RoutingStep, RoutingStep.routing_id == Routing.id)
        .where(
            Routing.org_id == org_id,
            Routing.item_id.in_(item_ids),
            Routing.status == "active",
        )
        .group_by(Routing.item_id)
    )
    return {item_id: (setup, cycle) for item_id, setup, cycle in rows.all()}


async def _load_recent_durations(
    db: AsyncSession, org_id: uuid.UUID, item_ids: set[uuid.UUID]
) -> dict[uuid.UUID, list[float]]:
    """Per item: per-unit duration (seconds) of its DURATION_SAMPLE most
    recently completed jobs, latest first. One query for however many items
    are asked about - see _load_routing_totals."""
    if not item_ids:
        return {}
    rows = await db.execute(
        select(WorkOrder.item_id, WorkOrder.started_at, WorkOrder.finished_at, WorkOrder.quantity_planned)
        .where(
            WorkOrder.org_id == org_id,
            WorkOrder.item_id.in_(item_ids),
            WorkOrder.status == "done",
            WorkOrder.started_at.is_not(None),
            WorkOrder.finished_at.is_not(None),
        )
        .order_by(WorkOrder.item_id, WorkOrder.finished_at.desc())
    )
    per_item: dict[uuid.UUID, list[float]] = defaultdict(list)
    for item_id, started, finished, planned in rows.all():
        if not planned or planned <= 0:
            continue
        bucket = per_item[item_id]
        if len(bucket) >= DURATION_SAMPLE:
            # Rows for this item arrive latest-finished-first (the ORDER BY
            # above), so once a bucket holds the sample size the rest of
            # this item's rows are older ones the original per-item LIMIT
            # would also have excluded.
            continue
        bucket.append((ensure_utc(finished) - ensure_utc(started)).total_seconds() / float(planned))
    return per_item


def _expected_duration_from(
    routing_totals: dict[uuid.UUID, tuple[Decimal, Decimal]],
    recent_durations: dict[uuid.UUID, list[float]],
    item_id: uuid.UUID,
    quantity: Decimal,
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
    setup_total, cycle_total = routing_totals.get(item_id, (Decimal(0), Decimal(0)))
    if setup_total or cycle_total:
        return float(setup_total or 0) + float(cycle_total or 0) * float(quantity)

    per_unit = recent_durations.get(item_id)
    if per_unit:
        return median(per_unit) * float(quantity)

    return DEFAULT_JOB_DAYS * 24 * 3600


async def expected_duration_seconds(
    db: AsyncSession, org_id: uuid.UUID, item_id: uuid.UUID, quantity: Decimal
) -> float:
    """Single-item convenience wrapper over the batched loaders above."""
    routing_totals = await _load_routing_totals(db, org_id, {item_id})
    recent_durations = await _load_recent_durations(db, org_id, {item_id})
    return _expected_duration_from(routing_totals, recent_durations, item_id, quantity)


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

    # Loaded once for every running job on the brief/grid, rather than once
    # per job: this used to call expected_duration_seconds (two queries) per
    # running job, so a shop with many jobs in flight paid for the same
    # routing/history lookups over and over.
    running_item_ids = {
        job.item_id for job, _ in rows if job.status != "scheduled" and job.started_at is not None
    }
    routing_totals = await _load_routing_totals(db, org_id, running_item_ids)
    recent_durations = await _load_recent_durations(db, org_id, running_item_ids)

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
            seconds=_expected_duration_from(
                routing_totals, recent_durations, job.item_id, job.quantity_planned
            )
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
