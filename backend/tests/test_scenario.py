from __future__ import annotations

import uuid as uuid_module
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.inventory import InventoryTransaction

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


async def _create_item(client, token, sku="ITEM-1", name="Widget"):
    res = await client.post(
        "/items",
        json={"sku": sku, "name": name, "item_type": "raw_material", "unit_of_measure": "ea"},
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


async def _create_supplier(client, token, name="Acme Metals"):
    res = await client.post(
        "/suppliers", json={"name": name}, headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 201
    return res.json()


async def _receive(client, token, item, supplier, qty):
    headers = {"Authorization": f"Bearer {token}"}
    po = await client.post("/purchase-orders", json={"supplier_id": supplier["id"]}, headers=headers)
    po_id = po.json()["id"]
    lines = await client.patch(
        f"/purchase-orders/{po_id}",
        json=[{"item_id": item["id"], "quantity_ordered": str(qty), "unit_price": "1", "sequence": 0}],
        headers=headers,
    )
    line_id = lines.json()["lines"][0]["id"]
    await client.post(f"/purchase-orders/{po_id}/submit", headers=headers)
    recv = await client.post(
        f"/purchase-orders/{po_id}/receive",
        json=[{"po_line_id": line_id, "quantity": str(qty)}],
        headers=headers,
    )
    assert recv.status_code == 200


async def _default_location_id(client, token) -> str:
    res = await client.get("/locations", headers={"Authorization": f"Bearer {token}"})
    return next(loc["id"] for loc in res.json() if loc["is_default"])


async def _insert_consumption(
    test_engine, *, org_id: str, item_id: str, location_id: str, created_by: str, qty=5
):
    """Constant demand for all 90 lookback days -> deterministic mean, zero std."""
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        for offset in range(90):
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


async def _setup_item_with_lead_time(client, test_engine, token, org_id, user_id, *, sku, lead_time_days, on_hand):
    item = await _create_item(client, token, sku)
    await _set_lead_time(client, token, item, lead_time_days)
    supplier = await _create_supplier(client, token, f"Supplier-{sku}")
    await _receive(client, token, item, supplier, on_hand)
    location_id = await _default_location_id(client, token)
    await _insert_consumption(
        test_engine, org_id=org_id, item_id=item["id"], location_id=location_id, created_by=user_id, qty=5
    )
    return item


def _by_sku(response_json) -> dict:
    return {d["item_sku"]: d for d in response_json["deltas"]}


async def test_shortening_lead_time_improves(client, test_engine):
    reg, token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    org_id, user_id = reg["org_id"], reg["user_id"]

    # mean=5/day, lead_time=30 -> reorder_point=150 > on_hand=100 -> "low".
    item = await _setup_item_with_lead_time(
        client, test_engine, token, org_id, user_id, sku="SHORTEN", lead_time_days=30, on_hand=100
    )

    res = await client.post(
        "/inventory/scenario-compare",
        json={"lead_time_overrides": {item["id"]: 5}},
        headers=headers,
    )
    assert res.status_code == 200
    delta = _by_sku(res.json())["SHORTEN"]
    assert delta["baseline_status"] == "low"
    assert delta["scenario_status"] == "healthy"
    assert delta["classification"] == "improved"
    assert delta["baseline_lead_time_days"] == 30
    assert delta["scenario_lead_time_days"] == 5
    assert delta["reorder_point_delta"] < 0  # 5*5 - 5*30 = -125
    assert delta["scenario_reorder_point"] == pytest.approx(25.0)
    assert delta["baseline_reorder_point"] == pytest.approx(150.0)


async def test_lengthening_lead_time_regresses(client, test_engine):
    reg, token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    org_id, user_id = reg["org_id"], reg["user_id"]

    # mean=5/day, lead_time=5 -> reorder_point=25 < on_hand=100 -> "healthy".
    item = await _setup_item_with_lead_time(
        client, test_engine, token, org_id, user_id, sku="LENGTHEN", lead_time_days=5, on_hand=100
    )

    res = await client.post(
        "/inventory/scenario-compare",
        json={"lead_time_overrides": {item["id"]: 30}},
        headers=headers,
    )
    assert res.status_code == 200
    delta = _by_sku(res.json())["LENGTHEN"]
    assert delta["baseline_status"] == "healthy"
    assert delta["scenario_status"] == "low"
    assert delta["classification"] == "regressed"
    assert delta["reorder_point_delta"] > 0


async def test_same_lead_time_is_unchanged(client, test_engine):
    reg, token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    org_id, user_id = reg["org_id"], reg["user_id"]

    item = await _setup_item_with_lead_time(
        client, test_engine, token, org_id, user_id, sku="SAME", lead_time_days=10, on_hand=100
    )

    res = await client.post(
        "/inventory/scenario-compare",
        json={"lead_time_overrides": {item["id"]: 10}},
        headers=headers,
    )
    assert res.status_code == 200
    delta = _by_sku(res.json())["SAME"]
    assert delta["classification"] == "unchanged"
    assert delta["reorder_point_delta"] == pytest.approx(0.0)
    assert delta["baseline_status"] == delta["scenario_status"]


async def test_unknown_item_returns_404(client, test_engine):
    _, token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.post(
        "/inventory/scenario-compare",
        json={"lead_time_overrides": {str(uuid_module.uuid4()): 10}},
        headers=headers,
    )
    assert res.status_code == 404


async def test_negative_lead_time_returns_400(client, test_engine):
    _, token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)
    res = await client.post(
        "/inventory/scenario-compare",
        json={"lead_time_overrides": {item["id"]: -1}},
        headers=headers,
    )
    assert res.status_code == 400


async def test_empty_overrides_returns_400(client, test_engine):
    _, token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.post(
        "/inventory/scenario-compare", json={"lead_time_overrides": {}}, headers=headers
    )
    assert res.status_code == 400


async def test_org_isolation(client, test_engine):
    _, token_a = await _register_and_login(client, email="a@example.com", org_name="Org A")
    item_a = await _create_item(client, token_a, "A-ITEM")

    _, token_b = await _register_and_login(client, email="b@example.com", org_name="Org B")
    res = await client.post(
        "/inventory/scenario-compare",
        json={"lead_time_overrides": {item_a["id"]: 10}},
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert res.status_code == 404
