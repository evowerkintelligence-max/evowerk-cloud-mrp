# backend/app/api/routes/pace_tracking.py
"""Live tap-tracking session lifecycle - see app.services.pace_tracking for
the state machine and materialization logic."""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_or_404
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.pace_tracking import PaceSession
from app.models.work_order import WorkOrder
from app.services import pace_tracking as pace_service

router = APIRouter(prefix="/pace", tags=["pace-tracking"])

_TAP_KINDS = {"good", "reject"}
_PAUSE_REASONS = {"breakdown", "setup", "other"}


class PaceSessionCreateRequest(BaseModel):
    line: str = Field(..., min_length=1)
    operator: str = Field(..., min_length=1)
    target_rate: Optional[float] = Field(default=None, gt=0)
    # Optional: the free-text line/operator session is still the whole feature
    # for a shop not running work orders.
    work_order_id: Optional[uuid.UUID] = None


class PaceTapRequest(BaseModel):
    kind: str
    reason: Optional[str] = None


class PacePauseRequest(BaseModel):
    reason: str


class PaceSessionResponse(BaseModel):
    id: str
    line: str
    operator: str
    target_rate: Optional[float]
    status: str
    pause_reason: Optional[str]
    started_at: str
    stopped_at: Optional[str]
    dataset_id: Optional[str]
    good_count: int
    reject_count: int
    reject_reasons: dict[str, int]
    unplanned_dt_min: float
    planned_dt_min: float
    other_dt_min: float
    work_order_id: Optional[str]
    # Both null unless the session is linked. They ride the response so the UI
    # can say what a session is running without a second round trip.
    wo_number: Optional[int]
    product_sku: Optional[str]


async def _to_response(db: AsyncSession, session: PaceSession) -> PaceSessionResponse:
    good, reject = await pace_service.tap_counts(db, session.id)
    reasons = await pace_service.reason_counts(db, session.id)
    wo_number, product_sku = await pace_service.linked_job_labels(db, session)
    return PaceSessionResponse(
        id=str(session.id),
        line=session.line,
        operator=session.operator,
        target_rate=session.target_rate,
        status=session.status,
        pause_reason=session.pause_reason,
        started_at=session.started_at.isoformat(),
        stopped_at=session.stopped_at.isoformat() if session.stopped_at else None,
        dataset_id=str(session.dataset_id) if session.dataset_id else None,
        good_count=good,
        reject_count=reject,
        reject_reasons=reasons,
        unplanned_dt_min=session.unplanned_dt_min,
        planned_dt_min=session.planned_dt_min,
        other_dt_min=session.other_dt_min,
        work_order_id=str(session.work_order_id) if session.work_order_id else None,
        wo_number=wo_number,
        product_sku=product_sku,
    )


async def _get_session(db: AsyncSession, current: CurrentUser, session_id: uuid.UUID) -> PaceSession:
    return await get_or_404(db, PaceSession, current.org_id, session_id, "Pace session not found")


@router.post("/sessions", response_model=PaceSessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    req: PaceSessionCreateRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if req.work_order_id is not None:
        # Checked here rather than trusting the FK: a cross-org id would
        # otherwise be accepted and leak that org's product into this org's
        # dataset.
        job = await db.scalar(
            select(WorkOrder).where(
                WorkOrder.org_id == current.org_id, WorkOrder.id == req.work_order_id
            )
        )
        if job is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "job not found")

    session = await pace_service.start_session(
        db, org_id=current.org_id, created_by=current.id,
        line=req.line, operator=req.operator, target_rate=req.target_rate,
        work_order_id=req.work_order_id,
    )
    await db.commit()
    await db.refresh(session)
    return await _to_response(db, session)


@router.get("/sessions", response_model=list[PaceSessionResponse])
async def list_sessions(current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            select(PaceSession).where(PaceSession.org_id == current.org_id).order_by(PaceSession.started_at.desc())
        )
    ).scalars().all()
    return [await _to_response(db, s) for s in rows]


@router.get("/sessions/{session_id}", response_model=PaceSessionResponse)
async def get_session(
    session_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    session = await _get_session(db, current, session_id)
    return await _to_response(db, session)


@router.post("/sessions/{session_id}/tap", response_model=PaceSessionResponse)
async def tap_session(
    session_id: uuid.UUID,
    req: PaceTapRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if req.kind not in _TAP_KINDS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"kind must be one of {sorted(_TAP_KINDS)}")
    session = await _get_session(db, current, session_id)
    await pace_service.record_tap(db, session, req.kind, req.reason)
    await db.commit()
    await db.refresh(session)
    return await _to_response(db, session)


@router.post("/sessions/{session_id}/pause", response_model=PaceSessionResponse)
async def pause_session_route(
    session_id: uuid.UUID,
    req: PacePauseRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if req.reason not in _PAUSE_REASONS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"reason must be one of {sorted(_PAUSE_REASONS)}")
    session = await _get_session(db, current, session_id)
    await pace_service.pause_session(db, session, req.reason)
    await db.commit()
    await db.refresh(session)
    return await _to_response(db, session)


@router.post("/sessions/{session_id}/resume", response_model=PaceSessionResponse)
async def resume_session_route(
    session_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    session = await _get_session(db, current, session_id)
    await pace_service.resume_session(db, session)
    await db.commit()
    await db.refresh(session)
    return await _to_response(db, session)


@router.post("/sessions/{session_id}/stop", response_model=PaceSessionResponse)
async def stop_session_route(
    session_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    session = await _get_session(db, current, session_id)
    await pace_service.stop_session(db, session, org_id=current.org_id, created_by=current.id)
    await log_event(
        db, action="pace_session.stopped", org_id=current.org_id, actor_user_id=current.id,
        target=str(session.id), metadata={"dataset_id": str(session.dataset_id)},
    )
    await db.commit()
    await db.refresh(session)
    return await _to_response(db, session)


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def discard_session_route(
    session_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    session = await _get_session(db, current, session_id)
    if session.created_by != current.id and current.role != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the session's creator or an owner can discard it")
    await pace_service.discard_session(db, session)
    await log_event(
        db, action="pace_session.discarded", org_id=current.org_id, actor_user_id=current.id, target=str(session_id)
    )
    await db.commit()
