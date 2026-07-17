"""AutoML - sklearn model bake-off over a tenant's Dataset, an async
run-then-poll job (mirrors app/api/routes/workflows.py): training kicks off
via FastAPI BackgroundTasks and the client polls GET /automl/runs/{id} for
status. No capability system exists in this codebase (see workflows.py) -
any authenticated org member can train/predict/delete, same as workflows."""
from __future__ import annotations

import uuid
from typing import Any

import joblib
import pandas as pd
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_or_404
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.automl_live_binding import AutoMLLiveBinding
from app.models.automl_run import AutoMLRun
from app.models.realtime import RealtimeStream
from app.services import automl_engine, automl_realtime, automl_storage, automl_templates, datasets as datasets_service
from app.services.automl_runner import execute_automl_run

router = APIRouter(prefix="/automl", tags=["automl"])


class TrainRequest(BaseModel):
    name: str
    dataset_id: uuid.UUID
    task: str
    target_column: str
    feature_columns: list[str] = []
    thoroughness: str = "balanced"
    algorithms: list[str] | None = None
    handle_class_imbalance: bool = True
    time_column: str | None = None
    horizon: int = 1
    forecast_strategy: str = "direct"
    time_budget_seconds: int | None = None
    search_method: str = "random"


class TemplateRoleResponse(BaseModel):
    key: str
    label: str
    required: bool


class TemplateSummary(BaseModel):
    id: str
    label: str
    roles: list[TemplateRoleResponse]


class TemplateRunRequest(BaseModel):
    name: str
    dataset_id: uuid.UUID
    column_map: dict[str, str]
    config: dict[str, Any] = {}


class RunSummary(BaseModel):
    id: str
    name: str
    task: str
    status: str
    dataset_id: str
    target_column: str
    best_model_name: str | None
    metric_name: str | None
    metric_value: float | None
    error: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None


class RunDetail(RunSummary):
    feature_columns: list[str]
    config: dict
    leaderboard: list | None
    meta: dict | None


class DatasetRefRequest(BaseModel):
    dataset_id: uuid.UUID


class RowExplainRequest(BaseModel):
    row: dict[str, Any]


class LiveBindRequest(BaseModel):
    stream_id: uuid.UUID
    metric_name: str = "automl_risk_score"
    drift_severity_trigger: str = "moderate"
    drift_check_interval_minutes: int = 1440
    auto_retrain_on_drift: bool = False


class LiveBindingResponse(BaseModel):
    id: str
    run_id: str
    stream_id: str
    metric_name: str
    drift_severity_trigger: str
    drift_check_interval_minutes: int
    auto_retrain_on_drift: bool
    enabled: bool
    last_scored_at: str | None
    last_drift_check_at: str | None
    last_drift_status: str | None


def _binding_response(b: AutoMLLiveBinding) -> LiveBindingResponse:
    return LiveBindingResponse(
        id=str(b.id),
        run_id=str(b.run_id),
        stream_id=str(b.stream_id),
        metric_name=b.metric_name,
        drift_severity_trigger=b.drift_severity_trigger,
        drift_check_interval_minutes=b.drift_check_interval_minutes,
        auto_retrain_on_drift=b.auto_retrain_on_drift,
        enabled=b.enabled,
        last_scored_at=b.last_scored_at.isoformat() if b.last_scored_at else None,
        last_drift_check_at=b.last_drift_check_at.isoformat() if b.last_drift_check_at else None,
        last_drift_status=b.last_drift_status,
    )


def _summary(r: AutoMLRun) -> RunSummary:
    return RunSummary(
        id=str(r.id),
        name=r.name,
        task=r.task,
        status=r.status,
        dataset_id=str(r.dataset_id),
        target_column=r.target_column,
        best_model_name=r.best_model_name,
        metric_name=r.metric_name,
        metric_value=r.metric_value,
        error=r.error,
        created_at=r.created_at.isoformat(),
        started_at=r.started_at.isoformat() if r.started_at else None,
        finished_at=r.finished_at.isoformat() if r.finished_at else None,
    )


def _detail(r: AutoMLRun) -> RunDetail:
    return RunDetail(
        **_summary(r).model_dump(),
        feature_columns=r.feature_columns or [],
        config=r.config or {},
        leaderboard=r.leaderboard,
        meta=r.meta,
    )


def _require_success(run: AutoMLRun) -> None:
    if run.status != "success":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Run is not ready (status: {run.status})")


def _load_pipeline(run: AutoMLRun):
    if not run.model_path:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Run has no trained model artifact")
    return joblib.load(run.model_path)


