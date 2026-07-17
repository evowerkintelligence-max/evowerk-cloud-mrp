from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AutoMLRun(Base):
    """One AutoML training run over a tenant's Dataset: sklearn model bake-off
    + hyperparameter search, scored on a holdout split. status starts "queued"
    (set by the route before the background task even runs) and is advanced by
    app/services/automl_runner.py, same lifecycle as WorkflowRun.
    """

    __tablename__ = "automl_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # classification | regression | forecasting
    task: Mapped[str] = mapped_column(String(32), nullable=False)
    target_column: Mapped[str] = mapped_column(String(255), nullable=False)
    feature_columns: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # thoroughness, search_method, algorithms, time_column, horizon, forecast_strategy
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # queued | running | success | failed
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    error: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    leaderboard: Mapped[list | None] = mapped_column(JSON, nullable=True)
    best_model_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metric_name: Mapped[str | None] = mapped_column(String(32), nullable=True)
    metric_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # schema, feature list, explain-sample paths, notes - see automl_engine.py
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    # Opt-in, settable only at creation - see app.services.automl_retrain.
    # True means "this run is the currently-watched one for its dataset."
    auto_retrain: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Set on a run created by automl_retrain.maybe_retrain_for_dataset.
    retrained_from_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("automl_runs.id", ondelete="SET NULL"), nullable=True
    )
    # Consecutive auto-retrain attempts for this lineage that did NOT pass
    # the quality gate; reset to 0 on a promoted retrain. The circuit
    # breaker trips (auto_retrain -> False) once this reaches
    # automl_retrain.MAX_AUTO_RETRAIN_STREAK.
    auto_retrain_streak: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
