from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.services import datasets as ds_service
from app.services import rca_cases as service

router = APIRouter(prefix="/rca-cases", tags=["rca-cases"])


# ─── Schemas ────────────────────────────────────────────────────────────────

class RcaCaseCreate(BaseModel):
    title: str = Field(..., min_length=1)
    problem_statement: str = ""
    dataset_id: uuid.UUID
    owner: str | None = None
    trigger_context: dict = Field(default_factory=dict)


class RcaCaseUpdate(BaseModel):
    title: str | None = None
    problem_statement: str | None = None
    status: str | None = None
    owner: str | None = None


class RcaCaseResponse(BaseModel):
    id: uuid.UUID
    dataset_id: uuid.UUID
    title: str
    problem_statement: str
    status: str
    owner: str | None
    trigger_context: dict
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None

    model_config = {"from_attributes": True}


class RcaNodeCreate(BaseModel):
    parent_id: uuid.UUID | None = None
    text: str = Field(..., min_length=1)
    node_type: str = "cause"
    category: str | None = None
    evidence: list[dict] = Field(default_factory=list)


class RcaNodeUpdate(BaseModel):
    text: str | None = None
    node_type: str | None = None
    status: str | None = None
    category: str | None = None


class RcaNodeResponse(BaseModel):
    id: uuid.UUID
    case_id: uuid.UUID
    parent_id: uuid.UUID | None
    text: str
    node_type: str
    status: str
    category: str | None
    evidence: list[dict]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RcaActionCreate(BaseModel):
    description: str = Field(..., min_length=1)
    node_id: uuid.UUID | None = None
    owner: str | None = None
    due_date: str | None = None


class RcaActionUpdate(BaseModel):
    description: str | None = None
    owner: str | None = None
    due_date: str | None = None
    status: str | None = None


class RcaActionResponse(BaseModel):
    id: uuid.UUID
    case_id: uuid.UUID
    node_id: uuid.UUID | None
    description: str
    owner: str | None
    due_date: str | None
    status: str
    completed_at: datetime | None
    verification: dict | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RcaEventResponse(BaseModel):
    id: uuid.UUID
    event_type: str
    summary: str
    actor: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class RcaEvidenceRequest(BaseModel):
    planned_time_col: str = Field(..., min_length=1)
    operating_time_col: str = Field(..., min_length=1)
    total_units_col: str = Field(..., min_length=1)
    good_units_col: str = Field(..., min_length=1)
    target_rate: float | None = None
    dimension_cols: list[str] = Field(..., min_length=1)
    min_support_rows: int = Field(default=5, ge=1)


class RcaCandidateCauseResponse(BaseModel):
    dimension: str
    label: str
    oee: float
    delta_oee: float
    row_count: int
    total_units: float
    support_pct: float
    confidence: float


class RcaEvidenceResponse(BaseModel):
    overall_oee: float
    overall_availability: float
    overall_performance: float
    overall_quality: float
    candidates: list[RcaCandidateCauseResponse]
    candidates_total: int


class RcaParetoRequest(BaseModel):
    category_col: str = Field(..., min_length=1)
    value_col: str = Field(..., min_length=1)
    top_n: int = Field(default=15, ge=1, le=50)


class RcaParetoBucketResponse(BaseModel):
    label: str
    value: float
    pct_of_total: float
    cumulative_pct: float


class RcaParetoResponse(BaseModel):
    total: float
    buckets: list[RcaParetoBucketResponse]
    buckets_total: int


class RcaTimelineRequest(BaseModel):
    date_col: str = Field(..., min_length=1)
    planned_time_col: str = Field(..., min_length=1)
    operating_time_col: str = Field(..., min_length=1)
    total_units_col: str = Field(..., min_length=1)
    good_units_col: str = Field(..., min_length=1)
    window_days: int = Field(default=3, ge=1, le=14)
    center_date: str | None = None


class RcaTimelineEventResponse(BaseModel):
    date: str
    oee: float
    delta_oee: float
    row_count: int
    is_center: bool


class RcaTimelineResponse(BaseModel):
    overall_oee: float
    center_date: str
    window_days: int
    events: list[RcaTimelineEventResponse]


class RcaActionVerifyRequest(BaseModel):
    planned_time_col: str = Field(..., min_length=1)
    operating_time_col: str = Field(..., min_length=1)
    total_units_col: str = Field(..., min_length=1)
    good_units_col: str = Field(..., min_length=1)
    date_col: str = Field(..., min_length=1)


class RcaTrendEntryResponse(BaseModel):
    kind: str
    key: str
    count: int
    case_ids: list[str]
    case_titles: list[str]


class RcaTrendsResponse(BaseModel):
    total_root_causes: int
    by_category: list[RcaTrendEntryResponse]


def _actor_label(current: CurrentUser) -> str | None:
    return current.user.email


