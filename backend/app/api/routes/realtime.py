"""Real-time streaming API — manages live-polling collectors over
Integrations. Ported from the desktop app's routes/realtime.py; the
persistence/resume model is described in app.services.realtime and
app.models.realtime.

Note on auth for the SSE route: native browser `EventSource` can't set an
Authorization header, so the frontend must use a fetch-based SSE reader
(manually parsing `data: ...` frames) instead of `new EventSource(url)`, to
keep the same Bearer-token auth as every other route rather than falling back
to a token-in-query-string (which ends up logged in access logs/proxies).
"""
from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_or_404
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.integration import Integration
from app.models.realtime import RealtimeStream
from app.services import alerts as alerts_service
from app.services import realtime as realtime_service
from app.services import realtime_cache

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


def _snapshot_dict(stream: RealtimeStream, alerts: list) -> dict:
    """Live snapshot from the realtime_cache (populated by the Flink job's
    output via realtime_cache.CacheConsumer) if this stream has produced at
    least one windowed metrics message anywhere in the cluster, else a
    definition-only snapshot.

    A stopped stream's cached snapshot is never used, even if one exists -
    stopping a TickProducer means no more ticks/windows will ever arrive to
    overwrite it, so the cache would otherwise report a stale "running"
    snapshot forever. And when a cached snapshot IS used, its "name" field
    is overlaid with stream.name: the Flink job's MetricSnapshot.name is the
    stream_id (its key), not the human-readable name - RealtimeStream isn't
    in the tick payload, so this is the one place that can fill it in."""
    snap = realtime_cache.get_snapshot(stream.id) if stream.desired_status != "stopped" else None
    if snap is None:
        snap = {
            "ts": None, "stream_id": str(stream.id), "name": stream.name,
            "status": "stopped", "error": stream.last_error,
            "rows_in_window": 0, "rows_collected": 0,
            "duration": None, "duration_s": None, "last_tick_s": None, "metrics": {},
        }
    else:
        snap = {**snap, "name": stream.name}
    snap["alerts"] = [vars(a) for a in alerts]
    return snap


async def _snapshot_for(db: AsyncSession, org_id: uuid.UUID, stream: RealtimeStream) -> dict:
    alerts = await alerts_service.stream_alerts(db, org_id, stream.id)
    return _snapshot_dict(stream, alerts)


def _start(stream: RealtimeStream, integration: Integration) -> None:
    from app.services.integrations import decrypt_config

    realtime_service.start_runner(
        stream_id=stream.id,
        name=stream.name,
        org_id=stream.org_id,
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
    # One query for every stream's alert rules instead of one per stream.
    alerts_by_stream = await alerts_service.stream_alerts_bulk(db, current.org_id, [s.id for s in rows])
    return {"streams": [_snapshot_dict(s, alerts_by_stream.get(str(s.id), [])) for s in rows]}


@router.get("/streams/{stream_id}/snapshot")
async def stream_snapshot(
    stream_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    stream = await get_or_404(db, RealtimeStream, current.org_id, stream_id, "Stream not found")
    return await _snapshot_for(db, current.org_id, stream)


@router.get("/streams/{stream_id}/history")
async def stream_history(
    stream_id: uuid.UUID,
    limit: int = 2000,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Persisted tick history — survives a backend restart and works even
    while the stream isn't currently running in this process."""
    await get_or_404(db, RealtimeStream, current.org_id, stream_id, "Stream not found")
    return {"stream_id": str(stream_id), "points": realtime_service.read_history(str(stream_id), max_points=limit)}


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
    realtime_service.stop_runner(stream.id)
    stream.desired_status = "stopped"
    await db.commit()
    return await _snapshot_for(db, current.org_id, stream)


@router.delete("/streams/{stream_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_stream(
    stream_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    stream = await get_or_404(db, RealtimeStream, current.org_id, stream_id, "Stream not found")
    realtime_service.stop_runner(stream.id)
    realtime_service.delete_history(str(stream.id))
    await db.delete(stream)
    await log_event(db, action="realtime_stream.deleted", org_id=current.org_id, actor_user_id=current.id, target=str(stream_id))
    await db.commit()


# -----------------------------
# SSE feed
# -----------------------------

async def _sse_generator(stream_id: uuid.UUID, org_id: uuid.UUID, request: Request):
    from app.db import session as db_session

    async def _load_stream() -> RealtimeStream | None:
        async with db_session.async_session_factory() as db:
            return (
                await db.execute(select(RealtimeStream).where(RealtimeStream.id == stream_id, RealtimeStream.org_id == org_id))
            ).scalar_one_or_none()

    async def _snapshot(stream: RealtimeStream) -> dict:
        async with db_session.async_session_factory() as db:
            return await _snapshot_for(db, org_id, stream)

    stream = await _load_stream()
    if stream is None:
        yield f"event: error\ndata: {json.dumps({'error': 'stream not found'})}\n\n"
        return

    yield f"data: {json.dumps(await _snapshot(stream))}\n\n"

    try:
        while True:
            if await request.is_disconnected():
                break
            runner = realtime_service.get_runner(stream_id)
            await asyncio.sleep(runner.interval_seconds if runner else stream.interval_seconds)

            stream = await _load_stream()
            if stream is None:
                yield f"event: stopped\ndata: {json.dumps({'stream_id': str(stream_id)})}\n\n"
                break
            yield f"data: {json.dumps(await _snapshot(stream))}\n\n"
    except asyncio.CancelledError:
        pass


@router.get("/streams/{stream_id}/events")
async def stream_events(
    stream_id: uuid.UUID,
    request: Request,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await get_or_404(db, RealtimeStream, current.org_id, stream_id, "Stream not found")  # 404s before opening the stream if unknown/foreign
    return StreamingResponse(
        _sse_generator(stream_id, current.org_id, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
