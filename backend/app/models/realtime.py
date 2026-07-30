from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RealtimeStream(Base):
    """A tenant-scoped definition of a live-polling collector over an
    Integration, ported from the desktop app's realtime/engine.py.

    The desktop app kept stream state purely in memory (a dict of running
    background threads) - fine for one always-on local process, but this
    server can restart or redeploy. This row is the durable half: the
    collector loop itself is still an in-process thread (see
    app.services.realtime.StreamRunner), but its definition and
    `desired_status` survive a restart, and app.main's startup hook resumes
    any stream still marked "running" so a redeploy doesn't silently lose it.

    `id` doubles as the stream_id on RealtimeSample rows, so a resumed stream
    keeps its sample history contiguous.
    """

    __tablename__ = "realtime_streams"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    integration_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("integrations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # {"units_col": "...", "scrap_col": "...", ...} - see StreamRunner.snapshot for the keys read.
    column_map: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # Which machine this stream's ticks belong to - a stream carries one row
    # per poll (one machine's worth of data), never multiple machines mixed
    # together. None = this stream stays OEE-only, unaffected by CBM.
    machine: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # {"vib_x": "vibration_x_mm_s", ...} - sensor channel name -> the field
    # name to read from a polled row. See app.services.sensor_ingest.
    sensor_map: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    accumulate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # running | stopped - the user's intent, used to decide what to resume on startup.
    # The *live* status (running/error, from the in-memory collector thread) is
    # ephemeral and only available via GET .../snapshot while this process is up.
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


class RealtimeSample(Base):
    """One collector poll window, written by app.services.realtime.
    StreamCollector: the durable replacement for the old broker pipeline's
    windowed snapshot, the per-replica cache dict, AND the per-container
    JSONL history files. `id` is the incremental-sync cursor: the frontend
    passes back the highest id it has seen and only newer rows are returned,
    so nothing is ever re-sent."""

    __tablename__ = "realtime_samples"
    __table_args__ = (
        Index("ix_realtime_samples_stream_id_id", "stream_id", "id"),
        # The retention sweep deletes by ts alone (app.services.realtime._maybe_prune).
        Index("ix_realtime_samples_ts", "ts"),
    )

    # BigInteger in prod; plain INTEGER on SQLite so autoincrement works in tests.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stream_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("realtime_streams.id", ondelete="CASCADE"), nullable=False
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    rows_in_window: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_collected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_s: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_tick_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # This window's NEW rows (post-dedup), capped at 1000 by the collector.
    # Backs app.services.realtime.recent_rows for AutoML live scoring.
    raw_rows: Mapped[list] = mapped_column(JSON, nullable=False, default=list)


class RealtimeStreamMetric(Base):
    """Externally computed live metrics (today: AutoML risk scores) merged
    into a stream's snapshot metrics at read time, written by
    app.services.realtime.set_external_metric. A separate row per (stream,
    name) with a composite
    PK so concurrent publishers upsert race-free instead of read-modify-
    writing a JSON blob."""

    __tablename__ = "realtime_stream_metrics"

    stream_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("realtime_streams.id", ondelete="CASCADE"), primary_key=True
    )
    name: Mapped[str] = mapped_column(String(255), primary_key=True)
    # Any + explicit JSON column: union annotations don't resolve in
    # SQLAlchemy's type map. Values are floats today (AutoML scores).
    value: Mapped[Any] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