def _wrap_not_found(exc: ValueError) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))


# ─── Case CRUD ──────────────────────────────────────────────────────────────

@router.get("/cases", response_model=list[RcaCaseResponse])
async def list_cases(current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await service.list_cases(db, current.org_id)


@router.get("/cases/{case_id}", response_model=RcaCaseResponse)
async def get_case(case_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        return await service.get_case_or_404(db, current.org_id, case_id)
    except ValueError as exc:
        raise _wrap_not_found(exc) from exc


@router.post("/cases", response_model=RcaCaseResponse, status_code=status.HTTP_201_CREATED)
async def create_case(payload: RcaCaseCreate, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        return await service.create_case(
            db, org_id=current.org_id, created_by=current.id, actor_label=_actor_label(current),
            title=payload.title, problem_statement=payload.problem_statement, dataset_id=payload.dataset_id,
            owner=payload.owner, trigger_context=payload.trigger_context,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.patch("/cases/{case_id}", response_model=RcaCaseResponse)
async def update_case(case_id: uuid.UUID, payload: RcaCaseUpdate, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    patch = payload.model_dump(exclude_unset=True)
    try:
        return await service.update_case(db, current.org_id, case_id, _actor_label(current), patch)
    except ValueError as exc:
        raise _wrap_not_found(exc) from exc


@router.delete("/cases/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_case(case_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        await service.delete_case(db, current.org_id, case_id)
    except ValueError as exc:
        raise _wrap_not_found(exc) from exc


# ─── Cause tree ─────────────────────────────────────────────────────────────

@router.get("/cases/{case_id}/nodes", response_model=list[RcaNodeResponse])
async def list_nodes(case_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        await service.get_case_or_404(db, current.org_id, case_id)
    except ValueError as exc:
        raise _wrap_not_found(exc) from exc
    return await service.list_nodes(db, current.org_id, case_id)


@router.post("/cases/{case_id}/nodes", response_model=RcaNodeResponse, status_code=status.HTTP_201_CREATED)
async def create_node(case_id: uuid.UUID, payload: RcaNodeCreate, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        return await service.create_node(db, current.org_id, case_id, _actor_label(current), **payload.model_dump())
    except ValueError as exc:
        raise _wrap_not_found(exc) from exc


@router.patch("/cases/{case_id}/nodes/{node_id}", response_model=RcaNodeResponse)
async def update_node(case_id: uuid.UUID, node_id: uuid.UUID, payload: RcaNodeUpdate, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    patch = payload.model_dump(exclude_unset=True)
    try:
        return await service.update_node(db, current.org_id, case_id, node_id, _actor_label(current), patch)
    except ValueError as exc:
        raise _wrap_not_found(exc) from exc


@router.delete("/cases/{case_id}/nodes/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_node(case_id: uuid.UUID, node_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        await service.delete_node(db, current.org_id, case_id, node_id, _actor_label(current))
    except ValueError as exc:
        raise _wrap_not_found(exc) from exc


# ─── CAPA actions ───────────────────────────────────────────────────────────

@router.get("/cases/{case_id}/actions", response_model=list[RcaActionResponse])
async def list_actions(case_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        await service.get_case_or_404(db, current.org_id, case_id)
    except ValueError as exc:
        raise _wrap_not_found(exc) from exc
    return await service.list_actions(db, current.org_id, case_id)


@router.post("/cases/{case_id}/actions", response_model=RcaActionResponse, status_code=status.HTTP_201_CREATED)
async def create_action(case_id: uuid.UUID, payload: RcaActionCreate, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        return await service.create_action(db, current.org_id, case_id, current.id, _actor_label(current), **payload.model_dump())
    except ValueError as exc:
        raise _wrap_not_found(exc) from exc


@router.patch("/cases/{case_id}/actions/{action_id}", response_model=RcaActionResponse)
async def update_action(case_id: uuid.UUID, action_id: uuid.UUID, payload: RcaActionUpdate, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    patch = payload.model_dump(exclude_unset=True)
    try:
        return await service.update_action(db, current.org_id, case_id, action_id, current.id, _actor_label(current), patch)
    except ValueError as exc:
        raise _wrap_not_found(exc) from exc


@router.delete("/cases/{case_id}/actions/{action_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_action(case_id: uuid.UUID, action_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        await service.delete_action(db, current.org_id, case_id, action_id, _actor_label(current))
    except ValueError as exc:
        raise _wrap_not_found(exc) from exc


@router.post("/cases/{case_id}/actions/{action_id}/verify", response_model=RcaActionResponse)
async def verify_action(case_id: uuid.UUID, action_id: uuid.UUID, req: RcaActionVerifyRequest, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        case = await service.get_case_or_404(db, current.org_id, case_id)
        action = await service.get_action_or_404(db, current.org_id, case_id, action_id)
    except ValueError as exc:
        raise _wrap_not_found(exc) from exc
    if action.completed_at is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Mark this action Done (with a completion date) before verifying its effectiveness.")

    dataset = await ds_service.get_dataset_or_404(db, current.org_id, case.dataset_id)
    result = service.compute_verification(
        dataset, planned_col=req.planned_time_col, operating_col=req.operating_time_col,
        total_col=req.total_units_col, good_col=req.good_units_col, date_col=req.date_col,
        split_at=action.completed_at,
    )
    patch = {
        "verification": {
            "before_oee": result.before_oee, "after_oee": result.after_oee, "delta_oee": result.delta_oee,
            "before_row_count": result.before_row_count, "after_row_count": result.after_row_count,
            "date_col": result.date_col, "split_at": result.split_at,
        },
        "status": "verified",
    }
    updated = await service.update_action(db, current.org_id, case_id, action_id, current.id, _actor_label(current), patch)
    await service.log_event(
        db, current.org_id, case_id, "action_verified",
        f"Verified '{updated.description}': {result.before_oee}% -> {result.after_oee}% ({result.delta_oee:+.2f}pts)",
        _actor_label(current),
    )
    await db.commit()
    return updated


# ─── Evidence / Pareto / Timeline ───────────────────────────────────────────

@router.post("/cases/{case_id}/evidence", response_model=RcaEvidenceResponse)
async def case_evidence(case_id: uuid.UUID, req: RcaEvidenceRequest, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        case = await service.get_case_or_404(db, current.org_id, case_id)
    except ValueError as exc:
        raise _wrap_not_found(exc) from exc
    dataset = await ds_service.get_dataset_or_404(db, current.org_id, case.dataset_id)
    result = service.compute_evidence(
        dataset, planned_col=req.planned_time_col, operating_col=req.operating_time_col,
        total_col=req.total_units_col, good_col=req.good_units_col, dimension_cols=req.dimension_cols,
        target_rate=req.target_rate, min_support_rows=req.min_support_rows,
    )
    return RcaEvidenceResponse(
        overall_oee=result.overall_oee, overall_availability=result.overall_availability,
        overall_performance=result.overall_performance, overall_quality=result.overall_quality,
        candidates=[RcaCandidateCauseResponse(**vars(c)) for c in result.candidates],
        candidates_total=result.candidates_total,
    )


@router.post("/cases/{case_id}/pareto", response_model=RcaParetoResponse)
async def case_pareto(case_id: uuid.UUID, req: RcaParetoRequest, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        case = await service.get_case_or_404(db, current.org_id, case_id)
    except ValueError as exc:
        raise _wrap_not_found(exc) from exc
    dataset = await ds_service.get_dataset_or_404(db, current.org_id, case.dataset_id)
    result = service.compute_pareto(dataset, category_col=req.category_col, value_col=req.value_col, top_n=req.top_n)
    return RcaParetoResponse(
        total=result.total, buckets=[RcaParetoBucketResponse(**vars(b)) for b in result.buckets],
        buckets_total=result.buckets_total,
    )


@router.post("/cases/{case_id}/timeline", response_model=RcaTimelineResponse)
async def case_timeline(case_id: uuid.UUID, req: RcaTimelineRequest, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        case = await service.get_case_or_404(db, current.org_id, case_id)
    except ValueError as exc:
        raise _wrap_not_found(exc) from exc
    dataset = await ds_service.get_dataset_or_404(db, current.org_id, case.dataset_id)
    result = service.compute_timeline(
        dataset, date_col=req.date_col, planned_col=req.planned_time_col, operating_col=req.operating_time_col,
        total_col=req.total_units_col, good_col=req.good_units_col, window_days=req.window_days, center_date=req.center_date,
    )
    return RcaTimelineResponse(
        overall_oee=result.overall_oee, center_date=result.center_date, window_days=result.window_days,
        events=[RcaTimelineEventResponse(**vars(e)) for e in result.events],
    )


# ─── Taxonomy / trends / events ─────────────────────────────────────────────

@router.get("/taxonomy")
async def get_taxonomy(current: CurrentUser = Depends(get_current_user)) -> dict[str, list[str]]:
    return service.RCA_TAXONOMY


@router.get("/trends", response_model=RcaTrendsResponse)
async def get_trends(current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await service.compute_trends(db, current.org_id)
    return RcaTrendsResponse(
        total_root_causes=result.total_root_causes,
        by_category=[RcaTrendEntryResponse(**vars(e)) for e in result.by_category],
    )


@router.get("/cases/{case_id}/events", response_model=list[RcaEventResponse])
async def list_case_events(case_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        await service.get_case_or_404(db, current.org_id, case_id)
    except ValueError as exc:
        raise _wrap_not_found(exc) from exc
    return await service.list_events(db, current.org_id, case_id)
