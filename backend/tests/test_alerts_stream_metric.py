from __future__ import annotations

import pytest

from app.models.alert import AlertRule
from app.models.integration import Integration
from app.models.sync_schedule import SyncSchedule
from app.models.sync_tick import SyncTick
from app.models.tenant import Org
from app.models.user import User
from app.services.alerts import eval_stream_metric


async def _make_schedule(db_session, *, email: str) -> SyncSchedule:
    org = Org(name="Acme")
    db_session.add(org)
    await db_session.flush()
    user = User(org_id=org.id, email=email, hashed_password="x")
    db_session.add(user)
    await db_session.flush()
    integration = Integration(org_id=org.id, name="Cloud DB", type="postgres", config={}, created_by=user.id)
    db_session.add(integration)
    await db_session.flush()
    schedule = SyncSchedule(
        org_id=org.id, integration_id=integration.id, name="Line 1",
        cursor_column="ts", interval_seconds=300, created_by=user.id,
    )
    db_session.add(schedule)
    await db_session.flush()
    return schedule


@pytest.mark.asyncio
async def test_eval_stream_metric_reads_latest_sync_tick(db_session):
    schedule = await _make_schedule(db_session, email="c@example.com")
    db_session.add(SyncTick(schedule_id=schedule.id, org_id=schedule.org_id, row_count=5, metrics={"throughput": 12.0}))
    await db_session.commit()

    rule = AlertRule(
        org_id=schedule.org_id, name="Throughput watch", type="stream_metric", severity="warning", enabled=True,
        config={"schedule_id": str(schedule.id), "metric_name": "throughput", "comparison": "gt", "threshold": 10},
    )

    trigger = await eval_stream_metric(db_session, rule)
    assert trigger.triggered is True
    assert trigger.value == 12.0


@pytest.mark.asyncio
async def test_eval_stream_metric_no_tick_yet(db_session):
    schedule = await _make_schedule(db_session, email="d@example.com")
    await db_session.commit()
    # deliberately no SyncTick added - schedule exists but has never synced

    rule = AlertRule(
        org_id=schedule.org_id, name="Throughput watch", type="stream_metric", severity="warning", enabled=True,
        config={"schedule_id": str(schedule.id), "metric_name": "throughput", "comparison": "gt", "threshold": 10},
    )

    trigger = await eval_stream_metric(db_session, rule)
    assert trigger.triggered is False
    assert "not synced yet" in trigger.message.lower()


@pytest.mark.asyncio
async def test_eval_stream_metric_missing_metric_name(db_session):
    schedule = await _make_schedule(db_session, email="e@example.com")
    db_session.add(SyncTick(schedule_id=schedule.id, org_id=schedule.org_id, row_count=1, metrics={"throughput": 1.0}))
    await db_session.commit()

    rule = AlertRule(
        org_id=schedule.org_id, name="Missing metric", type="stream_metric", severity="warning", enabled=True,
        config={"schedule_id": str(schedule.id), "metric_name": "scrap_rate_pct", "comparison": "gt", "threshold": 5},
    )

    trigger = await eval_stream_metric(db_session, rule)
    assert trigger.triggered is False
    assert "not available" in trigger.message.lower()
