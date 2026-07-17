"""Runs an AutoML training job. `execute_automl_run` is scheduled via FastAPI
BackgroundTasks from the /automl/runs route - it opens its own DB session (the
request's session is closed once the response is sent), mirroring
app/services/workflow_runner.py.

Unlike workflow steps (sub-second SQL/filter ops, safe to await directly), the
actual model fit (RandomizedSearchCV.fit) is CPU-bound and can run tens of
seconds to minutes. This app has no separate worker process, so that call is
wrapped in asyncio.to_thread so it doesn't block the event loop for other
tenants' requests while it runs.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import joblib
import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session as db_session
from app.models.automl_run import AutoMLRun
from app.models.dataset import Dataset
from app.services import automl_engine, automl_explain_nl, automl_storage, automl_templates
from app.services.datasets import dataframe_for


async def execute_automl_run(run_id: str, org_id: str) -> None:
    async with db_session.async_session_factory() as db:
        await _execute(db, uuid.UUID(run_id), uuid.UUID(org_id))


async def _execute(db: AsyncSession, run_id: uuid.UUID, org_id: uuid.UUID) -> None:
    run = (
        await db.execute(select(AutoMLRun).where(AutoMLRun.id == run_id, AutoMLRun.org_id == org_id))
    ).scalar_one_or_none()
    if run is None:
        return
    dataset = (
        await db.execute(select(Dataset).where(Dataset.id == run.dataset_id, Dataset.org_id == org_id))
    ).scalar_one_or_none()
    if dataset is None:
        run.status = "failed"
        run.error = "Dataset not found"
        run.finished_at = datetime.now(timezone.utc)
        await db.commit()
        return

    run.status = "running"
    run.started_at = datetime.now(timezone.utc)
    await db.flush()
    await db.commit()

    try:
        df = await asyncio.to_thread(dataframe_for, dataset)
        config = run.config or {}

        if run.template_id:
            template = automl_templates.get_template(run.template_id)
            pre = await asyncio.to_thread(template.preprocess, df, config)

            if pre.skip_ml:
                engine_result = None
                template_out = await asyncio.to_thread(template.postprocess, None, config, pre.context)
            else:
                engine_result = await asyncio.to_thread(
                    automl_engine.train_automl_baseline,
                    pre.df, pre.target_col, automl_engine.AutoMLTask(pre.task),
                    feature_columns=pre.feature_cols, algorithms=pre.algorithms,
                    thoroughness=config.get("thoroughness", "balanced"),
                )
                template_out = await asyncio.to_thread(template.postprocess, engine_result, config, pre.context)

            # Plain-English explanation + recommendation, built on the same
            # engine_result the template just used - never fails the run,
            # degrades to explanation_sentence=None/recommendation=None.
            role_labels = config.get("column_labels") or config.get("friendly_labels") or {}
            explain_out = automl_explain_nl.explain(engine_result, template, role_labels)
            template_out["explanation_sentence"] = explain_out.get("explanation_sentence")
            template_out["recommendation"] = explain_out.get("recommendation")

            if pre.task:
                run.task = pre.task
            if pre.target_col:
                run.target_column = pre.target_col
            run.status = "success"
            run.leaderboard = template_out["leaderboard"]
            run.best_model_name = template_out["best_model_name"]
            run.metric_name = template_out["metric_name"]
            run.metric_value = template_out["metric_value"]
            meta = {k: v for k, v in template_out.items() if k not in ("pipeline", "explain_X", "explain_y", "leaderboard", "best_model_name", "metric_name", "metric_value")}
            meta["notes"] = pre.notes

            if template_out["pipeline"] is not None:
                run_dir = automl_storage.run_dir(org_id, run_id)
                model_path = run_dir / "model.joblib"
                joblib.dump(template_out["pipeline"], model_path)
                template_out["explain_X"].to_csv(run_dir / "explain_X.csv", index=False)
                pd.DataFrame({"y": template_out["explain_y"]}).to_csv(run_dir / "explain_y.csv", index=False)
                run.model_path = str(model_path)
            else:
                run.model_path = None

            run.meta = meta
            run.finished_at = datetime.now(timezone.utc)
            await db.commit()
            return

        result = await asyncio.to_thread(
            automl_engine.train_automl_baseline,
            df,
            run.target_column,
            automl_engine.AutoMLTask(run.task),
            feature_columns=run.feature_columns or None,
            time_column=config.get("time_column"),
            horizon=int(config.get("horizon", 1)),
            algorithms=config.get("algorithms") or None,
            thoroughness=config.get("thoroughness", "balanced"),
            handle_class_imbalance=bool(config.get("handle_class_imbalance", True)),
            forecast_strategy=config.get("forecast_strategy", "direct"),
            time_budget_seconds=config.get("time_budget_seconds"),
            search_method=config.get("search_method", "random"),
        )
    except Exception as exc:  # noqa: BLE001 - training failure is reported on the run, not raised
        run.status = "failed"
        run.error = str(exc)
        run.finished_at = datetime.now(timezone.utc)
        await db.commit()
        return

    run_dir = automl_storage.run_dir(org_id, run_id)
    model_path = run_dir / "model.joblib"
    joblib.dump(result["pipeline"], model_path)
    result["explain_X"].to_csv(run_dir / "explain_X.csv", index=False)
    pd.DataFrame({"y": result["explain_y"]}).to_csv(run_dir / "explain_y.csv", index=False)

    meta = dict(result["meta"])
    meta["notes"] = result["notes"]

    run.status = "success"
    run.leaderboard = result["leaderboard"]
    run.best_model_name = meta.get("model_name")
    run.metric_name = meta.get("metric_name")
    run.metric_value = meta.get("metric_value")
    run.model_path = str(model_path)
    run.meta = meta
    run.finished_at = datetime.now(timezone.utc)
    await db.commit()
