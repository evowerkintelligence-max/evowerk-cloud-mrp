from __future__ import annotations

import math
import statistics
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.demand_history import daily_consumption_by_item

# Ported from a desktop app's forecast_engine.py (moving-average / simple
# exponential smoothing over a KPI time series), rewired onto this app's
# per-item consumption history (app.services.demand_history) instead of a
# generic KPI store.
DEFAULT_LOOKBACK_DAYS = 90
DEFAULT_HORIZON_DAYS = 30
MOVING_AVERAGE_WINDOW_DAYS = 7
# Below this many non-zero history days, exponential smoothing has too little
# signal to beat a plain recent-average - same threshold the source used.
MIN_NON_ZERO_DAYS_FOR_EXP_SMOOTHING = 6
EXP_SMOOTHING_ALPHA_SHORT_HISTORY = 0.5
EXP_SMOOTHING_ALPHA_LONG_HISTORY = 0.35
EXP_SMOOTHING_LONG_HISTORY_THRESHOLD_DAYS = 12
Z_95 = 1.96


def moving_average_forecast(values: list[float], horizon: int, window: int) -> list[float]:
    """Flat forecast at the average of the last `window` values."""
    if not values:
        return []
    w = max(1, min(window, len(values)))
    base = sum(values[-w:]) / w
    return [base for _ in range(horizon)]


def exp_smoothing_forecast(values: list[float], horizon: int, alpha: float) -> tuple[list[float], float]:
    """Simple exponential smoothing. Returns (forecast, residual_std) - the
    forecast is constant at the final smoothed level; residual_std measures
    how much the one-step-ahead prediction missed historically, used to widen
    the confidence interval with the horizon."""
    if not values:
        return [], 0.0
    level = values[0]
    residuals: list[float] = []
    for x in values[1:]:
        residuals.append(x - level)
        level = alpha * x + (1.0 - alpha) * level
    forecast = [level for _ in range(horizon)]
    std = statistics.pstdev(residuals) if len(residuals) > 1 else 0.0
    return forecast, std


def choose_forecast_method(non_zero_history_days: int) -> str:
    return "exp_smoothing" if non_zero_history_days >= MIN_NON_ZERO_DAYS_FOR_EXP_SMOOTHING else "moving_avg"


@dataclass
class ForecastPoint:
    period_start: date
    yhat: float
    lower: float | None
    upper: float | None


@dataclass
class DemandForecastResult:
    item_id: uuid.UUID
    method_used: str  # moving_avg | exp_smoothing | no_data
    history_points: int  # non-zero consumption days in the lookback window
    points: list[ForecastPoint]


def bucket_weekly(points: list[ForecastPoint], bucket_days: int = 7) -> list[ForecastPoint]:
    """Aggregate consecutive daily points into bucket_days-wide buckets.

    Bucket quantity is the sum of the daily yhat values; the last bucket may
    cover fewer than bucket_days days and sums only what it has. Bounds sum
    linearly because the forecast is a flat level: a level error repeats
    identically every day, so daily errors are fully correlated and interval
    widths add. If any day in a bucket lacks bounds the bucket has none.
    """
    buckets: list[ForecastPoint] = []
    for i in range(0, len(points), bucket_days):
        chunk = points[i : i + bucket_days]
        has_bounds = all(p.lower is not None and p.upper is not None for p in chunk)
        buckets.append(
            ForecastPoint(
                period_start=chunk[0].period_start,
                yhat=sum(p.yhat for p in chunk),
                lower=sum(p.lower for p in chunk) if has_bounds else None,
                upper=sum(p.upper for p in chunk) if has_bounds else None,
            )
        )
    return buckets


def _series_for_item(daily: dict[date, Decimal], lookback_days: int, as_of: date) -> list[float]:
    """Oldest -> newest daily demand over the trailing lookback window,
    zero-filled for days with no recorded consumption - same convention as
    inventory_analytics._demand_stats, so the two features never disagree on
    what "average daily demand" means for the same item."""
    return [
        float(daily.get(as_of - timedelta(days=offset), Decimal(0)))
        for offset in range(lookback_days - 1, -1, -1)
    ]


