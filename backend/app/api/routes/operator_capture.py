"""Public, unauthenticated ingestion for who's-at-the-machine signals: a
badge/RFID reader posts to /operators/capture directly, and the QR check-in
page (its own public frontend route, see frontend/src/pages/CheckIn.tsx)
calls /checkin/{machine_id}. Neither actor has an app login - see the
identity-capture spec's Tier 1/2 sections. Org scope always comes from
machine_id/badge_id, never a bearer token."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.machine import Machine
from app.models.operator import Operator
from app.services.operator_capture import open_capture_event
from app.services.registry_matching import resolve_or_create_operator

router = APIRouter(tags=["operator-capture"])


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
    name: str | None = None


async def _machine_or_404(db: AsyncSession, machine_id: uuid.UUID) -> Machine:
    machine = await db.get(Machine, machine_id)
    if machine is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Machine not found")
    return machine


@router.post("/operators/capture", response_model=CaptureResponse, status_code=status.HTTP_201_CREATED)
async def capture_badge_scan(body: CaptureRequest, db: AsyncSession = Depends(get_db)):
    machine = await _machine_or_404(db, body.machine_id)
    operator = (
        await db.execute(
            select(Operator).where(Operator.org_id == machine.org_id, Operator.badge_id == body.badge_id)
        )
    ).scalar_one_or_none()
    if operator is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "badge not recognized")

    scanned_at = body.scanned_at or datetime.now(timezone.utc)
    event = await open_capture_event(
        db, org_id=machine.org_id, operator_id=operator.id, machine_id=machine.id,
        source="reader", scanned_at=scanned_at,
    )
    await db.commit()
    return CaptureResponse(
        operator_id=str(operator.id), operator_name=operator.name,
        machine_id=str(machine.id), started_at=event.started_at.isoformat(),
    )


@router.get("/checkin/{machine_id}", response_model=CheckinRosterResponse)
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


@router.post("/checkin/{machine_id}", response_model=CaptureResponse, status_code=status.HTTP_201_CREATED)
async def checkin_scan(machine_id: uuid.UUID, body: CheckinRequest, db: AsyncSession = Depends(get_db)):
    machine = await _machine_or_404(db, machine_id)
    if body.operator_id is not None:
        operator = (
            await db.execute(
                select(Operator).where(Operator.org_id == machine.org_id, Operator.id == body.operator_id)
            )
        ).scalar_one_or_none()
        if operator is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Operator not found")
    elif body.name:
        # Not on the roster from this screen is allowed - a new hire's first
        # scan creates the roster row, exactly as dataset auto-discovery does.
        operator = await resolve_or_create_operator(db, machine.org_id, body.name)
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
