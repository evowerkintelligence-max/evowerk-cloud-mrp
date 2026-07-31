from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bom import Item
from app.models.location import Location
from app.models.lot import Lot

# Active lots expiring within this many days are worth flagging even though
# they're not yet a hard stockout risk.
LOT_EXPIRY_WARNING_DAYS = 14


@dataclass
class LotAlertLine:
    lot_id: uuid.UUID
    item_id: uuid.UUID
    item_sku: str
    item_name: str
    location_id: uuid.UUID
    location_code: str
    lot_number: str
    quantity_remaining: Decimal
    status: str
    expiry_date: date | None
    days_until_expiry: int | None
    # "on_hold" | "expiring_soon" - a lot on hold takes priority even if it
    # also happens to be expiring soon, since it already needs a decision.
    reason: str


async def compute_lot_alerts(db: AsyncSession, org_id: uuid.UUID) -> list[LotAlertLine]:
    """Lots worth a human looking at today: on QA hold, or active stock
    expiring within LOT_EXPIRY_WARNING_DAYS. Depleted lots and active lots
    with no/distant expiry aren't actionable, so they're excluded."""
    today = datetime.now(timezone.utc).date()
    warning_cutoff = today + timedelta(days=LOT_EXPIRY_WARNING_DAYS)

    rows = await db.execute(
        select(Lot, Item, Location)
        .join(Item, Item.id == Lot.item_id)
        .join(Location, Location.id == Lot.location_id)
        .where(
            Lot.org_id == org_id,
            Lot.quantity_remaining > 0,
            or_(
                Lot.status == "on_hold",
                and_(Lot.status == "active", Lot.expiry_date.is_not(None), Lot.expiry_date <= warning_cutoff),
            ),
        )
    )

    alerts = [
        LotAlertLine(
            lot_id=lot.id,
            item_id=lot.item_id,
            item_sku=item.sku,
            item_name=item.name,
            location_id=lot.location_id,
            location_code=location.code,
            lot_number=lot.lot_number,
            quantity_remaining=lot.quantity_remaining,
            status=lot.status,
            expiry_date=lot.expiry_date,
            days_until_expiry=(lot.expiry_date - today).days if lot.expiry_date is not None else None,
            reason="on_hold" if lot.status == "on_hold" else "expiring_soon",
        )
        for lot, item, location in rows.all()
    ]
    alerts.sort(
        key=lambda a: (
            0 if a.reason == "on_hold" else 1,
            a.days_until_expiry if a.days_until_expiry is not None else 0,
            a.item_sku,
        )
    )
    return alerts
