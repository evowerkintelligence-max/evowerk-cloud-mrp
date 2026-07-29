# backend/tests/test_capture_event_model.py
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.models.capture_event import CaptureEvent
from app.models.machine import Machine
from app.models.operator import Operator
from app.models.work_center import WorkCenter

pytestmark = pytest.mark.asyncio


async def test_create_capture_event(db_session):
    org_id = uuid.uuid4()
    wc = WorkCenter(org_id=org_id, code="WC1", name="Line 1")
    db_session.add(wc)
    await db_session.flush()
    machine = Machine(org_id=org_id, work_center_id=wc.id, code="M1", name="Machine 1")
    db_session.add(machine)
    operator = Operator(org_id=org_id, name="J Kim", normalized_name="j kim")
    db_session.add(operator)
    await db_session.flush()

    event = CaptureEvent(
        org_id=org_id, operator_id=operator.id, machine_id=machine.id,
        source="reader", started_at=datetime.now(timezone.utc),
    )
    db_session.add(event)
    await db_session.flush()

    assert event.id is not None
    assert event.ended_at is None
    assert event.source == "reader"
