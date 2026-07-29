# backend/app/services/operator_capture.py
"""Who was at a machine, per the identity-capture spec's attribution model.
open_capture_event is what both public capture routes
(app.api.routes.operator_capture) call after resolving an operator, whichever
tier they came from. resolve_attribution is the one read anything downstream
(the live operator board, a future sensor-driven OEE-by-operator view) calls,
so it never has to know which tier answered."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.capture_event import CaptureEvent
from app.models.machine import Machine
from app.models.operator import Operator
from app.models.routing import RoutingStep
from app.models.work_order import WorkOrder


async def open_capture_event(
    db: AsyncSession,
    org_id: uuid.UUID,
    operator_id: uuid.UUID,
    machine_id: uuid.UUID,
    source: str,
    scanned_at: datetime,
) -> CaptureEvent:
    """Closes whatever capture event is still open on this machine, then
    opens a new one - a machine has at most one open event at a time."""
    open_event = (
        await db.execute(
            select(CaptureEvent).where(
                CaptureEvent.org_id == org_id,
                CaptureEvent.machine_id == machine_id,
                CaptureEvent.ended_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if open_event is not None:
        open_event.ended_at = scanned_at

    event = CaptureEvent(
        org_id=org_id, operator_id=operator_id, machine_id=machine_id,
        source=source, started_at=scanned_at,
    )
    db.add(event)
    await db.flush()
    return event


async def resolve_attribution(
    db: AsyncSession,
    org_id: uuid.UUID,
    machine_id: uuid.UUID,
    window_start: datetime,
    window_end: datetime,
) -> Operator | None:
    """1) An overlapping capture event (reader or QR) beats everything else -
    a real scan. 2) Otherwise, the operator assigned to whichever in-progress
    work order's routing step uses this machine's work center during the
    window - a schedule, not an observation. 3) Otherwise None: the
    dimension is simply unpopulated for that window, the same
    graceful-absence behaviour every other optional column here already has."""
    event = (
        await db.execute(
            select(CaptureEvent)
            .where(
                CaptureEvent.org_id == org_id,
                CaptureEvent.machine_id == machine_id,
                CaptureEvent.started_at < window_end,
                or_(CaptureEvent.ended_at.is_(None), CaptureEvent.ended_at > window_start),
            )
            .order_by(CaptureEvent.started_at.desc())
        )
    ).scalars().first()
    if event is not None:
        return await db.get(Operator, event.operator_id)

    machine = await db.get(Machine, machine_id)
    if machine is None:
        return None

    wo = (
        await db.execute(
            select(WorkOrder)
            .join(RoutingStep, RoutingStep.routing_id == WorkOrder.routing_id)
            .where(
                WorkOrder.org_id == org_id,
                WorkOrder.status == "in_progress",
                WorkOrder.assigned_operator_id.is_not(None),
                RoutingStep.work_center_id == machine.work_center_id,
                WorkOrder.started_at < window_end,
                or_(WorkOrder.finished_at.is_(None), WorkOrder.finished_at > window_start),
            )
        )
    ).scalars().first()
    if wo is None or wo.assigned_operator_id is None:
        return None
    return await db.get(Operator, wo.assigned_operator_id)
