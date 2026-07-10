from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inventory_snapshot import InventoryDailySnapshot
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
