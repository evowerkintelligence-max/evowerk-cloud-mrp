from __future__ import annotations

import statistics
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bom import Item
from app.models.inventory import InventoryBalance
from app.models.lot import Lot
from app.services.demand_history import daily_consumption_by_item
from app.services.pricing import latest_unit_prices

# Ported from a desktop app's inventory_stats.py/inventory_engine.py (safety
# stock / reorder point / ABC / XYZ), rewritten against this app's org-scoped
# SQLAlchemy models instead of a flat per-SKU dataset.
DEFAULT_LOOKBACK_DAYS = 90
DEFAULT_TARGET_SERVICE_LEVEL_PCT = 95.0
# Fallback demand coefficient-of-variation when too little consumption history
# exists to compute a real one - same 30% assumption the source used.
ASSUMED_DEMAND_CV = 0.3
MIN_HISTORY_DAYS_FOR_STATS = 5
SLOW_MOVING_THRESHOLD_DAYS = 90
EXCESS_THRESHOLD_DAYS = 180
# Stockout-risk threshold for items with no lead_time_days on file.
DEFAULT_STOCKOUT_DAYS = 30

_STANDARD_NORMAL = statistics.NormalDist(mu=0.0, sigma=1.0)


def norm_ppf(p: float) -> float:
    """Standard normal inverse CDF (quantile / probit) - Phi^-1(p)."""
    return _STANDARD_NORMAL.inv_cdf(p)


def demand_during_lead_time_std(
    demand_std: float, lead_time_mean: float, demand_mean: float, lead_time_std: float = 0.0
) -> float:
    """Standard deviation of demand during lead time (sigma_DLT), combining
    demand and lead-time variability (APICS CPIM / Silver-Pyke-Peterson),
    assuming independent, i.i.d. daily demand:

        sigma_DLT = sqrt( L_mean * sigma_d^2 + d_mean^2 * sigma_L^2 )
    """
    variance = (lead_time_mean * demand_std**2) + (demand_mean**2 * lead_time_std**2)
    return (max(0.0, variance)) ** 0.5


def safety_stock_for_service_level(sigma_dlt: float, target_service_level_pct: float) -> float:
    """Safety stock (SS) that achieves a target cycle service level:

        SS = z * sigma_DLT,   z = Phi^-1(target_service_level / 100)

    "Cycle service level" = probability of not stocking out during a single
    replenishment cycle - distinct from "fill rate", which isn't computed here.
    """
    p = max(0.0001, min(0.9999, target_service_level_pct / 100.0))
    return max(0.0, norm_ppf(p) * sigma_dlt)


@dataclass
class InventoryAnalyticsLine:
    item_id: uuid.UUID
    item_sku: str
    item_name: str
    on_hand: Decimal
    avg_daily_demand: float
    demand_std: float
    demand_std_source: str  # "history" | "assumed_cv"
    lead_time_days: int | None
    safety_stock: float | None
    reorder_point: float | None
    days_of_supply: float | None
    abc_class: str | None
    xyz_class: str | None
    status: str  # stockout | low | excess | slow_moving | healthy | no_data


def _demand_stats(daily: dict[date, Decimal], lookback_days: int, as_of: date) -> tuple[float, float, str]:
    """Mean/std of daily demand over the trailing lookback window. Days with no
    recorded consumption count as zero-demand days (the window defines the
    sample, not just the days something happened) - otherwise a single big
    consumption day would look like the average rate instead of a spike."""
    values = [float(daily.get(as_of - timedelta(days=offset), Decimal(0))) for offset in range(lookback_days)]
    mean = statistics.fmean(values) if values else 0.0
    non_zero_days = sum(1 for v in values if v > 0)
    if non_zero_days < MIN_HISTORY_DAYS_FOR_STATS:
        return mean, mean * ASSUMED_DEMAND_CV, "assumed_cv"
    return mean, statistics.pstdev(values), "history"


async def _on_hand_by_item(
    db: AsyncSession, org_id: uuid.UUID, item_ids: set[uuid.UUID]
) -> dict[uuid.UUID, Decimal]:
    """Same two-disjoint-source-sum pattern as services.mrp.compute_mrp_plan:
    active Lot.quantity_remaining for lot-tracked items, InventoryBalance for
    everything else, summed across all locations."""
    on_hand: dict[uuid.UUID, Decimal] = {}
    lot_rows = await db.execute(
        select(Lot.item_id, func.sum(Lot.quantity_remaining))
        .where(Lot.org_id == org_id, Lot.status == "active", Lot.item_id.in_(item_ids))
        .group_by(Lot.item_id)
    )
    for item_id, total in lot_rows.all():
        on_hand[item_id] = total or Decimal(0)

    balance_rows = await db.execute(
        select(InventoryBalance.item_id, func.sum(InventoryBalance.quantity_on_hand))
        .where(InventoryBalance.org_id == org_id, InventoryBalance.item_id.in_(item_ids))
        .group_by(InventoryBalance.item_id)
    )
    for item_id, total in balance_rows.all():
        on_hand[item_id] = on_hand.get(item_id, Decimal(0)) + (total or Decimal(0))
    return on_hand


