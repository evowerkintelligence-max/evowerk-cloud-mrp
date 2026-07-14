from __future__ import annotations

from decimal import Decimal

import pytest

from tests.test_inventory import (
    _add_user_with_role,
    _create_item,
    _create_supplier,
    _dec,
    _login,
    _receive,
    _register_and_login,
)

pytestmark = pytest.mark.asyncio


async def _submit_over_threshold_request(client, test_engine, owner_email, manager_email):
    token = await _register_and_login(client, email=owner_email)
    headers = {"Authorization": f"Bearer {token}"}
    org_id = (await client.get("/orgs/me", headers=headers)).json()["id"]
    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)
    await _receive(client, token, item, supplier, 100)

    await _add_user_with_role(test_engine, org_id, manager_email, "manager")
    manager_headers = await _login(client, manager_email)

    submit = await client.post(
        f"/inventory/{item['id']}/adjust",
        json={"quantity": "60", "reason_code": "cycle_count"},
        headers=manager_headers,
    )
    assert submit.status_code == 200
    request_id = submit.json()["request"]["id"]
    return headers, manager_headers, item, request_id


async def test_owner_and_manager_can_list_pending_requests(client, test_engine):
    headers, manager_headers, item, request_id = await _submit_over_threshold_request(
        client, test_engine, "owner11@example.com", "manager11@example.com"
    )

    owner_view = await client.get("/inventory/adjustment-requests?status=pending", headers=headers)
    assert owner_view.status_code == 200
    assert any(r["id"] == request_id for r in owner_view.json())

    manager_view = await client.get(
        "/inventory/adjustment-requests?status=pending", headers=manager_headers
    )
    assert manager_view.status_code == 200
    assert any(r["id"] == request_id for r in manager_view.json())


async def test_member_cannot_list_pending_requests(client, test_engine):
    token = await _register_and_login(client, email="owner12@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    org_id = (await client.get("/orgs/me", headers=headers)).json()["id"]
    await _add_user_with_role(test_engine, org_id, "member12@example.com", "member")
    member_headers = await _login(client, "member12@example.com")

    res = await client.get("/inventory/adjustment-requests?status=pending", headers=member_headers)
    assert res.status_code == 403


async def test_default_status_filter_is_pending(client, test_engine):
    headers, _, item, request_id = await _submit_over_threshold_request(
        client, test_engine, "owner13@example.com", "manager13@example.com"
    )

    res = await client.get("/inventory/adjustment-requests", headers=headers)
    assert res.status_code == 200
    assert all(r["status"] == "pending" for r in res.json())
    assert any(r["id"] == request_id for r in res.json())


async def test_owner_can_approve_pending_request(client, test_engine):
    headers, manager_headers, item, request_id = await _submit_over_threshold_request(
        client, test_engine, "owner14@example.com", "manager14@example.com"
    )
    # requested_by should end up as the transaction's created_by after approval -
    # the ledger attributes the movement to whoever asked for it, not the approver.
    pending = await client.get("/inventory/adjustment-requests?status=pending", headers=headers)
    requested_by = next(r for r in pending.json() if r["id"] == request_id)["requested_by"]

    approve = await client.post(
        f"/inventory/adjustment-requests/{request_id}/approve",
        json={"review_note": "checked the shelf myself"},
        headers=headers,
    )
    assert approve.status_code == 200
    body = approve.json()
    assert body["status"] == "approved"
    assert body["review_note"] == "checked the shelf myself"

    detail = await client.get(f"/inventory/{item['id']}", headers=headers)
    assert _dec(detail.json()["quantity_on_hand"]) == Decimal("160")
    last_tx = detail.json()["transactions"][-1]
    assert last_tx["transaction_type"] == "adjustment"
    assert _dec(last_tx["quantity_delta"]) == Decimal("60")
    assert last_tx["created_by"] == requested_by


async def test_manager_cannot_approve(client, test_engine):
    headers, manager_headers, item, request_id = await _submit_over_threshold_request(
        client, test_engine, "owner15@example.com", "manager15@example.com"
    )

    res = await client.post(
        f"/inventory/adjustment-requests/{request_id}/approve", json={}, headers=manager_headers
    )
    assert res.status_code == 403


async def test_approving_already_reviewed_request_conflicts(client, test_engine):
    headers, manager_headers, item, request_id = await _submit_over_threshold_request(
        client, test_engine, "owner16@example.com", "manager16@example.com"
    )

    first = await client.post(
        f"/inventory/adjustment-requests/{request_id}/approve", json={}, headers=headers
    )
    assert first.status_code == 200

    second = await client.post(
        f"/inventory/adjustment-requests/{request_id}/approve", json={}, headers=headers
    )
    assert second.status_code == 409


async def test_owner_can_reject_pending_request_with_no_balance_impact(client, test_engine):
    headers, manager_headers, item, request_id = await _submit_over_threshold_request(
        client, test_engine, "owner17@example.com", "manager17@example.com"
    )

    reject = await client.post(
        f"/inventory/adjustment-requests/{request_id}/reject",
        json={"review_note": "count looks wrong, recount needed"},
        headers=headers,
    )
    assert reject.status_code == 200
    body = reject.json()
    assert body["status"] == "rejected"
    assert body["review_note"] == "count looks wrong, recount needed"

    # on-hand is unchanged - the +60 never posted
    detail = await client.get(f"/inventory/{item['id']}", headers=headers)
    assert _dec(detail.json()["quantity_on_hand"]) == Decimal("100")

    # no longer shows up in the pending queue
    pending = await client.get("/inventory/adjustment-requests?status=pending", headers=headers)
    assert not any(r["id"] == request_id for r in pending.json())


async def test_manager_cannot_reject(client, test_engine):
    headers, manager_headers, item, request_id = await _submit_over_threshold_request(
        client, test_engine, "owner18@example.com", "manager18@example.com"
    )

    res = await client.post(
        f"/inventory/adjustment-requests/{request_id}/reject", json={}, headers=manager_headers
    )
    assert res.status_code == 403
