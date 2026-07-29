# backend/app/api/routes/operators.py
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_or_404, require_role
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.operator import Operator
from app.services.registry_matching import FUZZY_MATCH_THRESHOLD, normalized_similarity

router = APIRouter(prefix="/operators", tags=["operators"])

_STATUSES = {"active", "inactive"}


class OperatorCreateRequest(BaseModel):
    name: str
    code: str | None = None
    badge_id: str | None = None


class OperatorUpdateRequest(BaseModel):
    name: str | None = None
    code: str | None = None
    badge_id: str | None = None
    status: str | None = None
    needs_review: bool | None = None


class OperatorMergeRequest(BaseModel):
    target_operator_id: uuid.UUID


class OperatorResponse(BaseModel):
    id: str
    name: str
    code: str | None
    badge_id: str | None
    status: str
    needs_review: bool


def _to_response(o: Operator) -> OperatorResponse:
    return OperatorResponse(
        id=str(o.id), name=o.name, code=o.code, badge_id=o.badge_id,
        status=o.status, needs_review=o.needs_review,
    )


@router.get("", response_model=list[OperatorResponse])
async def list_operators(
    needs_review: bool | None = Query(None),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(Operator).where(Operator.org_id == current.org_id)
    if needs_review is not None:
        query = query.where(Operator.needs_review == needs_review)
    result = await db.execute(query.order_by(Operator.name))
    return [_to_response(o) for o in result.scalars().all()]


@router.post("", response_model=OperatorResponse, status_code=status.HTTP_201_CREATED)
async def create_operator(
    body: OperatorCreateRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    target = body.name.strip()
    # Manual entry is still free text a human typed - a near-duplicate is as
    # likely a typo here as it is via dataset auto-discovery, so it gets the
    # same needs_review flag (not the exact-match reuse resolve_or_create_operator
    # does, since POST must 409 on an exact duplicate rather than silently
    # return the existing row).
    others = (await db.execute(select(Operator).where(Operator.org_id == current.org_id))).scalars().all()
    needs_review = any(normalized_similarity(target, o.name) >= FUZZY_MATCH_THRESHOLD for o in others)
    o = Operator(
        org_id=current.org_id, name=target, normalized_name=target.casefold(),
        code=body.code, badge_id=body.badge_id, needs_review=needs_review,
    )
    db.add(o)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "name or badge_id already exists in this org") from None

    await log_event(
        db, action="operator.created", org_id=current.org_id, actor_user_id=current.id,
        target=str(o.id), metadata={"name": o.name},
    )
    await db.commit()
    return _to_response(o)


@router.patch("/{operator_id}", response_model=OperatorResponse)
async def update_operator(
    operator_id: uuid.UUID,
    body: OperatorUpdateRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    o = await get_or_404(db, Operator, current.org_id, operator_id, "Operator not found")
    if body.status is not None and body.status not in _STATUSES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"status must be one of {sorted(_STATUSES)}")

    fields: dict[str, Any] = body.model_dump(exclude_unset=True)
    if "name" in fields and fields["name"] is not None:
        fields["normalized_name"] = fields["name"].strip().casefold()
        fields["name"] = fields["name"].strip()
    for key, value in fields.items():
        setattr(o, key, value)

    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "name or badge_id already exists in this org") from None

    await log_event(
        db, action="operator.updated", org_id=current.org_id, actor_user_id=current.id,
        target=str(o.id), metadata=fields,
    )
    await db.commit()
    await db.refresh(o)
    return _to_response(o)


@router.post("/{operator_id}/merge", response_model=OperatorResponse)
async def merge_operator(
    operator_id: uuid.UUID,
    body: OperatorMergeRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    if operator_id == body.target_operator_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "cannot merge an operator into itself")

    source = await get_or_404(db, Operator, current.org_id, operator_id, "Operator not found")
    target = await get_or_404(db, Operator, current.org_id, body.target_operator_id, "Target operator not found")

    # ponytail: no CaptureEvent/WorkOrder.assigned_operator_id exist yet to
    # repoint - identity-capture spec adds that repointing here once those
    # tables land.
    await db.delete(source)
    await log_event(
        db, action="operator.merged", org_id=current.org_id, actor_user_id=current.id,
        target=str(target.id), metadata={"source_id": str(source.id)},
    )
    await db.commit()
    await db.refresh(target)
    return _to_response(target)


@router.delete("/{operator_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_operator(
    operator_id: uuid.UUID,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    o = await get_or_404(db, Operator, current.org_id, operator_id, "Operator not found")
    # ponytail: no CaptureEvent/WorkOrder.assigned_operator_id exist yet to
    # block on - identity-capture spec adds the 409 guard here once those
    # tables land.
    await db.delete(o)
    await log_event(
        db, action="operator.deleted", org_id=current.org_id, actor_user_id=current.id, target=str(o.id),
    )
    await db.commit()