def _forecast_from_daily(
    item_id: uuid.UUID,
    daily: dict[date, Decimal],
    *,
    lookback_days: int,
    horizon_days: int,
    as_of: date,
) -> DemandForecastResult:
    """The per-item math shared by compute_demand_forecast (one item, its own
    history query) and compute_weekly_suggestions (all items, one bulk query)."""
    values = _series_for_item(daily, lookback_days, as_of)
    non_zero_days = sum(1 for v in values if v > 0)

    if non_zero_days == 0:
        return DemandForecastResult(item_id=item_id, method_used="no_data", history_points=0, points=[])

    method = choose_forecast_method(non_zero_days)
    if method == "exp_smoothing":
        alpha = (
            EXP_SMOOTHING_ALPHA_LONG_HISTORY
            if non_zero_days >= EXP_SMOOTHING_LONG_HISTORY_THRESHOLD_DAYS
            else EXP_SMOOTHING_ALPHA_SHORT_HISTORY
        )
        fc_values, resid_std = exp_smoothing_forecast(values, horizon_days, alpha)
    else:
        alpha = 0.0
        fc_values = moving_average_forecast(values, horizon_days, MOVING_AVERAGE_WINDOW_DAYS)
        resid_std = 0.0

    points: list[ForecastPoint] = []
    for i, yhat in enumerate(fc_values, start=1):
        lower = upper = None
        if resid_std > 0:
            # Widens with horizon: the h-step forecast std for ETS(A,N,N)
            # grows as sigma * sqrt(1 + (h-1)*alpha^2); a constant band would
            # understate risk further out.
            step_std = resid_std * math.sqrt(1.0 + (i - 1) * (alpha**2))
            lower = max(0.0, yhat - Z_95 * step_std)
            upper = yhat + Z_95 * step_std
        points.append(
            ForecastPoint(period_start=as_of + timedelta(days=i), yhat=max(0.0, yhat), lower=lower, upper=upper)
        )

    return DemandForecastResult(item_id=item_id, method_used=method, history_points=non_zero_days, points=points)


async def compute_demand_forecast(
    db: AsyncSession,
    org_id: uuid.UUID,
    item_id: uuid.UUID,
    *,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> DemandForecastResult:
    """Data-driven demand projection for one item, for the caller to review
    before turning it into an actual DemandForecast (upsert_forecast) -
    read-only and side-effect-free, same boundary as inventory analytics."""
    as_of = datetime.now(timezone.utc).date()
    since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    daily = (await daily_consumption_by_item(db, org_id, since, item_ids={item_id})).get(item_id, {})
    return _forecast_from_daily(
        item_id, daily, lookback_days=lookback_days, horizon_days=horizon_days, as_of=as_of
    )


@dataclass
class ItemWeeklySuggestion:
    item_id: uuid.UUID
    item_sku: str
    item_name: str
    method_used: str
    history_points: int
    points: list[ForecastPoint]  # weekly buckets, not days


async def compute_weekly_suggestions(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    as_of: date | None = None,
    item_ids: set[uuid.UUID] | None = None,
) -> list[ItemWeeklySuggestion]:
    """Weekly-bucketed suggestions for every item with consumption history
    (or the given subset). One bulk history query, then the same per-item math
    as compute_demand_forecast, then bucket_weekly. Items whose history is
    empty in the lookback window are omitted entirely: no history means no
    suggestion, not a zero suggestion.

    as_of is injectable so a route can compute one date and share it between
    this projection and its own delete window.
    """
    # ponytail: in-request loop over items; move to a background job if org
    # catalogs reach tens of thousands of items
    if as_of is None:
        as_of = datetime.now(timezone.utc).date()
    since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    daily_all = await daily_consumption_by_item(db, org_id, since, item_ids=item_ids)
    if not daily_all:
        return []

    from sqlalchemy import select

    from app.models.bom import Item

    items = {
        item.id: item
        for item in (
            await db.execute(select(Item).where(Item.org_id == org_id, Item.id.in_(daily_all.keys())))
        )
        .scalars()
        .all()
    }

    suggestions: list[ItemWeeklySuggestion] = []
    for iid, daily in daily_all.items():
        item = items.get(iid)
        if item is None:
            continue
        result = _forecast_from_daily(
            iid, daily, lookback_days=lookback_days, horizon_days=horizon_days, as_of=as_of
        )
        if result.method_used == "no_data":
            continue
        suggestions.append(
            ItemWeeklySuggestion(
                item_id=iid,
                item_sku=item.sku,
                item_name=item.name,
                method_used=result.method_used,
                history_points=result.history_points,
                points=bucket_weekly(result.points),
            )
        )
    suggestions.sort(key=lambda s: s.item_sku)
    return suggestions