def _classify_abc(value_by_item: dict[uuid.UUID, Decimal]) -> dict[uuid.UUID, str]:
    """Pareto ABC classes from a descending-sorted cumulative-value curve.

    Classified by the cumulative percentage BEFORE each item is added, not the
    inclusive cumulative after it - otherwise a single dominant item whose own
    value share alone crosses 95% would land in class C despite being the most
    valuable item in the set."""
    total = float(sum(value_by_item.values()))
    if total <= 0:
        return dict.fromkeys(value_by_item, "C")
    ordered = sorted(value_by_item.items(), key=lambda kv: kv[1], reverse=True)
    result: dict[uuid.UUID, str] = {}
    cum_prior = 0.0
    for item_id, value in ordered:
        if cum_prior < 80.0:
            result[item_id] = "A"
        elif cum_prior < 95.0:
            result[item_id] = "B"
        else:
            result[item_id] = "C"
        cum_prior += float(value) / total * 100.0
    return result


def _classify_xyz(coefficient_of_variation: float) -> str:
    if coefficient_of_variation < 0.5:
        return "X"
    if coefficient_of_variation < 1.0:
        return "Y"
    return "Z"


def _status(
    on_hand: Decimal, avg_daily_demand: float, days_of_supply: float | None, reorder_point: float | None
) -> str:
    if on_hand <= 0 and avg_daily_demand <= 0:
        return "no_data"
    if on_hand <= 0:
        return "stockout"
    if reorder_point is not None:
        if on_hand <= reorder_point:
            return "low"
    elif days_of_supply is not None and days_of_supply < DEFAULT_STOCKOUT_DAYS:
        return "low"
    if days_of_supply is not None and days_of_supply > EXCESS_THRESHOLD_DAYS:
        return "excess"
    if days_of_supply is not None and days_of_supply > SLOW_MOVING_THRESHOLD_DAYS:
        return "slow_moving"
    return "healthy"


async def compute_inventory_analytics(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    lead_time_overrides: dict[uuid.UUID, int] | None = None,
) -> list[InventoryAnalyticsLine]:
    """Read-only, computed-on-request per-item inventory analytics: reorder
    point, safety stock, days-of-supply, ABC/XYZ classification and a status
    flag. Nothing here is stored - recomputing on read keeps it always-current
    and avoids a stale-cache-invalidation problem the moment inventory or lead
    time changes.

    `lead_time_overrides` substitutes a hypothetical lead_time_days for
    specific items without touching the DB - the hook app.services.scenario
    uses to answer "what if this item's lead time changed" by diffing two
    calls to this same function, one with and one without the override."""
    items = (await db.execute(select(Item).where(Item.org_id == org_id).order_by(Item.sku))).scalars().all()
    if not items:
        return []
    item_ids = {item.id for item in items}

    as_of = datetime.now(timezone.utc).date()
    since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    daily_by_item = await daily_consumption_by_item(db, org_id, since)
    on_hand_by_item = await _on_hand_by_item(db, org_id, item_ids)
    prices = await latest_unit_prices(db, org_id, item_ids)

    stats_by_item: dict[uuid.UUID, dict] = {}
    annual_value_by_item: dict[uuid.UUID, Decimal] = {}
    for item in items:
        mean, std, source = _demand_stats(daily_by_item.get(item.id, {}), lookback_days, as_of)
        hand = on_hand_by_item.get(item.id, Decimal(0))
        days_of_supply = (float(hand) / mean) if mean > 0 else None

        effective_lead_time = (lead_time_overrides or {}).get(item.id, item.lead_time_days)
        safety_stock = reorder_point = None
        if effective_lead_time is not None:
            sigma_dlt = demand_during_lead_time_std(std, float(effective_lead_time), mean)
            safety_stock = safety_stock_for_service_level(sigma_dlt, DEFAULT_TARGET_SERVICE_LEVEL_PCT)
            reorder_point = mean * effective_lead_time + safety_stock

        price = prices.get(item.id)
        if price is not None and mean > 0:
            annual_value_by_item[item.id] = Decimal(str(mean)) * 365 * price

        stats_by_item[item.id] = {
            "on_hand": hand,
            "mean": mean,
            "std": std,
            "source": source,
            "days_of_supply": days_of_supply,
            "safety_stock": safety_stock,
            "reorder_point": reorder_point,
            "lead_time_days": effective_lead_time,
        }

    abc_by_item = _classify_abc(annual_value_by_item)

    lines: list[InventoryAnalyticsLine] = []
    for item in items:
        s = stats_by_item[item.id]
        xyz = _classify_xyz(s["std"] / s["mean"]) if s["mean"] > 0 else None
        lines.append(
            InventoryAnalyticsLine(
                item_id=item.id,
                item_sku=item.sku,
                item_name=item.name,
                on_hand=s["on_hand"],
                avg_daily_demand=s["mean"],
                demand_std=s["std"],
                demand_std_source=s["source"],
                lead_time_days=s["lead_time_days"],
                safety_stock=s["safety_stock"],
                reorder_point=s["reorder_point"],
                days_of_supply=s["days_of_supply"],
                abc_class=abc_by_item.get(item.id),
                xyz_class=xyz,
                status=_status(s["on_hand"], s["mean"], s["days_of_supply"], s["reorder_point"]),
            )
        )

    # Most urgent first (lowest days of supply), unknowns last, sku tiebreak.
    lines.sort(key=lambda ln: (ln.days_of_supply is None, ln.days_of_supply or 0.0, ln.item_sku))
    return lines
