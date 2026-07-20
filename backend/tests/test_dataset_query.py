from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # satisfies policy: 12+ chars, letter + digit  # pragma: allowlist secret

CSV = b"sku,qty,region\nA-1,10,east\nA-2,5,west\nA-1,3,west\n"


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"]


async def _upload(client, token, *, content=CSV, filename="stock.csv"):
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.post(
        "/datasets", headers=headers, files={"file": (filename, content, "text/csv")}
    )
    assert res.status_code == 201
    return res.json()["id"]


async def _create_item(client, token, sku, name, *, is_lot_tracked=False):
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
    res = await client.post("/suppliers", json={"name": name}, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 201
    return res.json()


async def _receive(client, token, item, supplier, qty, *, lot_number=None):
    """Submit + receive a PO so the item's on-hand (InventoryBalance or Lot,
    depending on is_lot_tracked) is non-zero - exercises the same on_hand path
    the dataset-query join reuses from inventory_analytics._on_hand_by_item."""
    headers = {"Authorization": f"Bearer {token}"}
    po = (await client.post("/purchase-orders", json={"supplier_id": supplier["id"]}, headers=headers)).json()
    lines = await client.patch(
        f"/purchase-orders/{po['id']}",
        json=[{"item_id": item["id"], "quantity_ordered": qty, "unit_price": "1", "sequence": 0}],
        headers=headers,
    )
    line_id = lines.json()["lines"][0]["id"]
    await client.post(f"/purchase-orders/{po['id']}/submit", headers=headers)
    body = {"po_line_id": line_id, "quantity": qty}
    if lot_number:
        body["lot_number"] = lot_number
    res = await client.post(f"/purchase-orders/{po['id']}/receive", json=[body], headers=headers)
    assert res.status_code == 200


async def test_group_by_with_sum_aggregate(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)
    headers = {"Authorization": f"Bearer {token}"}

    res = await client.post(
        f"/datasets/{dataset_id}/query",
        headers=headers,
        json={
            "select": [{"column": "sku"}, {"column": "qty", "agg": "sum", "alias": "total_qty"}],
            "group_by": ["sku"],
            "order_by": "sku",
        },
    )
    assert res.status_code == 200
    rows = {r["sku"]: r["total_qty"] for r in res.json()["rows"]}
    assert rows == {"A-1": 13.0, "A-2": 5.0}


async def test_order_by_aggregate_alias(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)
    headers = {"Authorization": f"Bearer {token}"}

    res = await client.post(
        f"/datasets/{dataset_id}/query",
        headers=headers,
        json={
            "select": [{"column": "sku"}, {"column": "qty", "agg": "sum", "alias": "total_qty"}],
            "group_by": ["sku"],
            "order_by": "total_qty",
            "order_desc": True,
        },
    )
    assert res.status_code == 200
    assert [r["sku"] for r in res.json()["rows"]] == ["A-1", "A-2"]


async def test_empty_in_filter_rejected(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)
    headers = {"Authorization": f"Bearer {token}"}

    res = await client.post(
        f"/datasets/{dataset_id}/query",
        headers=headers,
        json={"select": [{"column": "sku"}], "filters": [{"column": "sku", "op": "in", "value": []}]},
    )
    assert res.status_code == 400


async def test_join_trims_whitespace_on_both_sides(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload(client, token, content=b"sku,qty\n A-1 ,10\n")

    item = await _create_item(client, token, "A-1", "Steel Rod", is_lot_tracked=False)
    supplier = await _create_supplier(client, token)
    await _receive(client, token, item, supplier, "25")

    res = await client.post(
        f"/datasets/{dataset_id}/query",
        headers=headers,
        json={
            "select": [{"column": "sku"}, {"column": "items.on_hand", "alias": "on_hand"}],
            "join_items_on": "sku",
        },
    )
    assert res.status_code == 200
    assert res.json()["rows"][0]["on_hand"] == 25.0


async def test_join_with_zero_items_in_org_returns_nulls_not_500(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)
    headers = {"Authorization": f"Bearer {token}"}

    res = await client.post(
        f"/datasets/{dataset_id}/query",
        headers=headers,
        json={
            "select": [{"column": "sku"}, {"column": "items.on_hand", "alias": "on_hand"}],
            "join_items_on": "sku",
        },
    )
    assert res.status_code == 200
    assert all(r["on_hand"] is None for r in res.json()["rows"])


async def test_unknown_column_returns_400_not_500(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)
    headers = {"Authorization": f"Bearer {token}"}

    res = await client.post(
        f"/datasets/{dataset_id}/query",
        headers=headers,
        json={"select": [{"column": "not_a_real_column"}]},
    )
    assert res.status_code == 400


async def test_filter_contains(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)
    headers = {"Authorization": f"Bearer {token}"}

    res = await client.post(
        f"/datasets/{dataset_id}/query",
        headers=headers,
        json={
            "select": [{"column": "sku"}, {"column": "region"}],
            "filters": [{"column": "region", "op": "eq", "value": "west"}],
            "order_by": "sku",
        },
    )
    assert res.status_code == 200
    assert [r["sku"] for r in res.json()["rows"]] == ["A-1", "A-2"]


async def test_join_items_on_brings_in_name_and_on_hand_non_lot_tracked(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload(client, token)

    item = await _create_item(client, token, "A-1", "Steel Rod", is_lot_tracked=False)
    supplier = await _create_supplier(client, token)
    await _receive(client, token, item, supplier, "25")

    res = await client.post(
        f"/datasets/{dataset_id}/query",
        headers=headers,
        json={
            "select": [
                {"column": "sku"},
                {"column": "items.name", "alias": "name"},
                {"column": "items.on_hand", "alias": "on_hand"},
            ],
            "filters": [{"column": "sku", "op": "eq", "value": "A-1"}],
            "join_items_on": "sku",
        },
    )
    assert res.status_code == 200
    rows = res.json()["rows"]
    assert len(rows) == 2  # two "A-1" rows in the uploaded CSV
    for row in rows:
        assert row["name"] == "Steel Rod"
        assert row["on_hand"] == 25.0


async def test_join_items_on_brings_in_on_hand_lot_tracked(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload(client, token)

    item = await _create_item(client, token, "A-2", "Bolt", is_lot_tracked=True)
    supplier = await _create_supplier(client, token)
    await _receive(client, token, item, supplier, "12", lot_number="LOT-1")

    res = await client.post(
        f"/datasets/{dataset_id}/query",
        headers=headers,
        json={
            "select": [{"column": "sku"}, {"column": "items.on_hand", "alias": "on_hand"}],
            "filters": [{"column": "sku", "op": "eq", "value": "A-2"}],
            "join_items_on": "sku",
        },
    )
    assert res.status_code == 200
    rows = res.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["on_hand"] == 12.0


async def test_filter_eq_null_matches_null_cells(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token, content=b"sku,qty,region\nA-1,10,east\nA-2,5,\n")
    headers = {"Authorization": f"Bearer {token}"}

    res = await client.post(
        f"/datasets/{dataset_id}/query",
        headers=headers,
        json={
            "select": [{"column": "sku"}],
            "filters": [{"column": "region", "op": "eq", "value": None}],
        },
    )
    assert res.status_code == 200
    assert [r["sku"] for r in res.json()["rows"]] == ["A-2"]

    res = await client.post(
        f"/datasets/{dataset_id}/query",
        headers=headers,
        json={
            "select": [{"column": "sku"}],
            "filters": [{"column": "region", "op": "neq", "value": None}],
        },
    )
    assert res.status_code == 200
    assert [r["sku"] for r in res.json()["rows"]] == ["A-1"]


async def test_filter_gt_null_rejected(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)
    headers = {"Authorization": f"Bearer {token}"}

    res = await client.post(
        f"/datasets/{dataset_id}/query",
        headers=headers,
        json={
            "select": [{"column": "sku"}],
            "filters": [{"column": "qty", "op": "gt", "value": None}],
        },
    )
    assert res.status_code == 400


async def test_items_column_rejected_without_join(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)
    headers = {"Authorization": f"Bearer {token}"}

    res = await client.post(
        f"/datasets/{dataset_id}/query",
        headers=headers,
        json={"select": [{"column": "items.name"}]},
    )
    assert res.status_code == 400
