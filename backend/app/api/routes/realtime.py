"""Realtime streams API. Collectors (app.services.realtime.StreamCollector)
write one RealtimeSample row per poll window; every read here is a plain DB
query, so any replica can serve any stream. The frontend syncs incrementally
via GET .../samples?since=<cursor> instead of holding an SSE connection."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_or_404
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.integration import Integration
from app.models.realtime import RealtimeSample, RealtimeStream
from app.services import alerts as alerts_service
from app.services import realtime as realtime_service

router = APIRouter(prefix="/realtime", tags=["realtime"])


# -----------------------------
# Schemas
# -----------------------------

class StreamCreate(BaseModel):
    integration_id: uuid.UUID
    name: str = ""
    interval_seconds: int = Field(5, ge=1, le=300)
    accumulate: bool = True
    column_map: dict[str, str] = Field(default_factory=dict)
    machine: str | None = None
    sensor_map: dict[str, str] = Field(default_factory=dict)


async def _snapshot_for(db: AsyncSession, org_id: uuid.UUID, stream: RealtimeStream) -> dict:
    snap = await realtime_service.latest_snapshot(db, stream)
    alerts = await alerts_service.stream_alerts(db, org_id, stream.id)
    snap["alerts"] = [vars(a) for a in alerts]
    return snap


def _start(stream: RealtimeStream, integration: Integration) -> None:
    from app.services.integrations import decrypt_config

    realtime_service.start_runner(
        stream_id=stream.id,
        org_id=stream.org_id,
        name=stream.name,
        integration_type=integration.type,
        integration_config=decrypt_config(integration.config),
        column_map=stream.column_map,
        interval_seconds=stream.interval_seconds,
        accumulate=stream.accumulate,
    )


# -----------------------------
# CRUD
# -----------------------------

@router.post("/streams")
async def create_stream(
    req: StreamCreate, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    integration = await get_or_404(db, Integration, current.org_id, req.integration_id, "Integration not found")

    stream = RealtimeStream(
        org_id=current.org_id,
        integration_id=integration.id,
        name=req.name or integration.name,
        column_map=req.column_map,
        machine=req.machine,
        sensor_map=req.sensor_map,
        interval_seconds=req.interval_seconds,
        accumulate=req.accumulate,
        desired_status="running",
        created_by=current.id,
    )
    db.add(stream)
    await log_event(
        db, action="realtime_stream.created", org_id=current.org_id, actor_user_id=current.id,
        metadata={"name": stream.name, "integration_id": str(integration.id)},
    )
    await db.commit()
    await db.refresh(stream)

    _start(stream, integration)
    return await _snapshot_for(db, current.org_id, stream)


@router.get("/streams")
async def list_streams(current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(select(RealtimeStream).where(RealtimeStream.org_id == current.org_id).order_by(RealtimeStream.name))
    ).scalars().all()
    snaps = await realtime_service.latest_snapshots_bulk(db, rows)
    # One query for every stream's alert rules instead of one per stream.
    alerts_by_stream = await alerts_service.stream_alerts_bulk(db, current.org_id, [s.id for s in rows])
    out = []
    for s in rows:
        snap = snaps[str(s.id)]
        snap["alerts"] = [vars(a) for a in alerts_by_stream.get(str(s.id), [])]
        out.append(snap)
    return {"streams": out}


@router.get("/streams/{stream_id}/snapshot")
async def stream_snapshot(
    stream_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    stream = await get_or_404(db, RealtimeStream, current.org_id, stream_id, "Stream not found")
    return await _snapshot_for(db, current.org_id, stream)


@router.get("/streams/{stream_id}/samples")
async def stream_samples(
    stream_id: uuid.UUID,
    since: int | None = None,
    limit: int = 500,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Incremental sync: samples with id > `since`, oldest first (`limit`
    capped at 2000; a truncated page self-heals because `cursor` is the
    highest id returned). Without `since`, returns only the latest sample.
    External metrics (AutoML live scores) are merged into the newest
    returned sample, mirroring the old snapshot-cache merge."""
    await get_or_404(db, RealtimeStream, current.org_id, stream_id, "Stream not found")
    rows, cursor = await realtime_service.samples_since(db, stream_id, since, limit)
    samples = [realtime_service.sample_dict(s) for s in rows]
    if samples:
        external = await realtime_service.external_metrics(db, stream_id)
        if external:
            samples[-1]["metrics"] = {**samples[-1]["metrics"], **external}
    return {"stream_id": str(stream_id), "cursor": cursor, "samples": samples}


@router.get("/streams/{stream_id}/history")
async def stream_history(
    stream_id: uuid.UUID,
    limit: int = 2000,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Persisted sample history: durable across restarts and visible from
    every replica (the old per-container JSONL files were neither)."""
    await get_or_404(db, RealtimeStream, current.org_id, stream_id, "Stream not found")
    limit = max(1, min(20_000, int(limit)))
    rows = (
        await db.execute(
            select(RealtimeSample)
            .where(RealtimeSample.stream_id == stream_id)
            .order_by(RealtimeSample.id.desc())
            .limit(limit)
        )
    ).scalars().all()
    # desc() + reversed(): the most recent `limit` samples, presented
    # oldest-first. An ascending query would return the OLDEST `limit` instead.
    points = [
        {"ts": s.ts.isoformat() if s.ts else None, "metrics": s.metrics} for s in reversed(rows)
    ]
    return {"stream_id": str(stream_id), "points": points}


@router.post("/streams/{stream_id}/start")
async def start_stream(
    stream_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    stream = await get_or_404(db, RealtimeStream, current.org_id, stream_id, "Stream not found")
    integration = await get_or_404(db, Integration, current.org_id, stream.integration_id, "Integration not found")

    stream.desired_status = "running"
    stream.last_error = None
    await db.commit()

    _start(stream, integration)
    return await _snapshot_for(db, current.org_id, stream)


@router.post("/streams/{stream_id}/stop")
async def stop_stream(
    stream_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    stream = await get_or_404(db, RealtimeStream, current.org_id, stream_id, "Stream not found")
    await realtime_service.stop_runner(stream.id)
    stream.desired_status = "stopped"
    await db.commit()
    return await _snapshot_for(db, current.org_id, stream)


@router.delete("/streams/{stream_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_stream(
    stream_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    stream = await get_or_404(db, RealtimeStream, current.org_id, stream_id, "Stream not found")
    await realtime_service.stop_runner(stream.id)
    await db.delete(stream)  # realtime_samples rows cascade with the stream
    await log_event(db, action="realtime_stream.deleted", org_id=current.org_id, actor_user_id=current.id, target=str(stream_id))
    await db.commit()
