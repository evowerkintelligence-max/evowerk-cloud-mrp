# backend/app/api/routes/downtime_tracking.py
"""Live downtime-board endpoints - see app.services.downtime_tracking for
the lifecycle and materialization logic."""
from __future__ import annotations

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_or_404
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.downtime_tracking import DowntimeIncident
from app.services import downtime_tracking as dt_service

router = APIRouter(prefix="/downtime-tracking", tags=["downtime-tracking"])

_REASONS = {"breakdown", "changeover", "material_shortage", "planned_maintenance", "quality_hold", "other"}


class IncidentStartRequest(BaseModel):
    machine: str = Field(..., min_length=1)
    reason: str
    shift: str = Field(..., min_length=1)


class IncidentResponse(BaseModel):
    id: str
    machine: str
    reason: str
    shift: str
    started_at: str
    ended_at: Optional[str]


class IncidentEndResponse(BaseModel):
    incident: IncidentResponse
    live_dataset_id: str


class LiveDatasetResponse(BaseModel):
    dataset_id: Optional[str]


def _to_response(incident: DowntimeIncident) -> IncidentResponse:
    return IncidentResponse(
        id=str(incident.id),
        machine=incident.machine,
        reason=incident.reason,
        shift=incident.shift,
        started_at=incident.started_at.isoformat(),
        ended_at=incident.ended_at.isoformat() if incident.ended_at else None,
    )


async def _get_incident(db: AsyncSession, current: CurrentUser, incident_id: uuid.UUID) -> DowntimeIncident:
    return await get_or_404(db, DowntimeIncident, current.org_id, incident_id, "Incident not found")


@router.post("/incidents", response_model=IncidentResponse, status_code=status.HTTP_201_CREATED)
async def start_incident_route(
    req: IncidentStartRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if req.reason not in _REASONS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"reason must be one of {sorted(_REASONS)}")
    incident = await dt_service.start_incident(
        db, org_id=current.org_id, started_by=current.id, machine=req.machine, reason=req.reason, shift=req.shift,
    )
    await db.commit()
    await db.refresh(incident)
    return _to_response(incident)


@router.get("/incidents", response_model=List[IncidentResponse])
async def list_open_incidents_route(
    open_: bool = Query(True, alias="open"),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not open_:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only open=true is supported")
    rows = await dt_service.list_open(db, current.org_id)
    return [_to_response(r) for r in rows]


@router.post("/incidents/{incident_id}/end", response_model=IncidentEndResponse)
async def end_incident_route(
    incident_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    incident = await _get_incident(db, current, incident_id)
    dataset_id = await dt_service.end_incident(db, incident, org_id=current.org_id, created_by=current.id)
    await db.commit()
    await db.refresh(incident)
    return IncidentEndResponse(incident=_to_response(incident), live_dataset_id=str(dataset_id))


@router.delete("/incidents/{incident_id}", status_code=status.HTTP_204_NO_CONTENT)
async def discard_incident_route(
    incident_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    incident = await _get_incident(db, current, incident_id)
    if incident.started_by != current.id and current.role != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the incident's reporter or an owner can discard it")
    await dt_service.discard_incident(db, incident)
    await log_event(
        db, action="downtime_incident.discarded", org_id=current.org_id,
        actor_user_id=current.id, target=str(incident_id),
    )
    await db.commit()


@router.get("/machines", response_model=List[str])
async def list_machines_route(
    current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    return await dt_service.list_machines(db, current.org_id)


@router.get("/shifts", response_model=List[str])
async def list_shifts_route(
    current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    return await dt_service.list_shifts(db, current.org_id)


@router.get("/live-dataset", response_model=LiveDatasetResponse)
async def live_dataset_route(
    current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    dataset_id = await dt_service.get_live_dataset_id(db, current.org_id)
    return LiveDatasetResponse(dataset_id=str(dataset_id) if dataset_id else None)
