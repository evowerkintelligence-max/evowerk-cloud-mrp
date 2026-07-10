from __future__ import annotations

import uuid as uuid_module
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.inventory import InventoryTransaction

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # satisfies policy: 12+ chars, letter + digit  # pragma: allowlist secret


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return reg.json(), login.json()["access_token"]


async def _create_item(client, token, sku="WIDGET-1", name="Widget One"):
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


async def _default_location_id(client, token) -> str:
    res = await client.get("/locations", headers={"Authorization": f"Bearer {token}"})
    return next(loc["id"] for loc in res.json() if loc["is_default"])


async def _insert_consumption(test_engine, *, org_id: str, item_id: str, location_id: str, created_by: str, qty=5):
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


async def _setup_stockout_item(client, test_engine, token, org_id, user_id, *, sku, name, lead_time_days=30):
    """No receipt -> on_hand=0 with real demand history -> status "stockout"."""
    item = await _create_item(client, token, sku, name)
    await _set_lead_time(client, token, item, lead_time_days)
    location_id = await _default_location_id(client, token)
    await _insert_consumption(
        test_engine, org_id=org_id, item_id=item["id"], location_id=location_id, created_by=user_id
    )
    return item


async def _ask(client, token, question):
    res = await client.post(
        "/ai/ask", json={"question": question}, headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 200
    return res.json()


async def test_default_intent_is_health_and_summarizes_status(client, test_engine):
    reg, token = await _register_and_login(client)
    org_id, user_id = reg["org_id"], reg["user_id"]
    await _setup_stockout_item(client, test_engine, token, org_id, user_id, sku="WIDGET-1", name="Widget One")

    body = await _ask(client, token, "how's my inventory looking?")
    assert body["intents"] == ["health"]
    assert "stockout" in body["narrative"]
    assert "WIDGET-1" in body["narrative"]
    assert body["warnings"] == []


async def test_health_question_filters_by_named_item(client, test_engine):
    reg, token = await _register_and_login(client)
    org_id, user_id = reg["org_id"], reg["user_id"]
    await _setup_stockout_item(client, test_engine, token, org_id, user_id, sku="WIDGET-1", name="Widget One")
    await _create_item(client, token, "OTHER-1", "Other Item")

    body = await _ask(client, token, "what is the status of WIDGET-1?")
    assert "1 item(s)" in body["narrative"]
    assert "WIDGET-1" in body["narrative"]
    assert "OTHER-1" not in body["narrative"]


async def test_forecast_intent_needs_a_named_item(client, test_engine):
    reg, token = await _register_and_login(client)
    org_id, user_id = reg["org_id"], reg["user_id"]
    await _setup_stockout_item(client, test_engine, token, org_id, user_id, sku="WIDGET-1", name="Widget One")

    body = await _ask(client, token, "forecast demand for next month")
    assert "forecast" in body["intents"]
    assert body["warnings"]  # no item named -> can't pick one


async def test_forecast_intent_with_named_item(client, test_engine):
    reg, token = await _register_and_login(client)
    org_id, user_id = reg["org_id"], reg["user_id"]
    await _setup_stockout_item(client, test_engine, token, org_id, user_id, sku="WIDGET-1", name="Widget One")

    body = await _ask(client, token, "forecast expected demand for WIDGET-1")
    assert "forecast" in body["intents"]
    assert body["warnings"] == []
    assert "WIDGET-1" in body["narrative"]


async def test_scenario_intent_runs_lead_time_whatif(client, test_engine):
    reg, token = await _register_and_login(client)
    org_id, user_id = reg["org_id"], reg["user_id"]
    # mean=5/day, lead_time=30 -> reorder_point=150 > on_hand=0 -> "stockout" baseline.
    await _setup_stockout_item(
        client, test_engine, token, org_id, user_id, sku="WIDGET-1", name="Widget One", lead_time_days=30
    )

    body = await _ask(client, token, "what if WIDGET-1's lead time were 5 days?")
    assert "scenario" in body["intents"]
    assert body["warnings"] == []
    assert "WIDGET-1" in body["narrative"]
    assert "5-day lead time" in body["narrative"]


async def test_scenario_intent_without_item_or_number_warns(client, test_engine):
    _, token = await _register_and_login(client)
    body = await _ask(client, token, "what if we changed the lead time?")
    assert "scenario" in body["intents"]
    assert body["warnings"]


async def test_compare_intent_runs_rca(client, test_engine):
    reg, token = await _register_and_login(client)
    org_id, user_id = reg["org_id"], reg["user_id"]
    await _setup_stockout_item(client, test_engine, token, org_id, user_id, sku="WIDGET-1", name="Widget One")

    body = await _ask(client, token, "why are items low, compare by supplier")
    assert "compare" in body["intents"]
    assert body["narrative"]


async def test_multi_intent_question_combines_narratives(client, test_engine):
    reg, token = await _register_and_login(client)
    org_id, user_id = reg["org_id"], reg["user_id"]
    await _setup_stockout_item(client, test_engine, token, org_id, user_id, sku="WIDGET-1", name="Widget One")

    body = await _ask(client, token, "give me a health summary and forecast for WIDGET-1")
    assert set(body["intents"]) >= {"health", "forecast"}


async def test_empty_question_returns_400(client, test_engine):
    _, token = await _register_and_login(client)
    res = await client.post("/ai/ask", json={"question": "   "}, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 400


async def test_org_isolation(client, test_engine):
    reg_a, token_a = await _register_and_login(client, email="a@example.com", org_name="Org A")
    org_id_a, user_id_a = reg_a["org_id"], reg_a["user_id"]
    await _setup_stockout_item(client, test_engine, token_a, org_id_a, user_id_a, sku="A-ITEM", name="A Item")

    _, token_b = await _register_and_login(client, email="b@example.com", org_name="Org B")
    body = await _ask(client, token_b, "how's my inventory looking?")
    assert "A-ITEM" not in body["narrative"]
    assert "No items match" in body["narrative"]
