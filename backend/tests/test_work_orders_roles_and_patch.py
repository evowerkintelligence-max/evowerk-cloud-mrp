from __future__ import annotations


import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests._work_orders_helpers import (
    PASSWORD,
    _register_and_login,
    _create_item,
    _create_active_bom,
    _raise_job,
    _give_stock,
    _member_headers,
)

pytestmark = pytest.mark.asyncio


async def test_a_member_can_read_jobs_but_cannot_touch_any_of_them(client, test_engine):
    """Every mutation is gated. Without this the gates could be deleted outright
    and the whole suite would still pass."""
    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _give_stock(client, token, seal, 100)
    job = await _raise_job(client, token, pump, qty=10)
    member = await _member_headers(client, test_engine, token)

    assert (await client.get("/work-orders", headers=member)).status_code == 200
    assert (await client.get(f"/work-orders/{job['id']}", headers=member)).status_code == 200

    raised = await client.post(
        "/work-orders",
        json={"item_id": pump["id"], "quantity_planned": "5"},
        headers=member,
    )
    assert raised.status_code == 403
    assert (
        await client.patch(
            f"/work-orders/{job['id']}", json={"quantity_planned": "20"}, headers=member
        )
    ).status_code == 403
    assert (await client.post(f"/work-orders/{job['id']}/start", headers=member)).status_code == 403
    assert (
        await client.post(
            f"/work-orders/{job['id']}/finish",
            json={"quantity_good": "10", "quantity_scrapped": "0"},
            headers=member,
        )
    ).status_code == 403
    assert (await client.post(f"/work-orders/{job['id']}/cancel", headers=member)).status_code == 403

    # None of the refusals moved anything.
    still = await client.get(f"/work-orders/{job['id']}", headers=member)
    assert still.json()["status"] == "scheduled"
    assert still.json()["quantity_planned"] == "10.0000"


async def test_a_manager_can_run_a_job_end_to_end(client, test_engine):
    """The allowed half of the same gate. Narrowing require_role to owner-only
    would pass every other test in this file."""
    from app.core.security import hash_password
    from app.models.user import User

    token = await _register_and_login(client)
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    await _give_stock(client, token, seal, 100)

    org_id = (
        await client.get("/orgs/me", headers={"Authorization": f"Bearer {token}"})
    ).json()["id"]
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(
            User(
                org_id=uuid.UUID(org_id),
                email="manager@example.com",
                hashed_password=hash_password(PASSWORD),
                role="manager",
                is_active=True,
            )
        )
        await session.commit()
    login = await client.post(
        "/auth/login", json={"email": "manager@example.com", "password": PASSWORD}
    )
    manager = {"Authorization": f"Bearer {login.json()['access_token']}"}

    raised = await client.post(
        "/work-orders", json={"item_id": pump["id"], "quantity_planned": "10"}, headers=manager
    )
    assert raised.status_code == 201, raised.text
    job = raised.json()
    assert (
        await client.patch(f"/work-orders/{job['id']}", json={"quantity_planned": "8"}, headers=manager)
    ).status_code == 200
    assert (await client.post(f"/work-orders/{job['id']}/start", headers=manager)).status_code == 200
    finished = await client.post(
        f"/work-orders/{job['id']}/finish",
        json={"quantity_good": "8", "quantity_scrapped": "0"},
        headers=manager,
    )
    assert finished.status_code == 200, finished.text


async def test_clearing_the_start_date_actually_clears_it(client):
    """null means "clear this", not "field omitted". The date drives reservation
    priority, the horizon bound and the Late badge, so silently keeping the old
    one made the job behave as though the edit never happened."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    job = await _raise_job(client, token, pump, qty=10, scheduled_start="2030-06-01")
    assert job["scheduled_start"] == "2030-06-01"

    res = await client.patch(
        f"/work-orders/{job['id']}", json={"scheduled_start": None}, headers=headers
    )
    assert res.status_code == 200, res.text
    assert res.json()["scheduled_start"] is None

    still = await client.get(f"/work-orders/{job['id']}", headers=headers)
    assert still.json()["scheduled_start"] is None


async def test_an_omitted_field_is_left_alone(client):
    """The other half: a PATCH that does not mention the date must not wipe it,
    or every quantity edit would silently unschedule the job."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    seal = await _create_item(client, token, "SEAL-1")
    pump = await _create_item(client, token, "PUMP-1")
    await _create_active_bom(client, token, pump, [(seal, 1)])
    job = await _raise_job(client, token, pump, qty=10, scheduled_start="2030-06-01")

    res = await client.patch(
        f"/work-orders/{job['id']}", json={"quantity_planned": "20"}, headers=headers
    )
    assert res.status_code == 200, res.text
    assert res.json()["scheduled_start"] == "2030-06-01"
