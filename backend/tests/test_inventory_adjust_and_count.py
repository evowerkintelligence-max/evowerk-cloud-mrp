from __future__ import annotations

from decimal import Decimal

import pytest

from tests._inventory_helpers import (
    _dec,
    _register_and_login,
    _create_item,
    _create_supplier,
    _receive,
    _on_hand,
    _add_user_with_role,
    _login,
)

pytestmark = pytest.mark.asyncio


async def test_adjust_up_and_down(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)
    await _receive(client, token, item, supplier, 10)

    up = await client.post(
        f"/inventory/{item['id']}/adjust",
        json={"quantity": "5", "reason_code": "correction"},
        headers=headers,
    )
    assert up.status_code == 200
    up_body = up.json()
    assert up_body["applied"] is True
    assert _dec(up_body["item"]["quantity_on_hand"]) == Decimal("15")

    down = await client.post(
        f"/inventory/{item['id']}/adjust",
        json={"quantity": "-3", "reason_code": "shrinkage"},
        headers=headers,
    )
    assert down.status_code == 200
    assert _dec(down.json()["item"]["quantity_on_hand"]) == Decimal("12")

    # An adjustment below zero is rejected, on-hand unchanged.
    too_low = await client.post(
        f"/inventory/{item['id']}/adjust",
        json={"quantity": "-100", "reason_code": "correction"},
        headers=headers,
    )
    assert too_low.status_code == 409
    assert _dec((await _on_hand(client, token, item))["quantity_on_hand"]) == Decimal("12")


async def test_adjust_zero_rejected(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)
    res = await client.post(
        f"/inventory/{item['id']}/adjust",
        json={"quantity": "0", "reason_code": "correction"},
        headers=headers,
    )
    assert res.status_code == 400


async def test_adjust_requires_reason_code(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)
    res = await client.post(
        f"/inventory/{item['id']}/adjust", json={"quantity": "1"}, headers=headers
    )
    assert res.status_code == 422


async def test_adjust_rejects_unknown_reason_code(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)
    res = await client.post(
        f"/inventory/{item['id']}/adjust",
        json={"quantity": "1", "reason_code": "made_up"},
        headers=headers,
    )
    assert res.status_code == 422


async def test_adjust_other_reason_requires_note(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)
    await _receive(client, token, item, supplier, 10)

    missing_note = await client.post(
        f"/inventory/{item['id']}/adjust",
        json={"quantity": "1", "reason_code": "other"},
        headers=headers,
    )
    assert missing_note.status_code == 422

    with_note = await client.post(
        f"/inventory/{item['id']}/adjust",
        json={"quantity": "1", "reason_code": "other", "reason_note": "warehouse fire, see incident report"},
        headers=headers,
    )
    assert with_note.status_code == 200


async def test_consume_and_adjust_reject_lot_tracked_item(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token, is_lot_tracked=True)
    assert (
        await client.post(f"/inventory/{item['id']}/consume", json={"quantity": "1"}, headers=headers)
    ).status_code == 400
    assert (
        await client.post(
            f"/inventory/{item['id']}/adjust",
            json={"quantity": "1", "reason_code": "correction"},
            headers=headers,
        )
    ).status_code == 400


