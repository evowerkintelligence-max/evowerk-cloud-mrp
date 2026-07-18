from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.services import cbm as service

router = APIRouter(prefix="/cbm", tags=["cbm"])


@router.get("/fleet")
async def fleet_dashboard(current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    machines = await service.fleet_dashboard(db, current.org_id)
    return {"machines": machines}


@router.get("/machines/{machine}")
async def machine_detail(machine: str, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await service.machine_detail(db, current.org_id, machine)


@router.post("/anomalies/{anomaly_id}/acknowledge")
async def acknowledge_anomaly(
    anomaly_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    anomaly = await service.acknowledge_anomaly(db, current.org_id, anomaly_id)
    if anomaly is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Anomaly not found")
    return {"id": str(anomaly.id), "acknowledged_at": anomaly.acknowledged_at.isoformat()}
