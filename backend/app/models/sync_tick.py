from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SyncTick(Base):
    """One successful scheduled-sync run's computed metrics - replaces the old
    JSONL tick-history file app.services.realtime used to write. Queried for
    GET /sync-schedules/{id}/history and for stream_metric alert evaluation
    (app.services.alerts.eval_stream_metric)."""

    __tablename__ = "sync_ticks"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    schedule_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("sync_schedules.id", ondelete="CASCADE"), nullable=False, index=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class SyncRow(Base):
    """Raw synced rows, capped at ~10,000 most recent per schedule (oldest
    trimmed each run by app.services.sync_scheduler) - the feature-column
    buffer app.services.automl_realtime's live scoring/drift-check needs,
    replacing the old in-memory realtime_cache.recent_rows()."""

    __tablename__ = "sync_rows"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    schedule_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("sync_schedules.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    row: Mapped[dict] = mapped_column(JSON, nullable=False)
