from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import Date, DateTime, Float, ForeignKey, Index, Numeric, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DemandForecast(Base):
    """One forecasted demand bucket for an item. The bucket's calendar length
    (weekly, monthly, ...) is the caller's choice, not ours - we only store the
    bucket's start date and treat the quantity as demand landing at
    period_start. MRP netting (see app.api.routes.forecasts.run_mrp_plan) just
    sums whichever buckets fall inside the requested horizon, so a customer can
    import weekly buckets and another monthly without any schema change.

    The (org_id, item_id, period_start) uniqueness makes a re-import of the same
    period an upsert, not a duplicate - re-running the desktop forecaster and
    pushing its output again overwrites the stale number instead of double
    counting it (see the import route).
    """

    __tablename__ = "demand_forecasts"
    __table_args__ = (
        UniqueConstraint("org_id", "item_id", "period_start", name="uq_demand_forecasts_org_item_period"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("items.id"), nullable=False, index=True)
    # Start date of the demand bucket. Index it - the MRP plan filters on a
    # [horizon_start, horizon_end] range over this column for every run.
    period_start: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    # Forecasted demand for the bucket. Decimal, not float - it nets directly
    # against Lot.quantity_remaining / PurchaseOrderLine.quantity_ordered, which
    # are also Numeric(12, 4), so a float here would reintroduce rounding drift
    # into the netting math.
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    # manual | imported. Server-controlled, never client-supplied: a hand-typed
    # override and a bulk push from the desktop forecaster are the same row
    # shape but different provenance, and provenance is what a planner needs
    # when a forecast looks wrong. The import route always stamps "imported",
    # the single-row route always "manual".
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")
    notes: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    # Bumped on every upsert (a re-import of an existing period), so "when was
    # this number last refreshed" stays answerable.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class ForecastSnapshot(Base):
    """One future point from one mfg-forecast run, kept forever (append-only,
    never overwritten) so a later run can compare what we predicted for a
    date against what actually happened once that date has passed. This is
    the only way to know real forecast accuracy - the model's own MAPE/R2 are
    cross-validated on history, not a genuine out-of-sample track record.

    Keyed by (org_id, target_col, machine_filter) rather than dataset_id -
    Datasets are immutable-after-create (see Dataset's docstring), so a
    real "new data came in" workflow is a fresh upload with a new dataset_id,
    not a growing row count on the same one. Scoping to dataset_id would mean
    a track record that almost never accumulates. dataset_id is kept as a
    plain informational column (which upload produced this point), not part
    of the lookup key, and has no FK so deleting an old dataset after a
    re-upload doesn't cascade-delete its forecast history.

    # ponytail: target_col name is the whole identity - two unrelated
    # datasets that both happen to use a column called "output" will blend
    # into the same track record. Add a real named-series concept if that
    # collision becomes a problem in practice.

    Track record only looks at the OLDEST snapshot per date (see
    app.services.mfg_forecast.get_track_record) - the longest-lead-time call,
    which is the fairest test of whether the model saw something coming.
    """

    __tablename__ = "mfg_forecast_snapshots"
    __table_args__ = (
        Index("ix_mfg_forecast_snapshots_lookup", "org_id", "target_col", "machine_filter", "date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    target_col: Mapped[str] = mapped_column(String(255), nullable=False)
    machine_filter: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    date: Mapped[date] = mapped_column(Date, nullable=False)
    predicted: Mapped[float] = mapped_column(Float, nullable=False)
    lower: Mapped[float] = mapped_column(Float, nullable=False)
    upper: Mapped[float] = mapped_column(Float, nullable=False)
    made_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
