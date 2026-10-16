from __future__ import annotations
from tests._test_secrets import TEST_PASSWORD

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal




PASSWORD = TEST_PASSWORD


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"]


async def _create_item(
    client, token, sku, name="Item", lead_time_days=None, min_order_qty=None, safety_stock_qty=None
):
    res = await client.post(
        "/items",
        json={
            "sku": sku,
            "name": name,
            "item_type": "component",
            "unit_of_measure": "ea",
            "is_lot_tracked": False,
            "lead_time_days": lead_time_days,
            "min_order_qty": str(min_order_qty) if min_order_qty is not None else None,
            "safety_stock_qty": str(safety_stock_qty) if safety_stock_qty is not None else None,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 201
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


async def _plan(client, token, item, qty, needed_by=None):
    """Set the grid's planned quantity for an item. Defaults needed_by to two
    weeks out for tests that plan for the first time and don't care about the
    date - the endpoint itself no longer invents one, so callers that only
    want a quantity in place still need to supply something."""
    if needed_by is None and Decimal(str(qty)) > 0:
        needed_by = str(_today() + timedelta(days=14))
    body = {"quantity": str(qty)}
    if needed_by is not None:
        body["needed_by"] = needed_by
    res = await client.put(
        f"/planning/grid/{item['id']}", json=body, headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 200, res.text
    return res.json()


async def _grid(client, token):
    res = await client.get("/planning/grid", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200, res.text
    return res.json()


def _product(grid, sku):
    for row in grid["products"]:
        if row["item_sku"] == sku:
            return row
    raise AssertionError(f"{sku} not in grid: {[r['item_sku'] for r in grid['products']]}")


def _today() -> date:
    return datetime.now(timezone.utc).date()


async def _create_customer(client, token, name="Northwind Ltd"):
    res = await client.post(
        "/customers", json={"name": name}, headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 201, res.text
    return res.json()


def _soon() -> str:
    return str(_today() + timedelta(days=30))


async def _create_order(client, token, customer, item, qty=10, needed_by=None):
    res = await client.post(
        "/sales-orders",
        json={
            "customer_id": customer["id"],
            "needed_by": needed_by or _soon(),
            "lines": [{"item_id": item["id"], "quantity_ordered": str(qty), "sequence": 0}],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 201, res.text
    return res.json()


async def _raise_job(client, token, item, qty=10, scheduled_start=None):
    body = {"item_id": item["id"], "quantity_planned": str(qty)}
    if scheduled_start is not None:
        body["scheduled_start"] = scheduled_start
    res = await client.post(
        "/work-orders", json=body, headers={"Authorization": f"Bearer {token}"}
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


async def _start(client, token, job):
    return await client.post(
        f"/work-orders/{job['id']}/start", headers={"Authorization": f"Bearer {token}"}
    )
