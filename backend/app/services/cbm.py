"""Fleet dashboard and per-machine detail for CBM - a pure read/rollup layer
over SensorReading/SensorBaseline/SensorAnomaly (Task 2/3/4) and WeibullFit
(Task 5). No new detection logic lives here."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.spc_calc import MIN_BASELINE
from app.models.realtime import RealtimeStream
from app.models.sensor import SensorAnomaly, SensorBaseline, SensorReading
from app.services.rul import days_since_last_breakdown, failure_probability_30d, get_or_refit

_READINGS_PER_SENSOR = 200
_STALE_AFTER_MINUTES = 15


def _risk_level(prob: float | None) -> str:
    if prob is None:
        return "unknown"
    if prob >= 0.65:
        return "high"
    if prob >= 0.35:
        return "medium"
    return "low"


async def _failure_probability(db: AsyncSession, org_id: uuid.UUID, machine: str):
    """get_or_refit + days_since_last_breakdown, both needed to condition the
    30-day probability on how long this machine has already run since its
    last breakdown - see app.services.rul.failure_probability_30d."""
    fit = await get_or_refit(db, org_id, machine)
    age = await days_since_last_breakdown(db, org_id, machine)
    return fit, failure_probability_30d(fit, age)


async def fleet_dashboard(db: AsyncSession, org_id: uuid.UUID) -> list[dict]:
    streams = (
        await db.execute(
            select(RealtimeStream).where(RealtimeStream.org_id == org_id, RealtimeStream.machine.is_not(None))
        )
    ).scalars().all()

    out = []
    for stream in streams:
        machine = stream.machine

        if not stream.sensor_map:
            out.append({
                "machine": machine, "status": "unmapped", "risk_level": "unknown",
                "failure_probability_30d": None, "active_anomalies": 0,
                "last_reading_at": None, "stream_id": str(stream.id),
            })
            continue

        baselines = (
            await db.execute(select(SensorBaseline).where(SensorBaseline.org_id == org_id, SensorBaseline.machine == machine))
        ).scalars().all()

        # No baselines yet (no readings scored) or any sensor still under
        # MIN_BASELINE readings -> still learning. Once every mapped sensor
        # has a baseline past MIN_BASELINE, the machine is "active".
        if not baselines or any(b.n < MIN_BASELINE for b in baselines):
            status = "learning"
        else:
            status = "active"

        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        active_anomalies = (
            await db.execute(
                select(SensorAnomaly).where(
                    SensorAnomaly.org_id == org_id, SensorAnomaly.machine == machine,
                    SensorAnomaly.detected_at >= cutoff, SensorAnomaly.acknowledged_at.is_(None),
                )
            )
        ).scalars().all()

        last_reading = (
            await db.execute(
                select(SensorReading)
                .where(SensorReading.org_id == org_id, SensorReading.machine == machine)
                .order_by(SensorReading.ts.desc()).limit(1)
            )
        ).scalar_one_or_none()

        _fit, prob = await _failure_probability(db, org_id, machine)

        out.append({
            "machine": machine, "status": status, "risk_level": _risk_level(prob),
            "failure_probability_30d": prob, "active_anomalies": len(active_anomalies),
            "last_reading_at": last_reading.ts.isoformat() if last_reading else None,
            "stream_id": str(stream.id),
        })

    out.sort(key=lambda m: {"high": 0, "medium": 1, "low": 2, "unknown": 3}.get(m["risk_level"], 4))
    return out


async def machine_detail(db: AsyncSession, org_id: uuid.UUID, machine: str) -> dict:
    baselines = (
        await db.execute(select(SensorBaseline).where(SensorBaseline.org_id == org_id, SensorBaseline.machine == machine))
    ).scalars().all()

    sensors: dict[str, dict] = {}
    for baseline in baselines:
        readings = (
            await db.execute(
                select(SensorReading)
                .where(SensorReading.org_id == org_id, SensorReading.machine == machine, SensorReading.sensor == baseline.sensor)
                .order_by(SensorReading.ts.desc()).limit(_READINGS_PER_SENSOR)
            )
        ).scalars().all()
        readings = list(reversed(readings))

        anomalies = (
            await db.execute(
                select(SensorAnomaly)
                .where(SensorAnomaly.org_id == org_id, SensorAnomaly.machine == machine, SensorAnomaly.sensor == baseline.sensor)
                .order_by(SensorAnomaly.detected_at.desc()).limit(50)
            )
        ).scalars().all()

        sensors[baseline.sensor] = {
            "centerline": baseline.centerline, "ucl": baseline.ucl, "lcl": baseline.lcl, "n": baseline.n,
            "readings": [{"value": r.value, "ts": r.ts.isoformat()} for r in readings],
            "anomalies": [
                {
                    "id": str(a.id), "violations": a.violations, "detected_at": a.detected_at.isoformat(),
                    "acknowledged_at": a.acknowledged_at.isoformat() if a.acknowledged_at else None,
                }
                for a in anomalies
            ],
        }

    fit, prob = await _failure_probability(db, org_id, machine)

    return {
        "machine": machine, "risk_level": _risk_level(prob), "failure_probability_30d": prob,
        "fit_tier": fit.fit_tier, "sensors": sensors,
    }


async def acknowledge_anomaly(db: AsyncSession, org_id: uuid.UUID, anomaly_id: uuid.UUID) -> SensorAnomaly | None:
    anomaly = (
        await db.execute(select(SensorAnomaly).where(SensorAnomaly.org_id == org_id, SensorAnomaly.id == anomaly_id))
    ).scalar_one_or_none()
    if anomaly is None:
        return None
    anomaly.acknowledged_at = datetime.now(timezone.utc)
    await db.commit()
    return anomaly
