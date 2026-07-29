from __future__ import annotations

import io
import uuid
from typing import Any

import qrcode
import qrcode.image.svg
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_or_404, require_role
from app.core.config import get_settings
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.machine import Machine
from app.models.work_center import WorkCenter

router = APIRouter(prefix="/machines", tags=["machines"])

_STATUSES = {"active", "inactive", "retired"}


class MachineCreateRequest(BaseModel):
    work_center_id: uuid.UUID
    code: str
    name: str
    mtbf_hours: float | None = None
    mttr_hours: float | None = None


class MachineUpdateRequest(BaseModel):
    name: str | None = None
    status: str | None = None
    mtbf_hours: float | None = None
    mttr_hours: float | None = None
    needs_review: bool | None = None


class MachineResponse(BaseModel):
    id: str
    work_center_id: str
    code: str
    name: str
    status: str
    mtbf_hours: float | None
    mttr_hours: float | None
    needs_review: bool


def _to_response(m: Machine) -> MachineResponse:
    return MachineResponse(
        id=str(m.id), work_center_id=str(m.work_center_id), code=m.code, name=m.name,
        status=m.status, mtbf_hours=m.mtbf_hours, mttr_hours=m.mttr_hours, needs_review=m.needs_review,
    )


@router.get("", response_model=list[MachineResponse])
async def list_machines(
    work_center_id: uuid.UUID | None = Query(None),
    needs_review: bool | None = Query(None),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(Machine).where(Machine.org_id == current.org_id)
    if work_center_id is not None:
        query = query.where(Machine.work_center_id == work_center_id)
    if needs_review is not None:
        query = query.where(Machine.needs_review == needs_review)
    result = await db.execute(query.order_by(Machine.code))
    return [_to_response(m) for m in result.scalars().all()]


@router.post("", response_model=MachineResponse, status_code=status.HTTP_201_CREATED)
async def create_machine(
    body: MachineCreateRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    await get_or_404(db, WorkCenter, current.org_id, body.work_center_id, "Work center not found")
    m = Machine(
        org_id=current.org_id, work_center_id=body.work_center_id, code=body.code, name=body.name,
        mtbf_hours=body.mtbf_hours, mttr_hours=body.mttr_hours,
    )
    db.add(m)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "code already exists in this org") from None

    await log_event(
        db, action="machine.created", org_id=current.org_id, actor_user_id=current.id,
        target=str(m.id), metadata={"code": m.code},
    )
    await db.commit()
    return _to_response(m)


@router.patch("/{machine_id}", response_model=MachineResponse)
async def update_machine(
    machine_id: uuid.UUID,
    body: MachineUpdateRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    m = await get_or_404(db, Machine, current.org_id, machine_id, "Machine not found")
    if body.status is not None and body.status not in _STATUSES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"status must be one of {sorted(_STATUSES)}")

    fields: dict[str, Any] = body.model_dump(exclude_unset=True)
    for key, value in fields.items():
        setattr(m, key, value)

    await log_event(
        db, action="machine.updated", org_id=current.org_id, actor_user_id=current.id,
        target=str(m.id), metadata=fields,
    )
    await db.commit()
    await db.refresh(m)
    return _to_response(m)


@router.get("/{machine_id}/checkin-qr")
async def machine_checkin_qr(
    machine_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """SVG QR code encoding this machine's public check-in URL - see
    app.api.routes.operator_capture for what scanning it does. Generated
    server-side so printing it needs no frontend QR dependency."""
    await get_or_404(db, Machine, current.org_id, machine_id, "Machine not found")
    url = f"{get_settings().frontend_base_url}/checkin/{machine_id}"
    image = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage)
    buffer = io.BytesIO()
    image.save(buffer)
    return Response(content=buffer.getvalue(), media_type="image/svg+xml")