def _load_explain_sample(org_id: uuid.UUID, run_id: uuid.UUID) -> tuple[pd.DataFrame | None, pd.Series | None]:
    run_dir = automl_storage.run_dir(org_id, run_id)
    x_path, y_path = run_dir / "explain_X.csv", run_dir / "explain_y.csv"
    if not x_path.exists() or not y_path.exists():
        return None, None
    X = pd.read_csv(x_path)
    ydf = pd.read_csv(y_path)
    y = ydf["y"] if "y" in ydf.columns else ydf.iloc[:, 0]
    return X, y


@router.get("/algorithms")
async def get_algorithms(task: str, current: CurrentUser = Depends(get_current_user)):
    try:
        automl_task = automl_engine.AutoMLTask(task)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown task '{task}'") from None
    return automl_engine.algorithm_roster(automl_task)


@router.get("/templates", response_model=list[TemplateSummary])
async def list_templates(current: CurrentUser = Depends(get_current_user)):
    return [
        TemplateSummary(
            id=t.id, label=t.label,
            roles=[TemplateRoleResponse(key=r.key, label=r.label, required=r.required) for r in t.roles],
        )
        for t in automl_templates.list_templates()
    ]


@router.post("/templates/{template_id}/runs", response_model=RunDetail, status_code=status.HTTP_202_ACCEPTED)
async def create_template_run(
    template_id: str,
    req: TemplateRunRequest,
    background_tasks: BackgroundTasks,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        template = automl_templates.get_template(template_id)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown AutoML template '{template_id}'") from None

    dataset = await datasets_service.get_dataset_or_404(db, current.org_id, req.dataset_id)

    run = AutoMLRun(
        org_id=current.org_id,
        dataset_id=dataset.id,
        created_by=current.id,
        name=req.name,
        task="classification",  # placeholder until the run finishes - automl_runner._execute()'s
                                 # template branch (Task 6) overwrites this with pre.task once
                                 # preprocess() runs; queued/running polls will read this default.
        target_column="",       # same - overwritten with pre.target_col once preprocess() runs
        template_id=template.id,
        config={"column_map": req.column_map, **req.config},
        status="queued",
    )
    db.add(run)
    await log_event(db, action="automl.template_train", org_id=current.org_id, actor_user_id=current.id, metadata={"name": req.name, "template_id": template.id})
    await db.commit()
    await db.refresh(run)

    background_tasks.add_task(execute_automl_run, str(run.id), str(current.org_id))
    return _detail(run)


@router.get("/runs", response_model=list[RunSummary])
async def list_runs(current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(select(AutoMLRun).where(AutoMLRun.org_id == current.org_id).order_by(AutoMLRun.created_at.desc()))
    ).scalars().all()
    return [_summary(r) for r in rows]


@router.post("/runs", response_model=RunDetail, status_code=status.HTTP_202_ACCEPTED)
async def create_run(
    req: TrainRequest,
    background_tasks: BackgroundTasks,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        automl_engine.AutoMLTask(req.task)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown task '{req.task}'") from None
    dataset = await datasets_service.get_dataset_or_404(db, current.org_id, req.dataset_id)

    run = AutoMLRun(
        org_id=current.org_id,
        dataset_id=dataset.id,
        created_by=current.id,
        name=req.name,
        task=req.task,
        target_column=req.target_column,
        feature_columns=req.feature_columns,
        config={
            "thoroughness": req.thoroughness,
            "algorithms": req.algorithms,
            "handle_class_imbalance": req.handle_class_imbalance,
            "time_column": req.time_column,
            "horizon": req.horizon,
            "forecast_strategy": req.forecast_strategy,
            "time_budget_seconds": req.time_budget_seconds,
            "search_method": req.search_method,
        },
        status="queued",
    )
    db.add(run)
    await log_event(db, action="automl.train", org_id=current.org_id, actor_user_id=current.id, metadata={"name": req.name, "task": req.task})
    await db.commit()
    await db.refresh(run)

    background_tasks.add_task(execute_automl_run, str(run.id), str(current.org_id))
    return _detail(run)


@router.get("/runs/{run_id}", response_model=RunDetail)
async def get_run(run_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    run = await get_or_404(db, AutoMLRun, current.org_id, run_id, "Run not found")
    return _detail(run)


@router.delete("/runs/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_run(run_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    run = await get_or_404(db, AutoMLRun, current.org_id, run_id, "Run not found")
    automl_storage.delete_run_dir(current.org_id, run_id)
    await db.delete(run)
    await log_event(db, action="automl.deleted", org_id=current.org_id, actor_user_id=current.id, target=str(run_id))
    await db.commit()


@router.post("/runs/{run_id}/predict")
async def predict(
    run_id: uuid.UUID, req: DatasetRefRequest, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    run = await get_or_404(db, AutoMLRun, current.org_id, run_id, "Run not found")
    _require_success(run)
    dataset = await datasets_service.get_dataset_or_404(db, current.org_id, req.dataset_id)
    pipe = _load_pipeline(run)
    df = datasets_service.dataframe_for(dataset)
    try:
        return automl_engine.predict_with_model(pipe, run.meta or {}, df)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get("/runs/{run_id}/forecast-trajectory")
async def forecast_trajectory(
    run_id: uuid.UUID, steps: int | None = None, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    run = await get_or_404(db, AutoMLRun, current.org_id, run_id, "Run not found")
    _require_success(run)
    dataset = await datasets_service.get_dataset_or_404(db, current.org_id, run.dataset_id)
    pipe = _load_pipeline(run)
    df = datasets_service.dataframe_for(dataset)
    Xs, ys = _load_explain_sample(current.org_id, run_id)
    try:
        return automl_engine.forecast_trajectory(pipe, run.meta or {}, df, Xs, ys, steps=steps)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get("/runs/{run_id}/explain")
async def explain(run_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    run = await get_or_404(db, AutoMLRun, current.org_id, run_id, "Run not found")
    _require_success(run)
    pipe = _load_pipeline(run)
    Xs, ys = _load_explain_sample(current.org_id, run_id)
    return automl_engine.explain_model(pipe, run.meta or {}, Xs, ys)


@router.post("/runs/{run_id}/explain-row")
async def explain_row(
    run_id: uuid.UUID, req: RowExplainRequest, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    run = await get_or_404(db, AutoMLRun, current.org_id, run_id, "Run not found")
    _require_success(run)
    pipe = _load_pipeline(run)
    Xs, ys = _load_explain_sample(current.org_id, run_id)
    try:
        return automl_engine.explain_row(pipe, run.meta or {}, Xs, ys, req.row)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get("/runs/{run_id}/partial-dependence")
async def partial_dependence(
    run_id: uuid.UUID, feature: str, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    run = await get_or_404(db, AutoMLRun, current.org_id, run_id, "Run not found")
    _require_success(run)
    pipe = _load_pipeline(run)
    Xs, _ys = _load_explain_sample(current.org_id, run_id)
    try:
        return automl_engine.partial_dependence_data(pipe, run.meta or {}, Xs, feature)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.post("/runs/{run_id}/drift-check")
async def drift_check(
    run_id: uuid.UUID, req: DatasetRefRequest, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    run = await get_or_404(db, AutoMLRun, current.org_id, run_id, "Run not found")
    _require_success(run)
    dataset = await datasets_service.get_dataset_or_404(db, current.org_id, req.dataset_id)
    df = datasets_service.dataframe_for(dataset)
    Xs, _ys = _load_explain_sample(current.org_id, run_id)
    try:
        return automl_engine.check_data_drift(run.meta or {}, Xs, df)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.post("/runs/{run_id}/live-bind", response_model=LiveBindingResponse, status_code=status.HTTP_201_CREATED)
async def create_live_binding(
    run_id: uuid.UUID, req: LiveBindRequest, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    run = await get_or_404(db, AutoMLRun, current.org_id, run_id, "Run not found")
    _require_success(run)
    stream = await get_or_404(db, RealtimeStream, current.org_id, req.stream_id, "Stream not found")

    binding = AutoMLLiveBinding(
        org_id=current.org_id,
        run_id=run.id,
        stream_id=stream.id,
        metric_name=req.metric_name,
        drift_severity_trigger=req.drift_severity_trigger,
        drift_check_interval_minutes=req.drift_check_interval_minutes,
        auto_retrain_on_drift=req.auto_retrain_on_drift,
    )
    run.source_stream_id = stream.id
    db.add(binding)
    await log_event(
        db, action="automl.live_bind", org_id=current.org_id, actor_user_id=current.id,
        metadata={"run_id": str(run.id), "stream_id": str(stream.id), "metric_name": req.metric_name},
    )
    await db.commit()
    await db.refresh(binding)

    automl_realtime.start_binding_scheduler(binding.id, current.org_id)
    return _binding_response(binding)


@router.delete("/live-bindings/{binding_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_live_binding(
    binding_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    binding = await get_or_404(db, AutoMLLiveBinding, current.org_id, binding_id, "Live binding not found")
    automl_realtime.stop_binding_scheduler(binding.id)
    await db.delete(binding)
    await log_event(db, action="automl.live_binding_deleted", org_id=current.org_id, actor_user_id=current.id, target=str(binding_id))
    await db.commit()
