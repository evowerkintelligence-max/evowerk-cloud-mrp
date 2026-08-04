"""CBM sensor extraction + anomaly scoring for a batch of freshly-synced
rows. Ported from app.services.sensor_consumer's per-tick Kafka handler (now
called synchronously from app.services.sync_scheduler after each scheduled
sync instead of via a Redpanda consumer) - same extraction/scoring logic,
unchanged."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sensor import SensorAnomaly, SensorBaseline, SensorReading
from app.models.sync_schedule import SyncSchedule
from app.services.anomaly import WINDOW, score_reading


async def process_rows(db: AsyncSession, schedule: SyncSchedule, rows: list[dict[str, Any]]) -> None:
    # Each row in this batch gets a strictly increasing ts (not one shared
    # `now`) so the control-chart's oldest-first ordering below reflects
    # actual fetch order instead of an arbitrary DB tie-break on identical
    # timestamps - order-sensitive Nelson rules (trends, runs) depend on it.
    now = datetime.now(timezone.utc)
    for i, row in enumerate(rows):
        ts = now + timedelta(microseconds=i)
        for sensor, field in schedule.sensor_map.items():
            raw = row.get(field)
            if raw is None:
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue

            reading = SensorReading(org_id=schedule.org_id, machine=schedule.machine, sensor=sensor, value=value, ts=ts)
            db.add(reading)
            await db.flush()

            window_rows = (
                await db.execute(
                    select(SensorReading)
                    .where(
                        SensorReading.org_id == schedule.org_id,
                        SensorReading.machine == schedule.machine,
                        SensorReading.sensor == sensor,
                    )
                    .order_by(SensorReading.ts.desc())
                    .limit(WINDOW)
                )
            ).scalars().all()
            window_rows = list(reversed(window_rows))  # oldest first

            result = score_reading([r.value for r in window_rows], [r.ts for r in window_rows])

            baseline = (
                await db.execute(
                    select(SensorBaseline).where(
                        SensorBaseline.org_id == schedule.org_id,
                        SensorBaseline.machine == schedule.machine,
                        SensorBaseline.sensor == sensor,
                    )
                )
            ).scalar_one_or_none()
            if baseline is None:
                baseline = SensorBaseline(org_id=schedule.org_id, machine=schedule.machine, sensor=sensor)
                db.add(baseline)
            baseline.n = result.n
            baseline.centerline = result.centerline
            baseline.ucl = result.ucl
            baseline.lcl = result.lcl
            baseline.mr_bar = result.mr_bar

            if not result.learning and not result.in_control:
                db.add(SensorAnomaly(
                    org_id=schedule.org_id, machine=schedule.machine, sensor=sensor,
                    reading_id=reading.id, violations=result.violations,
                ))
