from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.downtime_tracking import DowntimeIncident
from app.services.rul import failure_probability_30d, get_or_refit

pytestmark = pytest.mark.asyncio


async def _make_breakdown(db_session, org_id, user_id, machine, started_at, ended_at):
    incident = DowntimeIncident(
        org_id=org_id, started_by=user_id, machine=machine, reason="breakdown",
        shift="Day", started_at=started_at, ended_at=ended_at,
    )
    db_session.add(incident)


async def test_get_or_refit_per_machine_tier_with_enough_failures(db_session):
    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    # 6 breakdowns roughly every 20 days - enough for the per_machine tier (>=5).
    for i in range(6):
        started = now - timedelta(days=(6 - i) * 20)
        await _make_breakdown(db_session, org_id, user_id, "M1", started, started + timedelta(hours=2))
    await db_session.commit()

    fit = await get_or_refit(db_session, org_id, "M1")

    # 6 breakdown events produce 5 *completed* inter-breakdown intervals (the
    # gaps between consecutive breakdowns) plus one still-open, right-censored
    # interval since the last one - n_failures counts completed intervals
    # (the uncensored samples the Weibull fit actually uses), not raw events.
    assert fit.fit_tier == "per_machine"
    assert fit.n_failures == 5
    assert fit.n_censored == 1
    assert fit.shape_k is not None
    assert fit.scale_lambda is not None
    prob = failure_probability_30d(fit)
    assert prob is not None
    assert 0.0 <= prob <= 1.0


async def test_get_or_refit_insufficient_data_tier(db_session):
    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    # Only 1 breakdown fleet-wide - below both tiers' minimum of 5.
    await _make_breakdown(db_session, org_id, user_id, "M2", now - timedelta(days=10), now - timedelta(days=9))
    await db_session.commit()

    fit = await get_or_refit(db_session, org_id, "M2")

    assert fit.fit_tier == "insufficient_data"
    assert fit.shape_k is None
    assert failure_probability_30d(fit) is None


async def test_get_or_refit_is_lazy_per_day(db_session):
    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    for i in range(6):
        started = now - timedelta(days=(6 - i) * 20)
        await _make_breakdown(db_session, org_id, user_id, "M3", started, started + timedelta(hours=2))
    await db_session.commit()

    first = await get_or_refit(db_session, org_id, "M3")
    first_fit_at = first.fit_at

    # A second call the same day must return the same persisted row, not refit.
    second = await get_or_refit(db_session, org_id, "M3")
    assert second.id == first.id
    assert second.fit_at == first_fit_at
