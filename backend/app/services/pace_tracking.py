# backend/app/services/pace_tracking.py
"""Pace-tracking session lifecycle: start/tap/pause/resume/stop/discard for a
manual/assembly line with no sensors - the operator taps a button per
completed unit. Stop materializes the session into a normal, immutable
Dataset row via the same ingestion path a CSV upload uses
(datasets.create_dataset), so every OEE engine feature works on
pace-tracking data with zero changes.
"""
from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import ensure_utc
from app.models.bom import Item
from app.models.pace_tracking import PaceSession, PaceTap
from app.models.work_order import WorkOrder
from app.services import datasets as ds_service

_PAUSE_FIELD = {"breakdown": "unplanned_dt_min", "setup": "planned_dt_min", "other": "other_dt_min"}


async def start_session(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    created_by: uuid.UUID,
    line: str,
    operator: str,
    target_rate: float | None,
    work_order_id: uuid.UUID | None = None,
) -> PaceSession:
    session = PaceSession(
        org_id=org_id,
        created_by=created_by,
        line=line,
        operator=operator,
        target_rate=target_rate,
        work_order_id=work_order_id,
    )
    db.add(session)
    await db.flush()
    return session


async def linked_job_labels(
    db: AsyncSession, session: PaceSession
) -> tuple[int | None, str | None]:
    """The job number and product SKU behind a linked session, or (None, None).
    One query, and none at all for the unlinked sessions that are still the
    common case."""
    if session.work_order_id is None:
        return None, None
    row = (
        await db.execute(
            select(WorkOrder.wo_number, Item.sku)
            .join(Item, Item.id == WorkOrder.item_id)
            .where(WorkOrder.id == session.work_order_id)
        )
    ).first()
    return (row[0], row[1]) if row is not None else (None, None)


async def tap_counts(db: AsyncSession, session_id: uuid.UUID) -> tuple[int, int]:
    rows = (
        await db.execute(
            select(PaceTap.kind, func.count()).where(PaceTap.session_id == session_id).group_by(PaceTap.kind)
        )
    ).all()
    counts = {kind: int(n) for kind, n in rows}
    return counts.get("good", 0), counts.get("reject", 0)


async def record_tap(db: AsyncSession, session: PaceSession, kind: str, reason: str | None = None) -> PaceTap:
    if session.status != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, f"Session is {session.status}, not active")
    clean_reason = (reason or "").strip() or None
    tap = PaceTap(session_id=session.id, kind=kind, reason=clean_reason if kind == "reject" else None)
    db.add(tap)
    await db.flush()
    return tap


async def reason_counts(db: AsyncSession, session_id: uuid.UUID) -> dict[str, int]:
    rows = (
        await db.execute(
            select(PaceTap.reason, func.count())
            .where(PaceTap.session_id == session_id, PaceTap.kind == "reject")
            .group_by(PaceTap.reason)
        )
    ).all()
    return {(reason or "Unspecified"): int(n) for reason, n in rows}


async def pause_session(db: AsyncSession, session: PaceSession, reason: str) -> PaceSession:
    if session.status != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, f"Session is {session.status}, not active")
    session.status = "paused"
    session.pause_reason = reason
    session.paused_at = datetime.now(timezone.utc)
    await db.flush()
    return session


async def _finalize_pause(db: AsyncSession, session: PaceSession, now: datetime) -> None:
    """Accumulate the just-ended pause window into whichever bucket matches
    pause_reason, via an atomic SQL increment (not a Python load-mutate-commit
    round trip) so two concurrent increments to the same column always sum
    correctly instead of one clobbering the other. This does NOT lock the row
    against a genuinely concurrent pause/resume cycle: status is still a Python
    read-check-write with no SELECT ... FOR UPDATE, so two near-simultaneous
    resume() calls could both pass the "paused" guard and double-count the same
    window. Acceptable for this feature's single-operator-per-session model;
    not a general-purpose distributed lock. Called from both resume() and
    stop() (when stopping while paused) - one code path, so the two can't
    drift apart."""
    elapsed_min = (now - ensure_utc(session.paused_at)).total_seconds() / 60.0
    field = _PAUSE_FIELD[session.pause_reason]
    column = getattr(PaceSession, field)
    await db.execute(update(PaceSession).where(PaceSession.id == session.id).values(**{field: column + elapsed_min}))
    session.paused_at = None
    session.pause_reason = None
    await db.flush()
    await db.refresh(session)


async def resume_session(db: AsyncSession, session: PaceSession) -> PaceSession:
    if session.status != "paused":
        raise HTTPException(status.HTTP_409_CONFLICT, f"Session is {session.status}, not paused")
    await _finalize_pause(db, session, datetime.now(timezone.utc))
    session.status = "active"
    await db.flush()
    return session


async def stop_session(
    db: AsyncSession, session: PaceSession, *, org_id: uuid.UUID, created_by: uuid.UUID
) -> PaceSession:
    if session.status == "stopped":
        raise HTTPException(status.HTTP_409_CONFLICT, "Session is already stopped")
    now = datetime.now(timezone.utc)
    if session.status == "paused":
        await _finalize_pause(db, session, now)

    planned_min = (now - ensure_utc(session.started_at)).total_seconds() / 60.0
    operating_min = max(0.0, planned_min - session.unplanned_dt_min - session.planned_dt_min - session.other_dt_min)
    good_units, reject_units = await tap_counts(db, session.id)
    total_units = good_units + reject_units

    # other_dt_min is intentionally not a CSV column: it already reduced operating_min
    # above, and the gap it leaves between planned_min and (operating_min + unplanned_dt_min
    # + planned_dt_min) is exactly what the OEE engine's Six Big Losses buckets as
    # "Unclassified Downtime" - no new bucket type needed downstream.
    header = ["line", "operator", "planned_min", "operating_min", "total_units", "good_units",
              "unplanned_dt_min", "planned_dt_min"]
    row = [session.line, session.operator, planned_min, operating_min, total_units, good_units,
           session.unplanned_dt_min, session.planned_dt_min]
    if session.target_rate is not None:
        header.append("target_rate")
        row.append(session.target_rate)
    # Same conditional shape as target_rate above: an unlinked session's CSV
    # keeps exactly the columns it has today, so every shipped OEE dashboard
    # reading these datasets is unaffected, while a linked session gains the one
    # column that lets OEE group by what was actually being made.
    _, product_sku = await linked_job_labels(db, session)
    if session.work_order_id is not None:
        header.append("product")
        row.append(product_sku)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerow(row)
    csv_bytes = buf.getvalue().encode("utf-8")

    dataset = await ds_service.create_dataset(
        db,
        org_id=org_id,
        created_by=created_by,
        name=f"Pace: {session.line} - {session.operator} ({now:%Y-%m-%d %H:%M})",
        filename="pace_session.csv",
        data=csv_bytes,
    )

    session.status = "stopped"
    session.stopped_at = now
    session.dataset_id = dataset.id
    await db.flush()
    return session


async def discard_session(db: AsyncSession, session: PaceSession) -> None:
    await db.delete(session)
    await db.flush()
