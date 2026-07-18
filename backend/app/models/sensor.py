"""CBM sensor telemetry - written by app.services.sensor_consumer as it drains
realtime.ticks for streams with a sensor_map configured, scored by
app.services.anomaly (which reuses app.analytics.spc_calc's existing
Individuals/Moving-Range control chart), and read by app.services.cbm."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SensorReading(Base):
    __tablename__ = "sensor_readings"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    machine: Mapped[str] = mapped_column(String(255), nullable=False)
    sensor: Mapped[str] = mapped_column(String(255), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SensorBaseline(Base):
    """Cached I-MR control limits for one (machine, sensor), refreshed on every
    SensorReading write from spc_calc.individuals_chart's output so reads
    don't recompute over the full window per request."""

    __tablename__ = "sensor_baselines"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    machine: Mapped[str] = mapped_column(String(255), nullable=False)
    sensor: Mapped[str] = mapped_column(String(255), nullable=False)
    n: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    centerline: Mapped[float | None] = mapped_column(Float, nullable=True)
    ucl: Mapped[float | None] = mapped_column(Float, nullable=True)
    lcl: Mapped[float | None] = mapped_column(Float, nullable=True)
    mr_bar: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class SensorAnomaly(Base):
    """One out-of-control reading. violations holds the Nelson rule numbers
    (1-8) from spc_calc.nelson_violations, e.g. [1] or [1, 6]."""

    __tablename__ = "sensor_anomalies"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    machine: Mapped[str] = mapped_column(String(255), nullable=False)
    sensor: Mapped[str] = mapped_column(String(255), nullable=False)
    reading_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("sensor_readings.id", ondelete="CASCADE"), nullable=False
    )
    violations: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WeibullFit(Base):
    """Latest Weibull survival fit for one machine. fit_tier records which
    fallback tier produced it (per_machine / fleet_pooled / insufficient_data)
    so callers know how much to trust the number. Refreshed lazily on first
    read each day by app.services.rul.get_or_refit - see that module's
    docstring for why there's no scheduled job."""

    __tablename__ = "weibull_fits"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    machine: Mapped[str] = mapped_column(String(255), nullable=False)
    shape_k: Mapped[float | None] = mapped_column(Float, nullable=True)
    scale_lambda: Mapped[float | None] = mapped_column(Float, nullable=True)
    n_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_censored: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fit_tier: Mapped[str] = mapped_column(String(32), nullable=False)
    fit_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
