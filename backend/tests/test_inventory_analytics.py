from __future__ import annotations

import uuid as uuid_module
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.inventory import InventoryTransaction
from app.models.lot import LotTransaction
from app.services.inventory_analytics import (
    ASSUMED_DEMAND_CV,
    demand_during_lead_time_std,
    norm_ppf,
    safety_stock_for_service_level,
)

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # satisfies policy: 12+ chars, letter + digit


def _dec(v) -> Decimal:
    # Numeric values serialize scaled when DB-round-tripped, bare when
    # computed in Python - compare the number, not its string form.
    return Decimal(str(v))


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return reg.json(), login.json()["access_token"]


async def _create_item(client, token, sku="RAW-1", name="Steel Rod", is_lot_tracked=False):
    res = await client.post(
        "/items",
        json={
            "sku": sku,
            "name": name,
            "item_type": "raw_material",
            "unit_of_measure": "ea",
            "is_lot_tracked": is_lot_tracked,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 201
    return res.json()


async def _set_lead_time(client, token, item, lead_time_days):
    res = await client.patch(
        f"/items/{item['id']}",
        json={"preferred_supplier_id": None, "lead_time_days": lead_time_days},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    return res.json()


async def _create_supplier(client, token, name="Acme Metals"):
    res = await client.post(
        "/suppliers", json={"name": name}, headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 201
    return res.json()


async def _receive(client, token, item, supplier, qty, unit_price="1", lot_number=None):
    """Order `qty` at `unit_price`, submit, and receive it."""
    headers = {"Authorization": f"Bearer {token}"}
    po = await client.post("/purchase-orders", json={"supplier_id": supplier["id"]}, headers=headers)
    po_id = po.json()["id"]
    lines = await client.patch(
        f"/purchase-orders/{po_id}",
        json=[{"item_id": item["id"], "quantity_ordered": str(qty), "unit_price": unit_price, "sequence": 0}],
        headers=headers,
    )
    line_id = lines.json()["lines"][0]["id"]
    await client.post(f"/purchase-orders/{po_id}/submit", headers=headers)
    body = {"po_line_id": line_id, "quantity": str(qty)}
    if lot_number:
        body["lot_number"] = lot_number
    recv = await client.post(f"/purchase-orders/{po_id}/receive", json=[body], headers=headers)
    assert recv.status_code == 200


async def _default_location_id(client, token) -> str:
    res = await client.get("/locations", headers={"Authorization": f"Bearer {token}"})
    return next(loc["id"] for loc in res.json() if loc["is_default"])


async def _insert_consumption(
    test_engine, *, org_id: str, item_id: str, location_id: str, created_by: str, qty_by_day_offset: dict
):
    """Backdated InventoryTransaction consumption rows - the API can't backdate
    created_at, so history has to be seeded directly for a demand-stats test."""
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


async def _insert_lot_consumption(test_engine, *, org_id: str, lot_id: str, created_by: str, qty_by_day_offset: dict):
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        for offset, qty in qty_by_day_offset.items():
            session.add(
                LotTransaction(
                    org_id=uuid_module.UUID(org_id),
                    lot_id=uuid_module.UUID(lot_id),
                    transaction_type="consumption",
                    quantity=Decimal(str(qty)),
                    created_by=uuid_module.UUID(created_by),
                    created_at=now - timedelta(days=offset),
                )
            )
        await session.commit()


def _by_sku(analytics_json) -> dict:
    return {line["item_sku"]: line for line in analytics_json["lines"]}


async def test_no_lead_time_reports_nulls_not_zero(client, test_engine):
    reg, token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token, "RAW-1")
    supplier = await _create_supplier(client, token)
    await _receive(client, token, item, supplier, 100)

    res = await client.get("/inventory/analytics", headers=headers)
    assert res.status_code == 200
    line = _by_sku(res.json())["RAW-1"]
    assert line["lead_time_days"] is None
    assert line["safety_stock"] is None
    assert line["reorder_point"] is None
    assert line["days_of_supply"] is None  # no consumption history -> no demand rate
    assert line["status"] == "healthy"


async def test_history_based_demand_stats_and_reorder_point(client, test_engine):
    reg, token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    org_id = reg["org_id"]
    user_id = reg["user_id"]
    item = await _create_item(client, token, "RAW-2")
    await _set_lead_time(client, token, item, 7)
    supplier = await _create_supplier(client, token)
    await _receive(client, token, item, supplier, 350)
    location_id = await _default_location_id(client, token)

    # Constant demand of 5/day for all 90 lookback days -> std is exactly 0,
    # so safety_stock is 0 and reorder_point is exactly mean * lead_time_days.
    await _insert_consumption(
        test_engine,
        org_id=org_id,
        item_id=item["id"],
        location_id=location_id,
        created_by=user_id,
        qty_by_day_offset={d: 5 for d in range(90)},
    )

    res = await client.get("/inventory/analytics", headers=headers)
    line = _by_sku(res.json())["RAW-2"]
    assert line["demand_std_source"] == "history"
    assert line["avg_daily_demand"] == pytest.approx(5.0)
    assert line["demand_std"] == pytest.approx(0.0)
    assert line["safety_stock"] == pytest.approx(0.0)
    assert line["reorder_point"] == pytest.approx(35.0)  # 5 * 7 + 0
    assert line["days_of_supply"] == pytest.approx(70.0)  # 350 / 5
    assert line["status"] == "healthy"


async def test_sparse_history_falls_back_to_assumed_cv(client, test_engine):
    reg, token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    org_id, user_id = reg["org_id"], reg["user_id"]
    item = await _create_item(client, token, "RAW-3")
    supplier = await _create_supplier(client, token)
    await _receive(client, token, item, supplier, 100)
    location_id = await _default_location_id(client, token)

    # Only 2 non-zero days of history - below MIN_HISTORY_DAYS_FOR_STATS (5).
    await _insert_consumption(
        test_engine,
        org_id=org_id,
        item_id=item["id"],
        location_id=location_id,
        created_by=user_id,
        qty_by_day_offset={0: 9, 1: 9},
    )

    res = await client.get("/inventory/analytics", headers=headers)
    line = _by_sku(res.json())["RAW-3"]
    assert line["demand_std_source"] == "assumed_cv"
    expected_mean = 18 / 90
    assert line["avg_daily_demand"] == pytest.approx(expected_mean)
    assert line["demand_std"] == pytest.approx(expected_mean * ASSUMED_DEMAND_CV)


async def test_abc_classification_by_value_and_missing_price(client, test_engine):
    reg, token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    org_id, user_id = reg["org_id"], reg["user_id"]
    supplier = await _create_supplier(client, token)
    location_id = await _default_location_id(client, token)

    item_high = await _create_item(client, token, "HIGH", "High value")
    item_mid = await _create_item(client, token, "MID", "Mid value")
    item_low = await _create_item(client, token, "LOW", "No price on file")

    await _receive(client, token, item_high, supplier, 100, unit_price="100")
    await _receive(client, token, item_mid, supplier, 100, unit_price="10")
    # item_low is never purchased - no unit_price to rank it by.

    # Same consumption pattern for all three so mean cancels out of the A/B
    # split - only the unit_price ratio (100 : 10) determines the classes.
    same_pattern = {d: 10 for d in range(5)}
    for item in (item_high, item_mid, item_low):
        await _insert_consumption(
            test_engine,
            org_id=org_id,
            item_id=item["id"],
            location_id=location_id,
            created_by=user_id,
            qty_by_day_offset=same_pattern,
        )

    res = await client.get("/inventory/analytics", headers=headers)
    lines = _by_sku(res.json())
    assert lines["HIGH"]["abc_class"] == "A"
    assert lines["MID"]["abc_class"] == "B"
    assert lines["LOW"]["abc_class"] is None


async def test_lot_tracked_consumption_pulled_from_lot_transactions(client, test_engine):
    reg, token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    org_id, user_id = reg["org_id"], reg["user_id"]
    item = await _create_item(client, token, "LOT-1", is_lot_tracked=True)
    supplier = await _create_supplier(client, token)
    await _receive(client, token, item, supplier, 200, lot_number="L1")

    lots_res = await client.get(f"/lots?item_id={item['id']}", headers=headers)
    lot_id = lots_res.json()[0]["id"]

    await _insert_lot_consumption(
        test_engine,
        org_id=org_id,
        lot_id=lot_id,
        created_by=user_id,
        qty_by_day_offset={d: 4 for d in range(90)},
    )

    res = await client.get("/inventory/analytics", headers=headers)
    line = _by_sku(res.json())["LOT-1"]
    assert line["avg_daily_demand"] == pytest.approx(4.0)
    assert line["demand_std_source"] == "history"
    # Unchanged: lot on-hand, not affected by the synthetic ledger rows above.
    assert _dec(line["on_hand"]) == Decimal("200")


async def test_org_isolation(client, test_engine):
    _, token_a = await _register_and_login(client, email="a@example.com", org_name="Org A")
    await _create_item(client, token_a, "A-ITEM")
    _, token_b = await _register_and_login(client, email="b@example.com", org_name="Org B")

    res = await client.get("/inventory/analytics", headers={"Authorization": f"Bearer {token_b}"})
    assert res.status_code == 200
    assert res.json()["lines"] == []


async def test_safety_stock_formulas():
    """Pure-function regression check, independent of the DB/API test above -
    fails immediately if the ported math itself ever changes."""
    assert norm_ppf(0.95) == pytest.approx(1.6448536, abs=1e-6)
    sigma_dlt = demand_during_lead_time_std(demand_std=2.0, lead_time_mean=9.0, demand_mean=5.0)
    assert sigma_dlt == pytest.approx(6.0)  # sqrt(9 * 2^2) = sqrt(36) = 6
    safety_stock = safety_stock_for_service_level(sigma_dlt, 95.0)
    assert safety_stock == pytest.approx(1.6448536 * 6.0, abs=1e-4)
