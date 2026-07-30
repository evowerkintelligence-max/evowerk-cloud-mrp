"""CBM sensor ingestion: extracts named sensor channels from a stream's
polled rows, persists SensorReading rows, refreshes the I-MR baseline, and
flags out-of-control readings via app.services.anomaly. Called by
app.services.realtime.StreamCollector inside the collector's per-window
transaction (the caller commits)."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.realtime import RealtimeStream
from app.models.sensor import SensorAnomaly, SensorBaseline, SensorReading
from app.services.anomaly import WINDOW, score_reading


async def ingest_rows(db: AsyncSession, stream: RealtimeStream, rows: list[dict]) -> None:
    if not stream.sensor_map or not stream.machine:
        return

    now = datetime.now(timezone.utc)
    for row in rows:
        for sensor, field in stream.sensor_map.items():
            raw = row.get(field)
            if raw is None:
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue

            reading = SensorReading(
                org_id=stream.org_id, machine=stream.machine, sensor=sensor, value=value, ts=now,
            )
            db.add(reading)
            await db.flush()

            window_rows = (
                await db.execute(
                    select(SensorReading)
                    .where(
                        SensorReading.org_id == stream.org_id,
                        SensorReading.machine == stream.machine,
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
                        SensorBaseline.org_id == stream.org_id,
                        SensorBaseline.machine == stream.machine,
                        SensorBaseline.sensor == sensor,
                    )
                )
            ).scalar_one_or_none()
            if baseline is None:
                baseline = SensorBaseline(org_id=stream.org_id, machine=stream.machine, sensor=sensor)
                db.add(baseline)
            baseline.n = result.n
            baseline.centerline = result.centerline
            baseline.ucl = result.ucl
            baseline.lcl = result.lcl
            baseline.mr_bar = result.mr_bar

            if not result.learning and not result.in_control:
                db.add(SensorAnomaly(
                    org_id=stream.org_id, machine=stream.machine, sensor=sensor,
                    reading_id=reading.id, violations=result.violations,
                ))