async def test_manager_adjust_within_threshold_applies_immediately(client, test_engine):
    token = await _register_and_login(client, email="owner4@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    org_id = (await client.get("/orgs/me", headers=headers)).json()["id"]
    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)
    await _receive(client, token, item, supplier, 100)

    await _add_user_with_role(test_engine, org_id, "manager4@example.com", "manager")
    manager_headers = await _login(client, "manager4@example.com")

    # default threshold is 50; a delta of 10 is within it
    res = await client.post(
        f"/inventory/{item['id']}/adjust",
        json={"quantity": "10", "reason_code": "cycle_count"},
        headers=manager_headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["applied"] is True
    assert _dec(body["item"]["quantity_on_hand"]) == Decimal("110")


async def test_manager_adjust_over_threshold_creates_pending_request(client, test_engine):
    token = await _register_and_login(client, email="owner5@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    org_id = (await client.get("/orgs/me", headers=headers)).json()["id"]
    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)
    await _receive(client, token, item, supplier, 100)

    await _add_user_with_role(test_engine, org_id, "manager5@example.com", "manager")
    manager_headers = await _login(client, "manager5@example.com")

    # default threshold is 50; a delta of 60 exceeds it
    res = await client.post(
        f"/inventory/{item['id']}/adjust",
        json={"quantity": "60", "reason_code": "cycle_count"},
        headers=manager_headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["applied"] is False
    assert body["request"]["status"] == "pending"
    assert _dec(body["request"]["quantity_delta"]) == Decimal("60")
    assert _dec(body["request"]["expected_quantity_at_submit"]) == Decimal("100")

    # on-hand is unchanged - nothing was posted
    assert _dec((await _on_hand(client, token, item))["quantity_on_hand"]) == Decimal("100")


async def test_owner_adjust_over_threshold_still_applies_immediately(client):
    token = await _register_and_login(client, email="owner6@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)
    await _receive(client, token, item, supplier, 100)

    res = await client.post(
        f"/inventory/{item['id']}/adjust",
        json={"quantity": "60", "reason_code": "cycle_count"},
        headers=headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["applied"] is True
    assert _dec(body["item"]["quantity_on_hand"]) == Decimal("160")


async def test_count_computes_variance_and_applies_within_threshold(client):
    token = await _register_and_login(client, email="owner7@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)
    await _receive(client, token, item, supplier, 50)

    res = await client.post(
        f"/inventory/{item['id']}/count",
        json={"counted_quantity": "42"},
        headers=headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["applied"] is True
    assert _dec(body["item"]["quantity_on_hand"]) == Decimal("42")

    detail = await _on_hand(client, token, item)
    last_tx = detail["transactions"][-1]
    assert last_tx["transaction_type"] == "adjustment"
    assert _dec(last_tx["quantity_delta"]) == Decimal("-8")
    assert _dec(last_tx["counted_quantity"]) == Decimal("42")
    assert _dec(last_tx["expected_quantity"]) == Decimal("50")
    assert last_tx["reason"] == "cycle_count"


async def test_count_no_variance_rejected(client):
    token = await _register_and_login(client, email="owner8@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)
    await _receive(client, token, item, supplier, 50)

    res = await client.post(
        f"/inventory/{item['id']}/count",
        json={"counted_quantity": "50"},
        headers=headers,
    )
    assert res.status_code == 400


async def test_count_rejects_negative_counted_quantity(client):
    token = await _register_and_login(client, email="owner8b@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)
    await _receive(client, token, item, supplier, 50)

    res = await client.post(
        f"/inventory/{item['id']}/count",
        json={"counted_quantity": "-1"},
        headers=headers,
    )
    assert res.status_code == 422


async def test_count_against_no_balance_treats_expected_as_zero(client):
    token = await _register_and_login(client, email="owner9@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token)  # never received - no balance row

    res = await client.post(
        f"/inventory/{item['id']}/count",
        json={"counted_quantity": "5"},
        headers=headers,
    )
    assert res.status_code == 200
    assert _dec(res.json()["item"]["quantity_on_hand"]) == Decimal("5")


async def test_manager_count_over_threshold_creates_pending_request(client, test_engine):
    token = await _register_and_login(client, email="owner10@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    org_id = (await client.get("/orgs/me", headers=headers)).json()["id"]
    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)
    await _receive(client, token, item, supplier, 100)

    await _add_user_with_role(test_engine, org_id, "manager10@example.com", "manager")
    manager_headers = await _login(client, "manager10@example.com")

    # default threshold is 50; counting 20 (variance -80) exceeds it
    res = await client.post(
        f"/inventory/{item['id']}/count",
        json={"counted_quantity": "20"},
        headers=manager_headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["applied"] is False
    assert _dec(body["request"]["quantity_delta"]) == Decimal("-80")
    assert _dec(body["request"]["counted_quantity"]) == Decimal("20")
    assert body["request"]["reason_code"] == "cycle_count"
