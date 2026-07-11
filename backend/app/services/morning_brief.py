from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bom import Item
from app.models.inventory_snapshot import InventoryDailySnapshot
from app.models.location import Location
from app.models.lot import Lot
from app.services.inventory_analytics import InventoryAnalyticsLine, compute_inventory_analytics

# Ported from a desktop app's morning_brief.py (compare yesterday's shift
# metrics to the same shift a week prior, rank findings by severity),
# rewired onto inventory status instead of OEE/downtime/scrap. This app has
# no job scheduler, so there's no cron writing a daily snapshot - the first
# request each day captures it lazily (see _ensure_todays_snapshot) and every
# request after that on the same day just reads the frozen row back.
COMPARISON_WINDOW_DAYS = 7

# Only these statuses are worth a mention in the brief - healthy/no_data are
# the "nothing to report" states. Order is severity, most urgent first.
ACTIONABLE_STATUSES = ("stockout", "low", "excess", "slow_moving")
_STATUS_PRIORITY = {status: i for i, status in enumerate(ACTIONABLE_STATUSES)}

# Active lots expiring within this many days are worth flagging even though
# they're not yet a hard stockout risk.
LOT_EXPIRY_WARNING_DAYS = 14


@dataclass
class BriefLine:
    item_id: uuid.UUID
    item_sku: str
    item_name: str
    status: str
    on_hand: Decimal
    days_of_supply: float | None
    status_last_week: str | None
    days_of_supply_last_week: float | None
    days_of_supply_delta: float | None
    # True if this item wasn't in the actionable set (or had no snapshot) a
    # week ago - i.e. this is a new problem, not a lingering one.
    is_new_this_week: bool


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


async def _ensure_todays_snapshot(
    db: AsyncSession, org_id: uuid.UUID, today: date, lines: list[InventoryAnalyticsLine]
) -> None:
    already_captured = set(
        (
            await db.execute(
                select(InventoryDailySnapshot.item_id).where(
                    InventoryDailySnapshot.org_id == org_id,
                    InventoryDailySnapshot.snapshot_date == today,
                )
            )
        )
        .scalars()
        .all()
    )
    for line in lines:
        if line.item_id in already_captured:
            continue
        try:
            async with db.begin_nested():
                db.add(
                    InventoryDailySnapshot(
                        org_id=org_id,
                        item_id=line.item_id,
                        snapshot_date=today,
                        on_hand=line.on_hand,
                        avg_daily_demand=line.avg_daily_demand,
                        days_of_supply=line.days_of_supply,
                        status=line.status,
                    )
                )
                await db.flush()
        except IntegrityError:
            # A concurrent request captured today's snapshot for this item
            # first - today's row already exists, nothing to do.
            continue
    await db.commit()


async def compute_morning_brief(db: AsyncSession, org_id: uuid.UUID) -> list[BriefLine]:
    today = datetime.now(timezone.utc).date()
    week_ago = today - timedelta(days=COMPARISON_WINDOW_DAYS)

    lines = await compute_inventory_analytics(db, org_id)
    await _ensure_todays_snapshot(db, org_id, today, lines)

    last_week_by_item = {
        snap.item_id: snap
        for snap in (
            await db.execute(
                select(InventoryDailySnapshot).where(
                    InventoryDailySnapshot.org_id == org_id,
                    InventoryDailySnapshot.snapshot_date == week_ago,
                )
            )
        )
        .scalars()
        .all()
    }

    brief: list[BriefLine] = []
    for line in lines:
        if line.status not in _STATUS_PRIORITY:
            continue
        last_week = last_week_by_item.get(line.item_id)
        days_of_supply_delta = None
        if last_week is not None and last_week.days_of_supply is not None and line.days_of_supply is not None:
            days_of_supply_delta = line.days_of_supply - last_week.days_of_supply
        was_actionable_last_week = last_week is not None and last_week.status in _STATUS_PRIORITY

        brief.append(
            BriefLine(
                item_id=line.item_id,
                item_sku=line.item_sku,
                item_name=line.item_name,
                status=line.status,
                on_hand=line.on_hand,
                days_of_supply=line.days_of_supply,
                status_last_week=last_week.status if last_week is not None else None,
                days_of_supply_last_week=last_week.days_of_supply if last_week is not None else None,
                days_of_supply_delta=days_of_supply_delta,
                is_new_this_week=not was_actionable_last_week,
            )
        )

    brief.sort(
        key=lambda b: (
            _STATUS_PRIORITY[b.status],
            b.days_of_supply if b.days_of_supply is not None else 0.0,
            b.item_sku,
        )
    )
    return brief
