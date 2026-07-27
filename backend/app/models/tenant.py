from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import JSON, DateTime, Numeric, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Org(Base):
    __tablename__ = "orgs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Manager-submitted adjust/count deltas at or below this magnitude post
    # immediately; over it, they queue for owner approval. See
    # app.api.routes.inventory._post_or_queue_adjustment.
    adjust_approval_threshold: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, default=Decimal("50")
    )
    # consume | add. How committed demand (open sales orders) combines with
    # estimated demand (typed or imported forecasts) when planning. `consume`
    # plans the larger of the two, so orders count towards the estimate rather
    # than on top of it and materials are not bought twice for the same sale.
    # `add` sums them, for a shop whose estimate deliberately excludes booked
    # work. A string rather than a boolean so a third policy needs no migration.
    # See app.services.demand.combine_demand.
    demand_policy: Mapped[str] = mapped_column(String(20), nullable=False, default="consume")
    # Single source of truth for what this org can access - no separate "plan"
    # label is stored anywhere, so the two can never drift apart. See
    # backend/app/core/features.py for the valid keys.
    enabled_features: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
