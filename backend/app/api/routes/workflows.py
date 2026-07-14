"""Workflows - a tenant-scoped DAG builder + run history. Validation
(app.services.workflow_dag) runs synchronously here so a bad graph fails fast
with a 400; actual execution happens in app.services.workflow_runner, kicked
off via FastAPI BackgroundTasks on run."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_or_404
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.workflow import Workflow, WorkflowRun, WorkflowRunEvent
from app.services import workflow_dag as dag_service
from app.services.workflow_runner import execute_workflow_run

router = APIRouter(prefix="/workflows", tags=["workflows"])


class WorkflowWriteRequest(BaseModel):
    name: str
    description: str | None = None
    steps: list[dict] = []
    failure_policy: dag_service.FailurePolicy = "fail_fast"


class WorkflowResponse(BaseModel):
    id: str
    name: str
    description: str | None
    steps: list[dict]
    failure_policy: str
    created_at: str
    updated_at: str


class RunResponse(BaseModel):
    id: str
    workflow_id: str
    status: str
    started_at: str | None
    finished_at: str | None
    created_at: str


class RunEventResponse(BaseModel):
    id: str
    step_id: str | None
    event_type: str
    payload: dict
    created_at: str


class RunDetailResponse(RunResponse):
    events: list[RunEventResponse]


class RunWithWorkflowResponse(RunResponse):
    workflow_name: str


def _workflow_to_response(w: Workflow) -> WorkflowResponse:
    return WorkflowResponse(
        id=str(w.id),
        name=w.name,
        description=w.description,
        steps=w.steps,
        failure_policy=w.failure_policy,
        created_at=w.created_at.isoformat(),
        updated_at=w.updated_at.isoformat(),
    )


def _run_to_response(r: WorkflowRun) -> RunResponse:
    return RunResponse(
        id=str(r.id),
        workflow_id=str(r.workflow_id),
        status=r.status,
        started_at=r.started_at.isoformat() if r.started_at else None,
        finished_at=r.finished_at.isoformat() if r.finished_at else None,
        created_at=r.created_at.isoformat(),
    )


def _validate_or_400(steps: list[dict]) -> None:
    try:
        dag_service.validate_dag(steps)
    except dag_service.DagError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get("", response_model=list[WorkflowResponse])
async def list_workflows(current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(select(Workflow).where(Workflow.org_id == current.org_id).order_by(Workflow.created_at.desc()))
    ).scalars().all()
    return [_workflow_to_response(w) for w in rows]


@router.post("", response_model=WorkflowResponse, status_code=status.HTTP_201_CREATED)
async def create_workflow(
    req: WorkflowWriteRequest, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    _validate_or_400(req.steps)
    workflow = Workflow(
        org_id=current.org_id,
        name=req.name,
        description=req.description,
        steps=req.steps,
        failure_policy=req.failure_policy,
        created_by=current.id,
    )
    db.add(workflow)
    await log_event(
        db, action="workflow.created", org_id=current.org_id, actor_user_id=current.id, metadata={"name": req.name}
    )
    await db.commit()
    await db.refresh(workflow)
    return _workflow_to_response(workflow)


@router.get("/runs", response_model=list[RunWithWorkflowResponse])
async def list_all_runs(
    status: str | None = None,
    limit: int = 50,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Run history across every workflow in the org - the audit view a plant
    manager checks, as opposed to /{workflow_id}/runs which is scoped to one
    workflow's builder page. Declared before /{workflow_id} below so "runs"
    isn't swallowed by that route's plain-string path converter."""
    limit = max(1, min(limit, 200))
    stmt = (
        select(WorkflowRun, Workflow.name)
        .join(Workflow, Workflow.id == WorkflowRun.workflow_id)
        .where(WorkflowRun.org_id == current.org_id)
        .order_by(WorkflowRun.created_at.desc())
        .limit(limit)
    )
    if status:
        stmt = stmt.where(WorkflowRun.status == status)
    rows = (await db.execute(stmt)).all()
    return [
        RunWithWorkflowResponse(**_run_to_response(r).model_dump(), workflow_name=name)
        for r, name in rows
    ]


@router.get("/{workflow_id}", response_model=WorkflowResponse)
async def get_workflow(
    workflow_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    workflow = await get_or_404(db, Workflow, current.org_id, workflow_id, "Workflow not found")
    return _workflow_to_response(workflow)


@router.put("/{workflow_id}", response_model=WorkflowResponse)
async def update_workflow(
    workflow_id: uuid.UUID,
    req: WorkflowWriteRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    workflow = await get_or_404(db, Workflow, current.org_id, workflow_id, "Workflow not found")
    _validate_or_400(req.steps)
    workflow.name = req.name
    workflow.description = req.description
    workflow.steps = req.steps
    workflow.failure_policy = req.failure_policy
    await log_event(
        db, action="workflow.updated", org_id=current.org_id, actor_user_id=current.id, target=str(workflow.id)
    )
    await db.commit()
    await db.refresh(workflow)
    return _workflow_to_response(workflow)


@router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workflow(
    workflow_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    workflow = await get_or_404(db, Workflow, current.org_id, workflow_id, "Workflow not found")
    await db.delete(workflow)
    await log_event(
        db, action="workflow.deleted", org_id=current.org_id, actor_user_id=current.id, target=str(workflow_id)
    )
    await db.commit()


@router.post("/{workflow_id}/run", response_model=RunResponse, status_code=status.HTTP_202_ACCEPTED)
async def run_workflow(
    workflow_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    workflow = await get_or_404(db, Workflow, current.org_id, workflow_id, "Workflow not found")
    _validate_or_400(workflow.steps)  # steps may have been valid at save time but re-check before spending a run

    run = WorkflowRun(org_id=current.org_id, workflow_id=workflow.id, status="queued", triggered_by=current.id)
    db.add(run)
    await db.flush()
    db.add(WorkflowRunEvent(org_id=current.org_id, run_id=run.id, event_type="run_started"))
    await log_event(
        db, action="workflow.run", org_id=current.org_id, actor_user_id=current.id, target=str(workflow.id)
    )
    await db.commit()
    await db.refresh(run)

    background_tasks.add_task(execute_workflow_run, str(run.id), str(current.org_id))
    return _run_to_response(run)


@router.get("/{workflow_id}/runs", response_model=list[RunResponse])
async def list_runs(
    workflow_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    await get_or_404(db, Workflow, current.org_id, workflow_id, "Workflow not found")
    rows = (
        await db.execute(
            select(WorkflowRun)
            .where(WorkflowRun.workflow_id == workflow_id, WorkflowRun.org_id == current.org_id)
            .order_by(WorkflowRun.created_at.desc())
        )
    ).scalars().all()
    return [_run_to_response(r) for r in rows]


@router.get("/{workflow_id}/runs/{run_id}", response_model=RunDetailResponse)
async def get_run(
    workflow_id: uuid.UUID,
    run_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    run = await get_or_404(db, WorkflowRun, current.org_id, run_id, "Run not found", WorkflowRun.workflow_id == workflow_id)
    events = (
        await db.execute(
            select(WorkflowRunEvent)
            .where(WorkflowRunEvent.run_id == run.id, WorkflowRunEvent.org_id == current.org_id)
            .order_by(WorkflowRunEvent.created_at)
        )
    ).scalars().all()
    return RunDetailResponse(
        **_run_to_response(run).model_dump(),
        events=[
            RunEventResponse(
                id=str(e.id), step_id=e.step_id, event_type=e.event_type, payload=e.payload,
                created_at=e.created_at.isoformat(),
            )
            for e in events
        ],
    )
