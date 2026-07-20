# backend/app/services/automl_retrain.py
"""Event-driven drift check + retrain. app.services.datasets.
overwrite_dataset_file schedules maybe_retrain_for_dataset via
BackgroundTasks whenever a live-refreshed Dataset's content changes (Task 4
wires that trigger; this module owns the decision policy on its own).

Policy (full rationale in docs/superpowers/specs/2026-07-18-automl-drift-
retrain-design.md): only runs with auto_retrain=True are watched. Drift is
checked with the existing PSI-based check_data_drift against each run's own
training-time sample. "high" severity retrains; a quality gate only
promotes the retrain (hands auto_retrain to the child) if its primary
metric is not worse than the parent's; MAX_AUTO_RETRAIN_STREAK consecutive
non-promoted retrains trips a circuit breaker that disables auto_retrain.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session as db_session
from app.middleware.audit import log_event
from app.models.automl_run import AutoMLRun
from app.models.dataset import Dataset
from app.services import automl_engine, automl_runner, automl_storage
from app.services.datasets import dataframe_for

MAX_AUTO_RETRAIN_STREAK = 3
DRIFT_SEVERITY_THRESHOLD = "high"
COOLDOWN = timedelta(hours=1)


async def maybe_retrain_for_dataset(org_id: str, dataset_id: str) -> None:
    async with db_session.async_session_factory() as db:
        await _maybe_retrain(db, uuid.UUID(org_id), uuid.UUID(dataset_id))


async def _maybe_retrain(db: AsyncSession, org_id: uuid.UUID, dataset_id: uuid.UUID) -> None:
    watched = (
        await db.execute(
            select(AutoMLRun).where(
                AutoMLRun.org_id == org_id,
                AutoMLRun.dataset_id == dataset_id,
                AutoMLRun.status == "success",
                AutoMLRun.auto_retrain.is_(True),
            )
        )
    ).scalars().all()
    if not watched:
        return

    dataset = (
        await db.execute(select(Dataset).where(Dataset.id == dataset_id, Dataset.org_id == org_id))
    ).scalar_one_or_none()
    if dataset is None:
        return

    for run in watched:
        await _evaluate_run(db, run, dataset)


async def _evaluate_run(db: AsyncSession, run: AutoMLRun, dataset: Dataset, *, skip_cooldown: bool = False) -> None:
    """skip_cooldown exists only so tests can exercise several consecutive
    evaluations without sleeping an hour between them - production code
    never passes it."""
    if run.auto_retrain_streak >= MAX_AUTO_RETRAIN_STREAK:
        return  # defensive guard - normally the breaker trips inline below,
        # so a still-watched run should never actually reach this streak.

    if not skip_cooldown:
        cutoff = datetime.now(timezone.utc) - COOLDOWN
        recent = (
            await db.execute(
                select(AutoMLRun.id).where(
                    AutoMLRun.org_id == run.org_id,
                    AutoMLRun.dataset_id == run.dataset_id,
                    AutoMLRun.retrained_from_run_id.isnot(None),
                    AutoMLRun.created_at >= cutoff,
                )
            )
        ).first()
        if recent is not None:
            return

    Xs, _ys = automl_storage.load_explain_sample(run.org_id, run.id)
    df_new = dataframe_for(dataset)
    try:
        drift = automl_engine.check_data_drift(run.meta or {}, Xs, df_new)
    except ValueError:
        return  # explain sample missing/unusable - nothing to compare, skip silently

    if drift["overall_severity"] != DRIFT_SEVERITY_THRESHOLD:
        return

    child = await automl_runner.create_run(
        db, org_id=run.org_id, created_by=run.created_by, dataset_id=run.dataset_id,
        name=f"{run.name} (auto-retrain)", task=run.task, target_column=run.target_column,
        feature_columns=run.feature_columns, config=run.config,
        auto_retrain=True, retrained_from_run_id=run.id,
    )
    await db.commit()

    await automl_runner.execute_automl_run(str(child.id), str(run.org_id))

    await db.refresh(child)
    await db.refresh(run)

    old_score = (
        automl_engine.normalize_higher_is_better(automl_engine.AutoMLTask(run.task), run.metric_value)
        if run.metric_value is not None else float("-inf")
    )
    new_score = (
        automl_engine.normalize_higher_is_better(automl_engine.AutoMLTask(child.task), child.metric_value)
        if child.status == "success" and child.metric_value is not None else float("-inf")
    )

    if new_score >= old_score:
        child.auto_retrain = True
        child.auto_retrain_streak = 0
        run.auto_retrain = False
        await log_event(
            db, action="automl.auto_retrain_promoted", org_id=run.org_id, actor_user_id=run.created_by,
            target=str(child.id),
            metadata={"from_run_id": str(run.id), "drift_severity": drift["overall_severity"]},
        )
    else:
        child.auto_retrain = False
        run.auto_retrain_streak += 1
        if run.auto_retrain_streak >= MAX_AUTO_RETRAIN_STREAK:
            run.auto_retrain = False
            await log_event(
                db, action="automl.auto_retrain_circuit_broken", org_id=run.org_id, actor_user_id=run.created_by,
                target=str(run.id),
                metadata={"new_run_id": str(child.id), "streak": run.auto_retrain_streak},
            )
        else:
            await log_event(
                db, action="automl.auto_retrain_rejected", org_id=run.org_id, actor_user_id=run.created_by,
                target=str(child.id),
                metadata={"from_run_id": str(run.id), "drift_severity": drift["overall_severity"]},
            )
    await db.commit()
