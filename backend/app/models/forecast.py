from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DemandForecast(Base):
    """One forecasted demand bucket for an item. The bucket's calendar length
    (weekly, monthly, ...) is the caller's choice, not ours - we only store the
    bucket's start date and treat the quantity as demand landing at
    period_start. MRP netting (see app.api.routes.forecasts_mrp.run_mrp_plan) just
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
