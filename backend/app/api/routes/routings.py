from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_or_404, require_role
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.bom import Item
from app.models.routing import Routing, RoutingStep
from app.models.work_center import WorkCenter

router = APIRouter(prefix="/routings", tags=["routings"])


class RoutingStepRequest(BaseModel):
    sequence: int
    work_center_id: uuid.UUID
    cycle_time_seconds: float | None = None
    setup_time_seconds: float | None = None


class RoutingStepResponse(BaseModel):
    id: str
    sequence: int
    work_center_id: str
    cycle_time_seconds: float | None
    setup_time_seconds: float | None


class RoutingResponse(BaseModel):
    id: str
    item_id: str
    version: int
    status: str
    steps: list[RoutingStepResponse]


def _step_response(s: RoutingStep) -> RoutingStepResponse:
    return RoutingStepResponse(
        id=str(s.id), sequence=s.sequence, work_center_id=str(s.work_center_id),
        cycle_time_seconds=s.cycle_time_seconds, setup_time_seconds=s.setup_time_seconds,
    )


async def _routing_response(db: AsyncSession, routing: Routing) -> RoutingResponse:
    steps = (
        await db.execute(
            select(RoutingStep).where(RoutingStep.routing_id == routing.id).order_by(RoutingStep.sequence)
        )
    ).scalars().all()
    return RoutingResponse(
        id=str(routing.id), item_id=str(routing.item_id), version=routing.version, status=routing.status,
        steps=[_step_response(s) for s in steps],
    )


@router.get("", response_model=list[RoutingResponse])
async def list_routings(
    item_id: uuid.UUID | None = Query(None),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(Routing).where(Routing.org_id == current.org_id)
    if item_id is not None:
        query = query.where(Routing.item_id == item_id)
    result = await db.execute(query.order_by(Routing.item_id, Routing.version))
    return [await _routing_response(db, r) for r in result.scalars().all()]


@router.post("", response_model=RoutingResponse, status_code=status.HTTP_201_CREATED)
async def create_routing(
    item_id: uuid.UUID,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    await get_or_404(db, Item, current.org_id, item_id, "Item not found")
    max_version = await db.scalar(
        select(func.max(Routing.version)).where(Routing.org_id == current.org_id, Routing.item_id == item_id)
    )
    routing = Routing(org_id=current.org_id, item_id=item_id, version=(max_version or 0) + 1, status="draft")
    db.add(routing)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Could not allocate a routing version, please retry") from None

    await log_event(
        db, action="routing.created", org_id=current.org_id, actor_user_id=current.id,
        target=str(routing.id), metadata={"item_id": str(item_id), "version": routing.version},
    )
    await db.commit()
    return await _routing_response(db, routing)


@router.post("/{routing_id}/steps", response_model=RoutingResponse)
async def replace_routing_steps(
    routing_id: uuid.UUID,
    steps: list[RoutingStepRequest],
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    routing = await get_or_404(db, Routing, current.org_id, routing_id, "Routing not found")
    if routing.status != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only draft routings can be edited")
    if not steps:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A routing needs at least one step")

    sequences = [s.sequence for s in steps]
    if len(set(sequences)) != len(sequences):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Step sequence numbers must be unique")

    for s in steps:
        if await get_or_404(db, WorkCenter, current.org_id, s.work_center_id, "Work center not found") is None:
            pass  # get_or_404 raises internally; unreachable, kept for clarity

    await db.execute(RoutingStep.__table__.delete().where(RoutingStep.routing_id == routing.id))
    for s in steps:
        db.add(RoutingStep(
            org_id=current.org_id, routing_id=routing.id, sequence=s.sequence,
            work_center_id=s.work_center_id, cycle_time_seconds=s.cycle_time_seconds,
            setup_time_seconds=s.setup_time_seconds,
        ))

    await log_event(
        db, action="routing.steps_replaced", org_id=current.org_id, actor_user_id=current.id,
        target=str(routing.id), metadata={"step_count": len(steps)},
    )
    await db.commit()
    await db.refresh(routing)
    return await _routing_response(db, routing)


@router.post("/{routing_id}/activate", response_model=RoutingResponse)
async def activate_routing(
    routing_id: uuid.UUID,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    routing = await get_or_404(db, Routing, current.org_id, routing_id, "Routing not found")
    if routing.status != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only draft routings can be activated")
    step_count = await db.scalar(select(func.count()).where(RoutingStep.routing_id == routing.id))
    if not step_count:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A routing needs at least one step before activation")

    # Lock every Routing row for this item for the rest of this transaction so two
    # concurrent activations can't both pass the status check before either
    # commits - e.g., two draft routings A and B for the same item could otherwise
    # both pass the draft check and both archive/activate, creating two active
    # routings. A second activate() for this item now blocks here until the first
    # one commits or rolls back.
    await db.execute(
        select(Routing.id)
        .where(Routing.org_id == current.org_id, Routing.item_id == routing.item_id)
        .with_for_update()
    )

    await db.execute(
        Routing.__table__.update()
        .where(Routing.org_id == current.org_id, Routing.item_id == routing.item_id, Routing.status == "active")
        .values(status="archived")
    )
    routing.status = "active"

    await log_event(
        db, action="routing.activated", org_id=current.org_id, actor_user_id=current.id, target=str(routing.id),
    )
    await db.commit()
    await db.refresh(routing)
    return await _routing_response(db, routing)
