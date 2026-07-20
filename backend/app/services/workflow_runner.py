"""Runs a workflow's DAG. `execute_workflow_run` is scheduled via FastAPI
BackgroundTasks from the /run route - it opens its own DB session (the
request's session is closed once the response is sent) so it's also directly
callable from tests without any background infra running - see
backend/tests/test_workflows.py.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session as db_session
from app.models.workflow import Workflow, WorkflowRun, WorkflowRunEvent
from app.services import workflow_dag as dag_service
from app.services.workflow_steps import StepContext, StepError, run_step


async def _emit(
    db: AsyncSession, org_id: uuid.UUID, run_id: uuid.UUID, event_type: str, *, step_id: str | None = None, payload: dict | None = None
) -> None:
    db.add(
        WorkflowRunEvent(
            org_id=org_id, run_id=run_id, step_id=step_id, event_type=event_type, payload=payload or {}
        )
    )
    await db.flush()


async def _run_one_step(
    db: AsyncSession, org_id: uuid.UUID, created_by: uuid.UUID, step, upstream: dict[str, dict]
) -> tuple[str, dict | None, str | None]:
    """Returns (step_id, output, error) - errors are captured per-step, never
    raised, so one bad step doesn't take down the whole asyncio.gather."""
    ctx = StepContext(db=db, org_id=org_id, created_by=created_by, config=step.config, upstream=upstream)
    try:
        output = await run_step(step.type, ctx)
        return step.id, output, None
    except StepError as exc:
        return step.id, None, str(exc)
    except Exception as exc:  # noqa: BLE001 - unexpected step failure, recorded per-step not raised
        return step.id, None, f"Unexpected error: {exc}"


async def execute_workflow_run(run_id: str, org_id: str) -> None:
    async with db_session.async_session_factory() as db:
        await _execute(db, uuid.UUID(run_id), uuid.UUID(org_id))


async def _execute(db: AsyncSession, run_id: uuid.UUID, org_id: uuid.UUID) -> None:
    run = (
        await db.execute(select(WorkflowRun).where(WorkflowRun.id == run_id, WorkflowRun.org_id == org_id))
    ).scalar_one_or_none()
    if run is None:
        return
    workflow = (
        await db.execute(select(Workflow).where(Workflow.id == run.workflow_id, Workflow.org_id == org_id))
    ).scalar_one_or_none()
    if workflow is None:
        run.status = "failed"
        run.finished_at = datetime.now(timezone.utc)
        await db.commit()
        return

    steps = dag_service.validate_dag(workflow.steps)
    layers = dag_service.topo_layers(steps)

    run.status = "running"
    run.started_at = datetime.now(timezone.utc)
    await db.flush()

    outputs: dict[str, dict] = {}
    failed_ids: set[str] = set()
    skipped_ids: set[str] = set()

    for layer in layers:
        runnable = []
        for step in layer:
            # "continue" tries every step regardless of an upstream failure - a
            # step that actually needs the missing output fails on its own
            # (e.g. StepError from _upstream_rows), it isn't pre-emptively
            # skipped here. fail_fast never reaches a later layer anyway (it
            # breaks below before one starts); skip_dependents keeps this
            # per-layer skip as before.
            if workflow.failure_policy != "continue" and set(step.depends_on) & (failed_ids | skipped_ids):
                skipped_ids.add(step.id)
                await _emit(db, org_id, run_id, "node_skipped", step_id=step.id)
                continue
            runnable.append(step)

        if not runnable:
            continue

        for step in runnable:
            await _emit(db, org_id, run_id, "node_started", step_id=step.id)

        upstream_by_step = {
            step.id: {dep: outputs[dep] for dep in step.depends_on if dep in outputs} for step in runnable
        }
        results = await asyncio.gather(
            *(_run_one_step(db, org_id, run.triggered_by, step, upstream_by_step[step.id]) for step in runnable)
        )

        for step_id, output, error in results:
            if error is not None:
                failed_ids.add(step_id)
                await _emit(
                    db, org_id, run_id, "node_finished", step_id=step_id, payload={"status": "failed", "error": error}
                )
            else:
                outputs[step_id] = output or {}
                await _emit(db, org_id, run_id, "node_finished", step_id=step_id, payload={"status": "success"})

        if failed_ids and workflow.failure_policy == "fail_fast":
            break
        if failed_ids and workflow.failure_policy == "skip_dependents":
            skipped_ids |= dag_service.descendants(steps, failed_ids) - skipped_ids

    total = len(steps)
    if not failed_ids and not skipped_ids:
        run.status = "success"
    elif (failed_ids and workflow.failure_policy == "fail_fast") or len(failed_ids) + len(skipped_ids) == total:
        run.status = "failed"
    else:
        run.status = "partial"
    run.finished_at = datetime.now(timezone.utc)
    await _emit(db, org_id, run_id, "run_finished", payload={"status": run.status})
    await db.commit()
