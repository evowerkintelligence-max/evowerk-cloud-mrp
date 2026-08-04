from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SyncSchedule(Base):
    """A tenant-scoped scheduled pull-sync against an Integration's connector.
    Replaces the old RealtimeStream (app.services.realtime's thread-based
    TickProducer + Redpanda/Flink pipeline) - see
    docs/superpowers/specs/2026-08-04-scheduled-cloud-sync-design.md. A
    background APScheduler job (app.services.sync_scheduler) wakes on
    interval_seconds, pulls only rows newer than cursor_value via the
    connector's fetch_since(), and persists results as SyncTick/SyncRow.
    """

    __tablename__ = "sync_schedules"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    integration_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("integrations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # {"units_col": "...", "scrap_col": "...", ...} - see app.services.metrics_calc.compute_window_metrics.
    column_map: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # Which machine this schedule's rows belong to - None = OEE-only, unaffected by CBM.
    machine: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # {"vib_x": "vibration_x_mm_s", ...} - sensor channel name -> field name to read
    # from each synced row. See app.services.sensor_sync.
    sensor_map: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    # Column used for incremental fetch (SqlServerConnector.fetch_since and
    # friends) - admin-picked from the connector's preview() output.
    cursor_column: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Last-seen cursor value, stored as text (each connector casts per its
    # column's real type when building the WHERE clause).
    cursor_value: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # running | stopped - the user's intent, read by app.services.sync_scheduler
    # on startup to decide which schedules to (re)register a job for.
    desired_status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    last_error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
