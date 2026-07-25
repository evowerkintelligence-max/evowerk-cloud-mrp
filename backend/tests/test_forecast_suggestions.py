from __future__ import annotations

import uuid as uuid_module
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.inventory import InventoryTransaction
from app.services.demand_forecasting import ForecastPoint, bucket_weekly

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret


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


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return reg.json(), login.json()["access_token"]


async def _create_item(client, token, sku="RAW-1", name="Steel Rod"):
    res = await client.post(
        "/items",
        json={"sku": sku, "name": name, "item_type": "raw_material", "unit_of_measure": "ea"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 201
    return res.json()


async def _default_location_id(client, token) -> str:
    res = await client.get("/locations", headers={"Authorization": f"Bearer {token}"})
    return next(loc["id"] for loc in res.json() if loc["is_default"])


async def _insert_consumption(
    test_engine, *, org_id: str, item_id: str, location_id: str, created_by: str, qty_by_day_offset: dict
):
    """Backdated InventoryTransaction consumption rows - the API cannot backdate
    created_at, so history has to be seeded directly for a forecast test."""
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        for offset, qty in qty_by_day_offset.items():
            session.add(
                InventoryTransaction(
                    org_id=uuid_module.UUID(org_id),
                    item_id=uuid_module.UUID(item_id),
                    location_id=uuid_module.UUID(location_id),
                    transaction_type="consumption",
                    quantity_delta=-Decimal(str(qty)),
                    created_by=uuid_module.UUID(created_by),
                    created_at=now - timedelta(days=offset),
                )
            )
        await session.commit()


async def test_bulk_suggested_empty_org(client, test_engine):
    reg, token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    res = await client.get("/forecasts/suggested", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert body["items"] == []
    ws = date.fromisoformat(body["window_start"])
    we = date.fromisoformat(body["window_end"])
    assert (we - ws).days == 30 - 1  # default horizon, window inclusive of both ends


async def test_bulk_suggested_buckets_and_excludes_no_history(client, test_engine):
    reg, token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    org_id, user_id = reg["org_id"], reg["user_id"]
    with_history = await _create_item(client, token, sku="RAW-1")
    await _create_item(client, token, sku="RAW-2")  # no history, must not appear
    location_id = await _default_location_id(client, token)

    # 3 non-zero days in the last 7: moving average of (7+7+7+0+0+0+0)/7 == 3/day
    await _insert_consumption(
        test_engine,
        org_id=org_id,
        item_id=with_history["id"],
        location_id=location_id,
        created_by=user_id,
        qty_by_day_offset={0: 7, 1: 7, 2: 7},
    )

    res = await client.get("/forecasts/suggested?horizon_days=10", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["item_sku"] == "RAW-1"
    assert item["method_used"] == "moving_avg"
    assert item["history_points"] == 3
    # 10 daily points -> one 7-day bucket then one 3-day bucket
    assert len(item["points"]) == 2
    assert Decimal(str(item["points"][0]["quantity"])) == Decimal("21")
    assert Decimal(str(item["points"][1]["quantity"])) == Decimal("9")
    assert item["points"][0]["period_start"] == body["window_start"]
    assert (
        date.fromisoformat(item["points"][1]["period_start"])
        - date.fromisoformat(item["points"][0]["period_start"])
    ).days == 7

    # org isolation: a second org sees none of this history
    reg2, token2 = await _register_and_login(client, email="owner2@example.com", org_name="Beta")
    other = await client.get("/forecasts/suggested", headers={"Authorization": f"Bearer {token2}"})
    assert other.status_code == 200
    assert other.json()["items"] == []


async def test_bulk_suggested_validates_horizon(client, test_engine):
    reg, token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    assert (await client.get("/forecasts/suggested?horizon_days=0", headers=headers)).status_code == 400
    assert (await client.get("/forecasts/suggested?horizon_days=366", headers=headers)).status_code == 400
