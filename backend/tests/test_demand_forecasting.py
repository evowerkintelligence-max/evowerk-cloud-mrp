from __future__ import annotations

import uuid as uuid_module
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.inventory import InventoryTransaction
from app.services.demand_forecasting import (
    choose_forecast_method,
    exp_smoothing_forecast,
    moving_average_forecast,
)

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # satisfies policy: 12+ chars, letter + digit


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
    """Backdated InventoryTransaction consumption rows - the API can't backdate
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


async def test_no_history_reports_no_data(client, test_engine):
    reg, token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)

    res = await client.get(f"/forecasts/suggested/{item['id']}", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert body["method_used"] == "no_data"
    assert body["history_points"] == 0
    assert body["points"] == []


async def test_sparse_history_uses_moving_average(client, test_engine):
    reg, token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    org_id, user_id = reg["org_id"], reg["user_id"]
    item = await _create_item(client, token)
    location_id = await _default_location_id(client, token)

    # 3 non-zero days within the last 7 - below the exp-smoothing threshold
    # (6), so this falls back to a flat moving average of the last 7 days:
    # (7 + 7 + 7 + 0 + 0 + 0 + 0) / 7 == 3.0.
    await _insert_consumption(
        test_engine,
        org_id=org_id,
        item_id=item["id"],
        location_id=location_id,
        created_by=user_id,
        qty_by_day_offset={0: 7, 1: 7, 2: 7},
    )

    res = await client.get(f"/forecasts/suggested/{item['id']}?horizon_days=5", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert body["method_used"] == "moving_avg"
    assert body["history_points"] == 3
    assert len(body["points"]) == 5
    for point in body["points"]:
        assert Decimal(str(point["quantity"])) == Decimal("3")
        assert point["lower"] is None
        assert point["upper"] is None


async def test_rich_history_uses_exp_smoothing_with_widening_interval(client, test_engine):
    reg, token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    org_id, user_id = reg["org_id"], reg["user_id"]
    item = await _create_item(client, token)
    location_id = await _default_location_id(client, token)

    # 10 non-zero days (alternating so the residual std is non-zero) - at or
    # above the exp-smoothing threshold (6).
    pattern = {offset: (10 if offset % 2 == 0 else 0) for offset in range(20)}
    await _insert_consumption(
        test_engine,
        org_id=org_id,
        item_id=item["id"],
        location_id=location_id,
        created_by=user_id,
        qty_by_day_offset=pattern,
    )

    res = await client.get(f"/forecasts/suggested/{item['id']}?horizon_days=10", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert body["method_used"] == "exp_smoothing"
    assert body["history_points"] == 10
    points = body["points"]
    assert len(points) == 10
    for p in points:
        assert p["lower"] is not None
        assert p["upper"] is not None

    first_band = Decimal(str(points[0]["upper"])) - Decimal(str(points[0]["lower"]))
    last_band = Decimal(str(points[-1]["upper"])) - Decimal(str(points[-1]["lower"]))
    assert last_band > first_band  # confidence interval widens with horizon


async def test_unknown_item_returns_404(client, test_engine):
    reg, token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.get(f"/forecasts/suggested/{uuid_module.uuid4()}", headers=headers)
    assert res.status_code == 404


async def test_forecast_formulas():
    """Pure-function regression check, independent of the DB/API tests above."""
    assert moving_average_forecast([1.0, 2.0, 3.0, 4.0], horizon=3, window=2) == [3.5, 3.5, 3.5]
    assert moving_average_forecast([], horizon=3, window=2) == []

    forecast, resid_std = exp_smoothing_forecast([10.0, 10.0, 10.0], horizon=2, alpha=0.5)
    assert forecast == [10.0, 10.0]
    assert resid_std == pytest.approx(0.0)

    assert choose_forecast_method(5) == "moving_avg"
    assert choose_forecast_method(6) == "exp_smoothing"
