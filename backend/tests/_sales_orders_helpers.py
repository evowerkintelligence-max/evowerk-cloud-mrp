from __future__ import annotations

from datetime import datetime, timedelta, timezone


PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"]


async def _create_customer(client, token, name="Northwind Ltd"):
    res = await client.post(
        "/customers", json={"name": name}, headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 201, res.text
    return res.json()


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


async def _create_order(client, token, customer, item, qty=10, needed_by="2030-06-01"):
    res = await client.post(
        "/sales-orders",
        json={
            "customer_id": customer["id"],
            "needed_by": needed_by,
            "lines": [{"item_id": item["id"], "quantity_ordered": str(qty), "sequence": 0}],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 201, res.text
    return res.json()


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


def _soon() -> str:
    return (datetime.now(timezone.utc).date() + timedelta(days=30)).isoformat()


async def _create_active_bom(client, token, produced_item, lines):
    """lines: list of (component_item, qty_per). Creates a draft BOM, sets its
    lines, and activates it."""
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


async def _plan_lines(client, token):
    res = await client.get("/mrp/plan", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200, res.text
    return {line["item_sku"]: line for line in res.json()["lines"]}


async def _create_two_line_order(client, token, customer, first, second, needed_by="2030-06-01"):
    res = await client.post(
        "/sales-orders",
        json={
            "customer_id": customer["id"],
            "needed_by": needed_by,
            "lines": [
                {"item_id": first[0]["id"], "quantity_ordered": str(first[1]), "sequence": 0},
                {"item_id": second[0]["id"], "quantity_ordered": str(second[1]), "sequence": 1},
            ],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 201, res.text
    return res.json()


async def _add_member(test_engine, client, headers):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.security import hash_password
    from app.models.user import User

    org_id = (await client.get("/orgs/me", headers=headers)).json()["id"]
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        import uuid as uuid_module

        session.add(
            User(
                org_id=uuid_module.UUID(org_id),
                email="member@example.com",
                hashed_password=hash_password(PASSWORD),
                role="member",
                is_active=True,
            )
        )
        await session.commit()
    login = await client.post(
        "/auth/login", json={"email": "member@example.com", "password": PASSWORD}
    )
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _yesterday() -> str:
    return (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
