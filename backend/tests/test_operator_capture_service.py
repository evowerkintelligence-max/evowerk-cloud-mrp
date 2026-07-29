# backend/tests/test_operator_capture_service.py
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.core.time import ensure_utc
from app.models.machine import Machine
from app.models.operator import Operator
from app.models.routing import Routing, RoutingStep
from app.models.work_center import WorkCenter
from app.models.work_order import WorkOrder
from app.services.operator_capture import open_capture_event, resolve_attribution

pytestmark = pytest.mark.asyncio

NOW = datetime.now(timezone.utc)


async def _seed_machine(db_session, org_id):
    wc = WorkCenter(org_id=org_id, code="WC1", name="Line 1")
    db_session.add(wc)
    await db_session.flush()
    machine = Machine(org_id=org_id, work_center_id=wc.id, code="M1", name="Machine 1")
    db_session.add(machine)
    await db_session.flush()
    return machine, wc


async def _seed_operator(db_session, org_id, name):
    operator = Operator(org_id=org_id, name=name, normalized_name=name.casefold())
    db_session.add(operator)
    await db_session.flush()
    return operator


async def _seed_in_progress_work_order(db_session, org_id, work_center_id, operator_id):
    from app.models.bom import Item
    from app.models.location import Location

    item = Item(org_id=org_id, sku="SKU1", name="Widget", item_type="finished_good", unit_of_measure="ea")
    db_session.add(item)
    location = Location(org_id=org_id, code="MAIN", name="Main")
    db_session.add(location)
    await db_session.flush()

    routing = Routing(org_id=org_id, item_id=item.id, version=1, status="active")
    db_session.add(routing)
    await db_session.flush()
    db_session.add(RoutingStep(org_id=org_id, routing_id=routing.id, sequence=1, work_center_id=work_center_id))

    wo = WorkOrder(
        org_id=org_id, item_id=item.id, bom_id=uuid.uuid4(), routing_id=routing.id,
        wo_number=1, status="in_progress", quantity_planned=Decimal("10"),
        location_id=location.id, started_at=NOW - timedelta(minutes=30),
        assigned_operator_id=operator_id, created_by=uuid.uuid4(),
    )
    db_session.add(wo)
    await db_session.flush()
    return wo


async def test_open_capture_event_closes_prior_open_event_on_same_machine(db_session):
    org_id = uuid.uuid4()
    machine, _ = await _seed_machine(db_session, org_id)
    a = await _seed_operator(db_session, org_id, "A")
    b = await _seed_operator(db_session, org_id, "B")

    first = await open_capture_event(
        db_session, org_id=org_id, operator_id=a.id, machine_id=machine.id,
        source="reader", scanned_at=NOW,
    )
    second_time = NOW + timedelta(minutes=5)
    second = await open_capture_event(
        db_session, org_id=org_id, operator_id=b.id, machine_id=machine.id,
        source="reader", scanned_at=second_time,
    )

    await db_session.refresh(first)
    assert ensure_utc(first.ended_at) == second_time
    assert second.ended_at is None


async def test_open_capture_event_clamps_ended_at_to_never_precede_started_at(db_session):
    org_id = uuid.uuid4()
    machine, _ = await _seed_machine(db_session, org_id)
    a = await _seed_operator(db_session, org_id, "A")
    b = await _seed_operator(db_session, org_id, "B")

    first = await open_capture_event(
        db_session, org_id=org_id, operator_id=a.id, machine_id=machine.id,
        source="reader", scanned_at=NOW,
    )
    # A backdated scan (still inside the route's accepted window) that predates
    # the currently-open event's own started_at must not produce a
    # negative-duration event.
    backdated = NOW - timedelta(minutes=5)
    await open_capture_event(
        db_session, org_id=org_id, operator_id=b.id, machine_id=machine.id,
        source="reader", scanned_at=backdated,
    )

    await db_session.refresh(first)
    assert ensure_utc(first.ended_at) == NOW


async def test_resolve_attribution_prefers_capture_event_over_work_order(db_session):
    org_id = uuid.uuid4()
    machine, wc = await _seed_machine(db_session, org_id)
    scanned = await _seed_operator(db_session, org_id, "Scanned")
    scheduled = await _seed_operator(db_session, org_id, "Scheduled")

    await open_capture_event(
        db_session, org_id=org_id, operator_id=scanned.id, machine_id=machine.id,
        source="reader", scanned_at=NOW,
    )
    await _seed_in_progress_work_order(db_session, org_id, wc.id, scheduled.id)

    result = await resolve_attribution(
        db_session, org_id=org_id, machine_id=machine.id,
        window_start=NOW, window_end=NOW + timedelta(minutes=1),
    )
    assert result is not None
    assert result.id == scanned.id


async def test_resolve_attribution_falls_back_to_work_order_assignment(db_session):
    org_id = uuid.uuid4()
    machine, wc = await _seed_machine(db_session, org_id)
    scheduled = await _seed_operator(db_session, org_id, "Scheduled")
    await _seed_in_progress_work_order(db_session, org_id, wc.id, scheduled.id)

    result = await resolve_attribution(
        db_session, org_id=org_id, machine_id=machine.id,
        window_start=NOW, window_end=NOW + timedelta(minutes=1),
    )
    assert result is not None
    assert result.id == scheduled.id


async def test_resolve_attribution_returns_none_when_neither_exists(db_session):
    org_id = uuid.uuid4()
    machine, _ = await _seed_machine(db_session, org_id)

    result = await resolve_attribution(
        db_session, org_id=org_id, machine_id=machine.id,
        window_start=NOW, window_end=NOW + timedelta(minutes=1),
    )
    assert result is None
