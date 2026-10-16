from __future__ import annotations

import pytest

from tests._bom_helpers import (
    _register_and_login,
    _create_item,
)

pytestmark = pytest.mark.asyncio


async def test_item_create_and_duplicate_sku_rejected(client):
    token, _ = await _register_and_login(client)
    await _create_item(client, token, sku="DUPE-1")

    res = await client.post(
        "/items",
        json={"sku": "DUPE-1", "name": "Other", "item_type": "component", "unit_of_measure": "ea"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 409


async def test_item_unit_create_list_and_duplicate_rejected(client):
    token, _ = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token, sku="UNIT-1", name="Screws", item_type="raw_material")

    create = await client.post(
        f"/items/{item['id']}/units", json={"code": "box", "factor_to_base": "100"}, headers=headers
    )
    assert create.status_code == 201
    body = create.json()
    assert body["code"] == "box"
    assert body["factor_to_base"] == "100.000000"

    listed = await client.get(f"/items/{item['id']}/units", headers=headers)
    assert listed.status_code == 200
    assert [u["code"] for u in listed.json()] == ["box"]

    dupe = await client.post(
        f"/items/{item['id']}/units", json={"code": "box", "factor_to_base": "50"}, headers=headers
    )
    assert dupe.status_code == 409


async def test_item_unit_rejects_non_positive_factor(client):
    token, _ = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token, sku="UNIT-2", name="Bolts", item_type="raw_material")

    res = await client.post(
        f"/items/{item['id']}/units", json={"code": "box", "factor_to_base": "0"}, headers=headers
    )
    assert res.status_code == 400


async def test_item_unit_rejects_overlong_code(client):
    # code is String(20) - an over-long value must be rejected with a clean
    # 400 before it ever reaches the DB (a DataError there would otherwise
    # surface as an unhandled 500, since only IntegrityError is caught).
    token, _ = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token, sku="UNIT-3", name="Washers", item_type="raw_material")

    res = await client.post(
        f"/items/{item['id']}/units",
        json={"code": "a" * 21, "factor_to_base": "100"},
        headers=headers,
    )
    assert res.status_code == 400


async def test_item_unit_delete_blocked_when_in_use(client):
    token, _ = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    parent = await _create_item(client, token, sku="UNIT-FG", name="Widget", item_type="finished_good")
    comp = await _create_item(client, token, sku="UNIT-3", name="Screws", item_type="raw_material")

    unit = await client.post(
        f"/items/{comp['id']}/units", json={"code": "box", "factor_to_base": "100"}, headers=headers
    )
    unit_id = unit.json()["id"]

    bom = await client.post("/boms", json={"item_id": parent["id"]}, headers=headers)
    bom_data = bom.json()
    await client.patch(
        f"/boms/{bom_data['id']}",
        json={
            "lines": [
                {
                    "component_item_id": comp["id"],
                    "quantity": "1",
                    "sequence": 0,
                    "unit_id": unit_id,
                    "unit_quantity": "1",
                }
            ],
            "expected_updated_at": bom_data["updated_at"],
        },
        headers=headers,
    )

    delete = await client.delete(f"/items/{comp['id']}/units/{unit_id}", headers=headers)
    assert delete.status_code == 409


async def test_item_unit_delete_succeeds_when_unused(client):
    token, _ = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token, sku="UNIT-4", name="Nuts", item_type="raw_material")

    unit = await client.post(
        f"/items/{item['id']}/units", json={"code": "box", "factor_to_base": "100"}, headers=headers
    )
    unit_id = unit.json()["id"]

    delete = await client.delete(f"/items/{item['id']}/units/{unit_id}", headers=headers)
    assert delete.status_code == 204


async def test_bom_line_unit_conversion_computes_base_quantity(client):
    token, _ = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    parent = await _create_item(client, token, sku="CONV-FG", name="Widget", item_type="finished_good")
    comp = await _create_item(
        client, token, sku="CONV-1", name="Steel", item_type="raw_material", unit_of_measure="kg"
    )
    unit = await client.post(
        f"/items/{comp['id']}/units", json={"code": "g", "factor_to_base": "0.001"}, headers=headers
    )
    unit_id = unit.json()["id"]

    bom = await client.post("/boms", json={"item_id": parent["id"]}, headers=headers)
    bom_data = bom.json()
    lines = await client.patch(
        f"/boms/{bom_data['id']}",
        json={
            "lines": [
                {
                    "component_item_id": comp["id"],
                    # Deliberately wrong client-submitted quantity - the
                    # server must ignore it and recompute from unit_quantity.
                    "quantity": "999",
                    "sequence": 0,
                    "unit_id": unit_id,
                    "unit_quantity": "500",
                }
            ],
            "expected_updated_at": bom_data["updated_at"],
        },
        headers=headers,
    )
    assert lines.status_code == 200
    line = lines.json()["lines"][0]
    assert line["quantity"] == "0.5000"
    assert line["unit_id"] == unit_id
    assert line["unit_quantity"] == "500.0000"
    assert line["unit_code"] == "g"


async def test_bom_line_unit_must_belong_to_its_component(client):
    token, _ = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    parent = await _create_item(client, token, sku="CONV-FG2", name="Gadget", item_type="finished_good")
    comp_a = await _create_item(client, token, sku="CONV-A", name="Steel", item_type="raw_material")
    comp_b = await _create_item(client, token, sku="CONV-B", name="Copper", item_type="raw_material")
    unit_for_a = await client.post(
        f"/items/{comp_a['id']}/units", json={"code": "g", "factor_to_base": "0.001"}, headers=headers
    )

    bom = await client.post("/boms", json={"item_id": parent["id"]}, headers=headers)
    bom_data = bom.json()
    res = await client.patch(
        f"/boms/{bom_data['id']}",
        json={
            "lines": [
                {
                    "component_item_id": comp_b["id"],
                    "quantity": "1",
                    "sequence": 0,
                    "unit_id": unit_for_a.json()["id"],
                    "unit_quantity": "500",
                }
            ],
            "expected_updated_at": bom_data["updated_at"],
        },
        headers=headers,
    )
    assert res.status_code == 400
