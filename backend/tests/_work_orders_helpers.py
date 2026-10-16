from __future__ import annotations
from tests._test_secrets import TEST_PASSWORD


import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker



PASSWORD = TEST_PASSWORD


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"]


async def _create_item(client, token, sku, name="Widget", is_lot_tracked=False):
    res = await client.post(
        "/items",
        json={
            "sku": sku,
            "name": name,
            "item_type": "finished_good",
            "unit_of_measure": "ea",
            "is_lot_tracked": is_lot_tracked,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 201, res.text
    return res.json()


async def _create_active_bom(client, token, produced_item, lines):
    """lines: list of (component_item, qty_per)."""
    headers = {"Authorization": f"Bearer {token}"}
    bom = await client.post("/boms", json={"item_id": produced_item["id"]}, headers=headers)
    assert bom.status_code == 201
    bom_data = bom.json()
    patch = await client.patch(
        f"/boms/{bom_data['id']}",
        json={
            "lines": [
                {
                    "component_item_id": component["id"],
                    "quantity": str(qty),
                    "sequence": i,
                    "scrap_pct": "0",
                }
                for i, (component, qty) in enumerate(lines)
            ],
            "expected_updated_at": bom_data["updated_at"],
        },
        headers=headers,
    )
    assert patch.status_code == 200
    activate = await client.post(f"/boms/{bom_data['id']}/activate", headers=headers)
    assert activate.status_code == 200
    return bom_data["id"]


async def _raise_job(client, token, item, qty=10, scheduled_start=None):
    body = {"item_id": item["id"], "quantity_planned": str(qty)}
    if scheduled_start is not None:
        body["scheduled_start"] = scheduled_start
    res = await client.post(
        "/work-orders", json=body, headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 201, res.text
    return res.json()


def _in_days(days: int) -> str:
    return (datetime.now(timezone.utc).date() + timedelta(days=days)).isoformat()


async def _give_stock(client, token, item, qty):
    res = await client.post(
        f"/inventory/{item['id']}/adjust",
        json={"quantity": str(qty), "reason_code": "found_stock"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["applied"] is True
    return res


async def _receive_into_lot(client, token, item, qty, lot_number):
    """Gives a lot-tracked item real on-hand stock via a received PO lot. Lot
    stock cannot be seeded any other way, so this needs a supplier too."""
    headers = {"Authorization": f"Bearer {token}"}
    supplier = await client.post(
        "/suppliers", json={"name": f"Acme Metals {lot_number}"}, headers=headers
    )
    assert supplier.status_code == 201, supplier.text
    po = await client.post(
        "/purchase-orders", json={"supplier_id": supplier.json()["id"]}, headers=headers
    )
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
        json=[{"po_line_id": line_id, "quantity": str(qty), "lot_number": lot_number}],
        headers=headers,
    )
    assert recv.status_code == 200, recv.text


async def _start(client, token, job):
    return await client.post(
        f"/work-orders/{job['id']}/start", headers={"Authorization": f"Bearer {token}"}
    )


async def _finish(client, token, job, good, scrap="0", **extra):
    body = {"quantity_good": str(good), "quantity_scrapped": str(scrap)}
    body.update(extra)
    return await client.post(
        f"/work-orders/{job['id']}/finish",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )


async def _running_job(client, token, seal, pump, planned=50, stock=200):
    await _give_stock(client, token, seal, stock)
    job = await _raise_job(client, token, pump, qty=planned)
    assert (await _start(client, token, job)).status_code == 200
    return job


async def _cancel(client, token, job):
    return await client.post(
        f"/work-orders/{job['id']}/cancel", headers={"Authorization": f"Bearer {token}"}
    )


async def _member_headers(client, test_engine, token, email="member@example.com"):
    """A read-only member in the same org. No API creates one, so this writes
    the row directly, following test_customers.py."""
    from app.core.security import hash_password
    from app.models.user import User

    org_id = (
        await client.get("/orgs/me", headers={"Authorization": f"Bearer {token}"})
    ).json()["id"]
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(
            User(
                org_id=uuid.UUID(org_id),
                email=email,
                hashed_password=hash_password(PASSWORD),
                role="member",
                is_active=True,
            )
        )
        await session.commit()
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _create_operator(client, token, name="J Kim"):
    res = await client.post(
        "/operators", json={"name": name}, headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 201, res.text
    return res.json()
