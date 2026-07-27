from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Customer(Base):
    """Who an order is for. Mirrors Supplier on the buy side, including the
    soft-delete-only rule: a customer is deactivated, never deleted, so past
    orders keep a valid customer_id instead of being orphaned.
    """

    __tablename__ = "customers"
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_customers_org_name"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Deactivated customers stay on their past orders and simply drop out of
    # the order-entry picker, the same way Supplier.is_active works.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class SalesOrder(Base):
    """A customer's commitment to buy. This is demand, not an invoice: there is
    deliberately no price, tax or currency anywhere on the sell side, because
    retrofitting those semantics onto historical rows is not reconstructable
    (same reasoning as PurchaseOrderLine.unit_price documents on the buy side).
    """

    __tablename__ = "sales_orders"
    __table_args__ = (UniqueConstraint("org_id", "so_number", name="uq_sos_org_number"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Server-computed (max so_number for this org + 1) at creation, never
    # user-supplied. Plain int, not a padded string like "SO-0004" - format that
    # at the response or frontend layer, never store the formatted string.
    so_number: Mapped[int] = mapped_column(Integer, nullable=False)
    # open | done | cancelled. Only `open` counts as demand, so this column is
    # the single switch that puts an order into or out of the plan.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open", index=True)
    # When the customer needs it. This is what drives planning dates, so it is
    # required rather than nullable: an order with no date cannot be planned.
    needed_by: Mapped[date] = mapped_column(Date, nullable=False)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
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


class SalesOrderLine(Base):
    __tablename__ = "sales_order_lines"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    so_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("sales_orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("items.id"), nullable=False)
    quantity_ordered: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    # Mirrors PurchaseOrderLine.quantity_received. Partial shipments are out of
    # scope, so today this is only ever 0 or the full ordered quantity, and the
    # outstanding-quantity subtraction in services.demand is a no-op on every
    # open order. It still earns its place: it records what actually left the
    # building rather than inferring it from status, and a later partial
    # -shipment feature builds on it without revisiting the demand maths.
    quantity_delivered: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=Decimal(0))
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
