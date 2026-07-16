from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import Date, DateTime, Float, ForeignKey, Numeric, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class InventoryDailySnapshot(Base):
    """One frozen point-in-time row per (org, item, day): the inventory
    analytics figures as of the first request that day (see
    app.services.morning_brief - there's no job scheduler in this app, so a
    day's snapshot is captured lazily on first read rather than by a cron job,
    and is never overwritten afterwards). The morning brief compares today's
    row to the row from 7 days ago to surface what changed.
    """

    __tablename__ = "inventory_daily_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "item_id", "snapshot_date", name="uq_inventory_daily_snapshots_org_item_date"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("items.id"), nullable=False, index=True)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    on_hand: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    avg_daily_demand: Mapped[float] = mapped_column(Float, nullable=False)
    days_of_supply: Mapped[float | None] = mapped_column(Float, nullable=True)
    # stockout | low | dead_stock | excess | slow_moving | healthy | no_data - see
    # app.services.inventory_analytics._status.
    status: Mapped[str] = mapped_column(String(20), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
