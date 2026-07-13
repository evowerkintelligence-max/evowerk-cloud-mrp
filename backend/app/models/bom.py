from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Item(Base):
    __tablename__ = "items"
    __table_args__ = (
        UniqueConstraint("org_id", "sku", name="uq_items_org_sku"),
        UniqueConstraint("org_id", "barcode", name="uq_items_org_barcode"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sku: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # raw_material | component | finished_good - not an enum column so adding a
    # new type later doesn't need a migration, validated in the route instead.
    item_type: Mapped[str] = mapped_column(String(50), nullable=False, default="component")
    unit_of_measure: Mapped[str] = mapped_column(String(20), nullable=False, default="ea")
    # Opt-in: only lot-tracked items require/get a Lot on PO receipt - see
    # app.api.routes.purchase_orders.receive_purchase_order.
    #
    # IMMUTABLE AFTER CREATION - load-bearing. On-hand for an item comes from
    # exactly one source depending on this flag: lot-tracked items sum their
    # active Lots, everything else uses inventory_balances (see
    # services.mrp.compute_mrp_plan). Those two are disjoint *because* this
    # can't change: is_lot_tracked is deliberately never a field on
    # ItemUpdateRequest (unlike preferred_supplier_id/lead_time_days/name/etc,
    # which are), inventory consume/adjust reject lot-tracked items, and
    # receiving writes to lots XOR inventory by this flag. Making it mutable
    # would strand one ledger and double-count on-hand - don't add it to
    # ItemUpdateRequest without migrating the existing on-hand first.
    # (Guarded by test_is_lot_tracked_immutable.)
    is_lot_tracked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Default source of supply, used to auto-group MRP shortfalls into supplier
    # POs (see app.api.routes.forecasts.generate_pos). Nullable - an item may
    # have no default supplier yet, in which case it's reported as "unassigned"
    # rather than ordered. ondelete SET NULL so deleting a supplier just clears
    # the preference instead of being blocked by / cascading through items.
    preferred_supplier_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("suppliers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Supplier lead time in days, for reorder-point calculation (see
    # app.services.inventory_analytics) and for MRP order-date offsetting (see
    # app.services.mrp). Nullable and NOT defaulted to 0 - no lead time
    # recorded is a real "insufficient data" state, not a same-day lead time,
    # and a 0 would silently understate the reorder point / order date.
    lead_time_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Supplier minimum order quantity, for MRP order-quantity rounding (see
    # app.services.mrp). Nullable and NOT defaulted to 0 for the same reason
    # as lead_time_days - "no MOQ recorded" must not silently behave like
    # "MOQ of 0" (a no-op) is indistinguishable from real data.
    min_order_qty: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    # Catalog-only: hides/tags a discontinued SKU in the UI. Does NOT gate
    # services.mrp.compute_mrp_plan or forecasts.generate_pos - an inactive
    # item is still planned and ordered until that's deliberately wired in.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Nullable + unique per org (see __table_args__) - NULLs don't collide
    # under a unique constraint, so items without a barcode are unaffected.
    barcode: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class Bom(Base):
    __tablename__ = "boms"
    __table_args__ = (UniqueConstraint("org_id", "item_id", "version", name="uq_boms_org_item_version"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The item this BOM produces - not the same as a line's component_item_id.
    item_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Server-computed (max version for this item_id + 1) at creation time, never
    # user-supplied - see app.api.routes.boms.create_bom.
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    # draft | active | archived. One-active-per-item is enforced in the activate
    # route (one transaction), not a DB constraint - see that route for why.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    # SQLAlchemy's own optimistic-concurrency guard: every UPDATE for this row
    # gets "AND row_version = <loaded value>" appended and bumps it by 1,
    # raising StaleDataError if another transaction already moved it since this
    # row was read. Separate from `version` (the BOM revision number, a
    # business field) and from `updated_at` (not usable as a version_id_col
    # itself - edits that only touch bom_lines rows don't dirty it on their
    # own, see update_bom_lines, which still bumps it by hand and lets that
    # same write carry row_version along for the ride).
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": row_version}


class BomLine(Base):
    __tablename__ = "bom_lines"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    bom_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("boms.id", ondelete="CASCADE"), nullable=False, index=True
    )
    component_item_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("items.id"), nullable=False
    )
    # Decimal, not float - this feeds cost rollups later, a float would be a
    # correctness bug waiting to happen. No unit_of_measure column here on
    # purpose - always read it from the joined Item instead, so a line can
    # never disagree with its own component's base unit.
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Expected material loss for this component, as a percentage (0-100) - the
    # user's own scrap/failure leeway. MRP orders quantity * (1 + scrap_pct/100)
    # of this component per unit of parent built, so a lossy step doesn't leave
    # planning short. Additive, not yield-based (dividing by (1 - scrap_pct/100)),
    # to keep 100%+ loss representable without a divide-by-zero at 100.
    scrap_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=Decimal(0))
