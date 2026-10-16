from __future__ import annotations

from decimal import Decimal

import pytest

from tests._inventory_helpers import (
    _add_user_with_role,
    _create_item,
    _create_supplier,
    _dec,
    _login,
    _receive,
    _register_and_login,
)

pytestmark = pytest.mark.asyncio


async def test_org_me_includes_default_threshold(client):
    token = await _register_and_login(client, email="owner19@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.get("/orgs/me", headers=headers)
    assert res.status_code == 200
    assert _dec(res.json()["adjust_approval_threshold"]) == Decimal("50")


async def test_owner_can_update_threshold_and_it_takes_effect(client, test_engine):
    token = await _register_and_login(client, email="owner20@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    org_id = (await client.get("/orgs/me", headers=headers)).json()["id"]
    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)
    await _receive(client, token, item, supplier, 100)

    patch = await client.patch(
        "/orgs/me", json={"adjust_approval_threshold": "5"}, headers=headers
    )
    assert patch.status_code == 200
    assert _dec(patch.json()["adjust_approval_threshold"]) == Decimal("5")

    await _add_user_with_role(test_engine, org_id, "manager20@example.com", "manager")
    manager_headers = await _login(client, "manager20@example.com")

    # threshold is now 5 - a delta of 10 exceeds it and should queue
    res = await client.post(
        f"/inventory/{item['id']}/adjust",
        json={"quantity": "10", "reason_code": "cycle_count"},
        headers=manager_headers,
    )
    assert res.status_code == 200
    assert res.json()["applied"] is False


async def test_manager_cannot_update_threshold(client, test_engine):
    token = await _register_and_login(client, email="owner21@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    org_id = (await client.get("/orgs/me", headers=headers)).json()["id"]
    await _add_user_with_role(test_engine, org_id, "manager21@example.com", "manager")
    manager_headers = await _login(client, "manager21@example.com")

    res = await client.patch(
        "/orgs/me", json={"adjust_approval_threshold": "5"}, headers=manager_headers
    )
    assert res.status_code == 403


async def test_negative_threshold_rejected(client):
    token = await _register_and_login(client, email="owner22@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.patch(
        "/orgs/me", json={"adjust_approval_threshold": "-1"}, headers=headers
    )
    assert res.status_code == 400
