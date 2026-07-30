"""Public, unauthenticated ingestion for who's-at-the-machine signals: a
badge/RFID reader posts to /operators/capture directly, and the QR check-in
page (its own public frontend route, see frontend/src/pages/CheckIn.tsx)
calls /checkin/{machine_id}. Neither actor has an app login - see the
identity-capture spec's Tier 1/2 sections. Org scope always comes from
machine_id/badge_id, never a bearer token."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.time import ensure_utc
from app.db.session import get_db
from app.middleware.rate_limit import rate_limit
from app.models.machine import Machine
from app.models.operator import Operator
from app.services.operator_capture import open_capture_event
from app.services.registry_matching import resolve_or_create_operator

# scanned_at window: 5 minutes future tolerates ordinary reader/server clock
# skew; 24 hours past tolerates a reader buffering an offline scan (per the
# identity-capture spec's Tier 1 section) without accepting arbitrary
# garbage/malicious timestamps that could poison a prior event's ended_at.
_SCANNED_AT_MAX_FUTURE = timedelta(minutes=5)
_SCANNED_AT_MAX_PAST = timedelta(hours=24)

router = APIRouter(tags=["operator-capture"])

settings = get_settings()
_capture_rate_limit = rate_limit("operator_capture", settings.rate_limit_operator_capture_per_5min, 300)
_checkin_read_rate_limit = rate_limit("checkin_read", settings.rate_limit_checkin_read_per_5min, 300)
_checkin_write_rate_limit = rate_limit("checkin_write", settings.rate_limit_checkin_write_per_5min, 300)


class CaptureRequest(BaseModel):
    badge_id: str
    machine_id: uuid.UUID
    scanned_at: datetime | None = None


class CaptureResponse(BaseModel):
    operator_id: str
    operator_name: str
    machine_id: str
    started_at: str


class RosterOperatorResponse(BaseModel):
    id: str
    name: str


class CheckinRosterResponse(BaseModel):
    machine_id: str
    machine_name: str
    operators: list[RosterOperatorResponse]


class CheckinRequest(BaseModel):
    operator_id: uuid.UUID | None = None
    # Matches Operator.name's String(255) column - unbounded input here would
    # 500 on insert instead of 422ing at the request boundary.
    name: str | None = Field(default=None, max_length=255)


async def _machine_or_404(db: AsyncSession, machine_id: uuid.UUID, for_update: bool = False) -> Machine:
    query = select(Machine).where(Machine.id == machine_id)
    if for_update:
        # Locks the machine row for the rest of this transaction, so a second
        # concurrent scan on the same machine blocks on open_capture_event's
        # check-then-act instead of racing it into two simultaneously "open"
        # events.
        query = query.with_for_update()
    machine = (await db.execute(query)).scalar_one_or_none()
    if machine is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Machine not found")
    return machine


@router.post(
    "/operators/capture",
    response_model=CaptureResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_capture_rate_limit)],
)
async def capture_badge_scan(body: CaptureRequest, db: AsyncSession = Depends(get_db)):
    machine = await _machine_or_404(db, body.machine_id, for_update=True)
    operator = (
        await db.execute(
            select(Operator).where(Operator.org_id == machine.org_id, Operator.badge_id == body.badge_id)
        )
    ).scalar_one_or_none()
    if operator is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "badge not recognized")

    now = datetime.now(timezone.utc)
    if body.scanned_at is not None:
        scanned_at = ensure_utc(body.scanned_at)
        if scanned_at > now + _SCANNED_AT_MAX_FUTURE or scanned_at < now - _SCANNED_AT_MAX_PAST:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "scanned_at is outside the accepted window")
    else:
        scanned_at = now

    event = await open_capture_event(
        db, org_id=machine.org_id, operator_id=operator.id, machine_id=machine.id,
        source="reader", scanned_at=scanned_at,
    )
    await db.commit()
    return CaptureResponse(
        operator_id=str(operator.id), operator_name=operator.name,
        machine_id=str(machine.id), started_at=event.started_at.isoformat(),
    )


@router.get(
    "/checkin/{machine_id}",
    response_model=CheckinRosterResponse,
    dependencies=[Depends(_checkin_read_rate_limit)],
)
async def checkin_roster(machine_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    machine = await _machine_or_404(db, machine_id)
    rows = (
        await db.execute(
            select(Operator)
            .where(Operator.org_id == machine.org_id, Operator.status == "active")
            .order_by(Operator.name)
        )
    ).scalars().all()
    return CheckinRosterResponse(
        machine_id=str(machine.id),
        machine_name=machine.name,
        operators=[RosterOperatorResponse(id=str(o.id), name=o.name) for o in rows],
    )


@router.post(
    "/checkin/{machine_id}",
    response_model=CaptureResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_checkin_write_rate_limit)],
)
async def checkin_scan(machine_id: uuid.UUID, body: CheckinRequest, db: AsyncSession = Depends(get_db)):
    machine = await _machine_or_404(db, machine_id, for_update=True)
    if body.operator_id is not None:
        operator = (
            await db.execute(
                select(Operator).where(Operator.org_id == machine.org_id, Operator.id == body.operator_id)
            )
        ).scalar_one_or_none()
        if operator is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Operator not found")
    elif body.name and body.name.strip():
        # Not on the roster from this screen is allowed - a new hire's first
        # scan creates the roster row, exactly as dataset auto-discovery does.
        operator = await resolve_or_create_operator(db, machine.org_id, body.name.strip())
    else:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "operator_id or name is required")

    scanned_at = datetime.now(timezone.utc)
    event = await open_capture_event(
        db, org_id=machine.org_id, operator_id=operator.id, machine_id=machine.id,
        source="qr", scanned_at=scanned_at,
    )
    await db.commit()
    return CaptureResponse(
        operator_id=str(operator.id), operator_name=operator.name,
        machine_id=str(machine.id), started_at=event.started_at.isoformat(),
    )
