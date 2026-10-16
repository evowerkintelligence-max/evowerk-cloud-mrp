from __future__ import annotations
from tests._test_secrets import TEST_PASSWORD

from decimal import Decimal


PASSWORD = TEST_PASSWORD


def _dec(v) -> Decimal:
    return Decimal(str(v))


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"]


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


async def _create_supplier(client, token, name="Acme Metals"):
    res = await client.post(
        "/suppliers", json={"name": name}, headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 201
    return res.json()


async def _receive(client, token, item, supplier, qty, lot_number=None):
    """Order `qty`, submit, and receive it. Returns the PO id."""
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
    body = {"po_line_id": line_id, "quantity": str(qty)}
    if lot_number:
        body["lot_number"] = lot_number
    recv = await client.post(f"/purchase-orders/{po_id}/receive", json=[body], headers=headers)
    assert recv.status_code == 200
    return po_id


async def _on_hand(client, token, item):
    res = await client.get(f"/inventory/{item['id']}", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    return res.json()


async def _add_user_with_role(test_engine, org_id: str, email: str, role: str) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker
    import uuid as uuid_module

    from app.core.security import hash_password
    from app.models.user import User

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(
            User(
                org_id=uuid_module.UUID(org_id),
                email=email,
                hashed_password=hash_password(PASSWORD),
                role=role,
                is_active=True,
            )
        )
        await session.commit()


async def _login(client, email: str) -> dict[str, str]:
    res = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert res.status_code == 200
    return {"Authorization": f"Bearer {res.json()['access_token']}"}
