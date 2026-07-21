from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AutoMLLiveBinding(Base):
    """Binds a successful AutoMLRun to a live RealtimeStream: app.services.
    automl_realtime polls the stream's collector on an interval, scores its
    recent buffer against the run's persisted pipeline, and publishes the
    result into the stream's metrics under `metric_name` so the existing
    stream_metric alerting (app.services.alerts.eval_stream_metric) can fire
    on it - no new alerting subsystem needed for the live case.

    Also owns periodic drift-checking against the run's training baseline
    (explain_X/explain_y) and, if `auto_retrain_on_drift`, kicks off a new
    versioned AutoMLRun + AutoMLLiveBinding when drift reaches
    `drift_severity_trigger` - see automl_realtime.py.
    """

    __tablename__ = "automl_live_bindings"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("automl_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stream_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("realtime_streams.id", ondelete="CASCADE"), nullable=False, index=True
    )

    metric_name: Mapped[str] = mapped_column(String(64), nullable=False, default="automl_risk_score")
    # "moderate" | "high" - compared against check_data_drift's overall_severity
    # (itself "low" | "moderate" | "high", PSI-based) to decide whether to retrain.
    drift_severity_trigger: Mapped[str] = mapped_column(String(16), nullable=False, default="moderate")
    drift_check_interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=1440)

    last_scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_drift_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # "ok" | "drifted" | None (never checked)
    last_drift_status: Mapped[str | None] = mapped_column(String(16), nullable=True)

    auto_retrain_on_drift: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
