from __future__ import annotations



import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests._work_orders_helpers import (
    _register_and_login,
    _create_item,
    _create_active_bom,
    _raise_job,
    _give_stock,
    _create_operator,
)

pytestmark = pytest.mark.asyncio


async def test_assign_operator_to_scheduled_job(client):
    token = await _register_and_login(client)
    item = await _create_item(client, token, "SKU-A")
    component = await _create_item(client, token, "SKU-B")
    await _create_active_bom(client, token, item, [(component, 1)])
    job = await _raise_job(client, token, item)
    operator = await _create_operator(client, token)

    res = await client.patch(
        f"/work-orders/{job['id']}",
        json={"assigned_operator_id": operator["id"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    assert res.json()["assigned_operator_id"] == operator["id"]


async def test_assign_operator_to_in_progress_job(client):
    token = await _register_and_login(client)
    item = await _create_item(client, token, "SKU-A")
    component = await _create_item(client, token, "SKU-B")
    await _create_active_bom(client, token, item, [(component, 1)])
    await _give_stock(client, token, component, 100)
    job = await _raise_job(client, token, item)
    headers = {"Authorization": f"Bearer {token}"}
    start = await client.post(f"/work-orders/{job['id']}/start", headers=headers)
    assert start.status_code == 200
    operator = await _create_operator(client, token)

    # A running job 409s on a quantity/date/notes edit, but assigning who's
    # actually running it is exactly the thing you'd want to do mid-job.
    res = await client.patch(
        f"/work-orders/{job['id']}", json={"assigned_operator_id": operator["id"]}, headers=headers
    )
    assert res.status_code == 200
    assert res.json()["assigned_operator_id"] == operator["id"]


async def test_editing_quantity_on_in_progress_job_still_409s(client):
    token = await _register_and_login(client)
    item = await _create_item(client, token, "SKU-A")
    component = await _create_item(client, token, "SKU-B")
    await _create_active_bom(client, token, item, [(component, 1)])
    await _give_stock(client, token, component, 100)
    job = await _raise_job(client, token, item)
    headers = {"Authorization": f"Bearer {token}"}
    await client.post(f"/work-orders/{job['id']}/start", headers=headers)

    res = await client.patch(
        f"/work-orders/{job['id']}", json={"quantity_planned": "5"}, headers=headers
    )
    assert res.status_code == 409


async def test_clear_assigned_operator(client):
    token = await _register_and_login(client)
    item = await _create_item(client, token, "SKU-A")
    component = await _create_item(client, token, "SKU-B")
    await _create_active_bom(client, token, item, [(component, 1)])
    job = await _raise_job(client, token, item)
    operator = await _create_operator(client, token)
    headers = {"Authorization": f"Bearer {token}"}
    await client.patch(f"/work-orders/{job['id']}", json={"assigned_operator_id": operator["id"]}, headers=headers)

    res = await client.patch(f"/work-orders/{job['id']}", json={"assigned_operator_id": None}, headers=headers)
    assert res.status_code == 200
    assert res.json()["assigned_operator_id"] is None


async def test_a_mixed_patch_writes_exactly_one_audit_row(client, test_engine):
    """Operator + quantity in one PATCH is a single edit, so it gets a single
    work_order.updated row carrying both - not one row per field applied."""
    from app.models.audit import AuditLog

    token = await _register_and_login(client)
    item = await _create_item(client, token, "SKU-A")
    component = await _create_item(client, token, "SKU-B")
    await _create_active_bom(client, token, item, [(component, 1)])
    job = await _raise_job(client, token, item, qty=10)
    operator = await _create_operator(client, token)
    headers = {"Authorization": f"Bearer {token}"}

    res = await client.patch(
        f"/work-orders/{job['id']}",
        json={"assigned_operator_id": operator["id"], "quantity_planned": "20"},
        headers=headers,
    )
    assert res.status_code == 200, res.text

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        rows = (
            await session.execute(select(AuditLog).where(AuditLog.action == "work_order.updated"))
        ).scalars().all()

    assert len(rows) == 1, f"expected one audit row for one PATCH, got {len(rows)}"
    assert rows[0].event_metadata["assigned_operator_id"] == operator["id"]


async def test_an_operator_only_patch_writes_exactly_one_audit_row(client, test_engine):
    from app.models.audit import AuditLog

    token = await _register_and_login(client)
    item = await _create_item(client, token, "SKU-A")
    component = await _create_item(client, token, "SKU-B")
    await _create_active_bom(client, token, item, [(component, 1)])
    job = await _raise_job(client, token, item)
    operator = await _create_operator(client, token)

    res = await client.patch(
        f"/work-orders/{job['id']}",
        json={"assigned_operator_id": operator["id"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200, res.text

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        rows = (
            await session.execute(select(AuditLog).where(AuditLog.action == "work_order.updated"))
        ).scalars().all()

    assert len(rows) == 1
    assert rows[0].event_metadata["assigned_operator_id"] == operator["id"]


async def test_assign_unknown_operator_404(client):
    token = await _register_and_login(client)
    item = await _create_item(client, token, "SKU-A")
    component = await _create_item(client, token, "SKU-B")
    await _create_active_bom(client, token, item, [(component, 1)])
    job = await _raise_job(client, token, item)

    res = await client.patch(
        f"/work-orders/{job['id']}",
        json={"assigned_operator_id": "00000000-0000-0000-0000-000000000000"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 404
