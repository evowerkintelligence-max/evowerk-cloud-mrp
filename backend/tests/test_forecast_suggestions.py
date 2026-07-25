from __future__ import annotations

from datetime import date, timedelta

from app.services.demand_forecasting import ForecastPoint, bucket_weekly


def _daily(n: int, yhat: float = 3.0, with_bounds: bool = True) -> list[ForecastPoint]:
    start = date(2026, 8, 1)
    return [
        ForecastPoint(
            period_start=start + timedelta(days=i),
            yhat=yhat,
            lower=(yhat - 1.0) if with_bounds else None,
            upper=(yhat + 1.0) if with_bounds else None,
        )
        for i in range(n)
    ]


def test_bucket_weekly_sums_full_and_partial_buckets():
    buckets = bucket_weekly(_daily(30))
    assert len(buckets) == 5  # 4 full weeks + one 2-day partial
    assert [b.period_start for b in buckets] == [
        date(2026, 8, 1) + timedelta(days=7 * k) for k in range(5)
    ]
    assert [b.yhat for b in buckets] == [21.0, 21.0, 21.0, 21.0, 6.0]
    # bucket totals preserve the daily total
    assert sum(b.yhat for b in buckets) == sum(p.yhat for p in _daily(30))


def test_bucket_weekly_sums_bounds_linearly():
    buckets = bucket_weekly(_daily(14))
    assert buckets[0].lower == 14.0  # 7 * (3 - 1)
    assert buckets[0].upper == 28.0  # 7 * (3 + 1)


def test_bucket_weekly_without_bounds_keeps_none():
    buckets = bucket_weekly(_daily(10, with_bounds=False))
    assert len(buckets) == 2
    assert buckets[0].lower is None and buckets[0].upper is None
    assert buckets[1].yhat == 9.0  # 3-day partial bucket


def test_bucket_weekly_empty():
    assert bucket_weekly([]) == []
