"""CBM sensor ingestion: extracts named sensor channels from a stream's
polled rows, persists SensorReading rows, refreshes the I-MR baseline, and
flags out-of-control readings via app.services.anomaly. Called by
app.services.realtime.StreamCollector inside the collector's per-window
transaction (the caller commits)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.realtime import RealtimeStream
from app.models.sensor import SensorAnomaly, SensorBaseline, SensorReading
from app.services.anomaly import WINDOW, score_reading


async def ingest_rows(db: AsyncSession, stream: RealtimeStream, rows: list[dict]) -> None:
    """Persist one SensorReading per (row, mapped sensor), rescore that
    sensor's trailing window, and flag it if it went out of control.

    ponytail: one INSERT plus a flush plus two SELECTs per row per sensor,
    all in the caller's single transaction, so a full 1000-row window with 3
    sensors is ~12k round trips. Fine at the interval-poll rates streams
    actually run at, not fine for a backfill. Upgrade path: bulk-insert the
    window's readings first, then score each sensor once off an incrementally
    maintained window instead of re-SELECTing the trailing 200 per row.
    """
    if not stream.sensor_map or not stream.machine:
        return

    # One distinct, increasing timestamp per row, so the scoring path's
    # ORDER BY ts DESC is a real arrival order and points[-1] is genuinely the
    # newest reading. Shared timestamps made that ordering arbitrary while the
    # anomaly row still pinned a specific reading.id. Offsets run backwards
    # from now so the window's rows stay inside the window's real time.
    now = datetime.now(timezone.utc) - timedelta(microseconds=max(0, len(rows) - 1))
    for i, row in enumerate(rows):
        ts = now + timedelta(microseconds=i)
        for sensor, field in stream.sensor_map.items():
            raw = row.get(field)
            if raw is None:
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue

            reading = SensorReading(
                org_id=stream.org_id, machine=stream.machine, sensor=sensor, value=value, ts=ts,
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
