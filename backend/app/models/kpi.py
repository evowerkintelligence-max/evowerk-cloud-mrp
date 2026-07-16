from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Kpi(Base):
    """A tenant-scoped named SQL metric over a Dataset, ported from the desktop
    app's kpis_engine.py/kpis_store.py (see app/services/kpis.py for compute/
    evaluate logic).

    `sql` must contain a `{table}` placeholder, substituted with the literal
    table name `dataset` — the name app.services.sql_query.run_sql
    materializes the source dataset under (same convention as that module's
    free-text SQL box).
    """

    __tablename__ = "kpis"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    sql: Mapped[str] = mapped_column(String(4000), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # higher_is_better | lower_is_better | neutral
    direction: Mapped[str] = mapped_column(String(20), nullable=False, default="neutral")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # {"delta": {...}, "static": {...}, "baseline": {...}, "trend": {...}}, all optional — see
    # app/services/kpis.py's _evaluate_status for the shape each key is read as.
    eval_policy: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # Optional grouped drill-down query, run alongside the main value. Serves
    # two uses depending on what column is picked: GROUP BY machine + avg for
    # per-machine breakdown, or GROUP BY reason + count for a breach Pareto.
    # Column names are validated against the dataset's schema at write time
    # (see api/routes/kpis.py _validate_breakdown) - never raw SQL.
    breakdown_group_col: Mapped[str | None] = mapped_column(String(200), nullable=True)
    breakdown_agg: Mapped[str | None] = mapped_column(String(10), nullable=True)  # count | avg | sum
    breakdown_value_col: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class KpiValue(Base):
    """One computed reading of a Kpi against a specific dataset (usually the
    Kpi's own dataset_id, but compute/compare can target any dataset in the
    org, mirroring the desktop app's scope_type/scope_id flexibility).

    Append-only: history/trend/baseline evaluation reads back through this
    table rather than a separate cache, so there is no pruning job like the
    desktop JSON store needed — a normal indexed table doesn't share that
    store's "whole file loaded into memory" problem.
    """

    __tablename__ = "kpi_values"
    __table_args__ = (
        Index("ix_kpi_values_kpi_dataset_computed_at", "kpi_id", "dataset_id", "computed_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kpi_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("kpis.id", ondelete="CASCADE"), nullable=False, index=True
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )


class KpiDashboard(Base):
    """A named, ordered, tiered collection of KPIs for the dashboard view
    (frontend/src/pages/KpiDashboards.tsx). kpi_ids is an ordered JSON list -
    same pattern as Kpi.tags - no join table needed at this scale.
    """

    __tablename__ = "kpi_dashboards"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    tier: Mapped[str] = mapped_column(String(20), nullable=False, default="supervisor")  # operator | supervisor
    kpi_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    refresh_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
