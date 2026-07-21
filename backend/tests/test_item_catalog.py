from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # satisfies policy: 12+ chars, letter + digit  # pragma: allowlist secret


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"]


async def _create_item(client, token, sku, name="Item", **extra):
    payload = {"sku": sku, "name": name, "item_type": "component", "unit_of_measure": "ea", **extra}
    res = await client.post("/items", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 201
    return res.json()


# --------------------------------------------------------------------------- #
# Editing name / unit_of_measure
# --------------------------------------------------------------------------- #
async def test_patch_edits_name_and_unit_of_measure(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token, "ROD", name="Old Name")

    res = await client.patch(
        f"/items/{item['id']}",
        json={"preferred_supplier_id": None, "name": "New Name", "unit_of_measure": "kg"},
        headers=headers,
    )
    assert res.status_code == 200
    assert res.json()["name"] == "New Name"
    assert res.json()["unit_of_measure"] == "kg"


async def test_patch_rejects_unit_of_measure_change_once_used_as_bom_component(client):
    """BomLine.quantity is a numeric magnitude fixed relative to the
    component's unit_of_measure at authoring time - changing the unit out
    from under an existing BOM line would silently reinterpret that stored
    number, so it must be rejected once the item is referenced.
    """
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    parent = await _create_item(client, token, "FG-UOM", item_type="finished_good")
    comp = await _create_item(client, token, "RAW-UOM", unit_of_measure="kg")

    bom = await client.post("/boms", json={"item_id": parent["id"]}, headers=headers)
    bom_data = bom.json()
    await client.patch(
        f"/boms/{bom_data['id']}",
        json={
            "lines": [{"component_item_id": comp["id"], "quantity": "1", "sequence": 0}],
            "expected_updated_at": bom_data["updated_at"],
        },
        headers=headers,
    )

    res = await client.patch(
        f"/items/{comp['id']}",
        json={"preferred_supplier_id": None, "unit_of_measure": "g"},
        headers=headers,
    )
    assert res.status_code == 409

    # Unrelated items (no BOM references, no custom units) can still change freely.
    other = await _create_item(client, token, "RAW-UOM-2", unit_of_measure="kg")
    res = await client.patch(
        f"/items/{other['id']}",
        json={"preferred_supplier_id": None, "unit_of_measure": "g"},
        headers=headers,
    )
    assert res.status_code == 200


async def test_patch_rejects_empty_name(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token, "ROD")

    res = await client.patch(
        f"/items/{item['id']}",
        json={"preferred_supplier_id": None, "name": ""},
        headers=headers,
    )
    assert res.status_code == 400


# --------------------------------------------------------------------------- #
# Role exposed on /orgs/me
# --------------------------------------------------------------------------- #
async def test_orgs_me_reports_owner_role(client):
    token = await _register_and_login(client)
    res = await client.get("/orgs/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert res.json()["role"] == "owner"


# --------------------------------------------------------------------------- #
# Description / barcode fields
# --------------------------------------------------------------------------- #
async def test_create_item_with_description_and_barcode(client):
    token = await _register_and_login(client)
    item = await _create_item(
        client, token, "ROD", description="A steel rod", barcode="012345678905"
    )
    assert item["description"] == "A steel rod"
    assert item["barcode"] == "012345678905"


async def test_barcode_unique_per_org(client):
    token = await _register_and_login(client)
    await _create_item(client, token, "ROD", barcode="DUPE")
    res = await client.post(
        "/items",
        json={"sku": "BOLT", "name": "Bolt", "barcode": "DUPE"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 409


async def test_multiple_items_without_barcode_allowed(client):
    # NULL barcode must not collide with itself under the unique constraint.
    token = await _register_and_login(client)
    await _create_item(client, token, "ROD")
    res = await _create_item(client, token, "BOLT")
    assert res["barcode"] is None


# --------------------------------------------------------------------------- #
# Audit log
# --------------------------------------------------------------------------- #
async def test_item_audit_log_records_create_and_update(client):
    token = await _register_and_login(client, email="owner@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token, "ROD")
    await client.patch(
        f"/items/{item['id']}",
        json={"preferred_supplier_id": None, "lead_time_days": 7},
        headers=headers,
    )

    res = await client.get(f"/items/{item['id']}/audit-log", headers=headers)
    assert res.status_code == 200
    entries = res.json()
    actions = [e["action"] for e in entries]
    assert actions == ["item.updated", "item.created"]  # newest first
    assert entries[0]["actor_email"] == "owner@example.com"


async def test_item_audit_log_scoped_to_org(client):
    token_a = await _register_and_login(client, email="orga@example.com", org_name="OrgA")
    token_b = await _register_and_login(client, email="orgb@example.com", org_name="OrgB")
    item = await _create_item(client, token_a, "ROD")

    res = await client.get(
        f"/items/{item['id']}/audit-log", headers={"Authorization": f"Bearer {token_b}"}
    )
    assert res.status_code == 404


# --------------------------------------------------------------------------- #
# Import from Dataset / export
# --------------------------------------------------------------------------- #
async def _upload_dataset(client, token, csv_text: str, filename="items.csv") -> str:
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.post(
        "/datasets",
        headers=headers,
        files={"file": (filename, csv_text.encode("utf-8"), "text/csv")},
    )
    assert res.status_code == 201
    return res.json()["id"]


async def test_import_from_dataset_creates_valid_rows(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload_dataset(
        client, token, "sku,name,lead_time_days\nROD,Steel Rod,14\nBOLT,Hex Bolt,\n"
    )
    res = await client.post("/items/import", json={"dataset_id": dataset_id}, headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert len(body["created"]) == 2
    assert body["errors"] == []

    items = (await client.get("/items", headers=headers)).json()
    assert {i["sku"] for i in items} == {"ROD", "BOLT"}
    assert next(i for i in items if i["sku"] == "ROD")["lead_time_days"] == 14


async def test_import_from_dataset_partial_success_does_not_block_good_rows(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    # Row 2 is bad (missing name); row 3 is good - the bad row must not roll
    # back or block the good one committed after it.
    dataset_id = await _upload_dataset(client, token, "sku,name\nBADROW,\nGOODROW,Good Item\n")
    res = await client.post("/items/import", json={"dataset_id": dataset_id}, headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert len(body["created"]) == 1
    assert body["created"][0]["sku"] == "GOODROW"
    assert len(body["errors"]) == 1
    assert body["errors"][0]["row"] == 2

    items = (await client.get("/items", headers=headers)).json()
    assert {i["sku"] for i in items} == {"GOODROW"}


async def test_import_from_dataset_requires_sku_and_name_columns(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload_dataset(client, token, "sku,description\nROD,A rod\n")
    res = await client.post("/items/import", json={"dataset_id": dataset_id}, headers=headers)
    assert res.status_code == 400


async def test_import_from_dataset_requires_owner(client, test_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.security import hash_password
    from app.models.user import User

    token = await _register_and_login(client, email="owner2@example.com")
    dataset_id = await _upload_dataset(client, token, "sku,name\nROD,Rod\n")
    org_id = (
        await client.get("/orgs/me", headers={"Authorization": f"Bearer {token}"})
    ).json()["id"]

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        import uuid as uuid_module

        session.add(
            User(
                org_id=uuid_module.UUID(org_id),
                email="member2@example.com",
                hashed_password=hash_password(PASSWORD),
                role="member",
                is_active=True,
            )
        )
        await session.commit()

    login = await client.post(
        "/auth/login", json={"email": "member2@example.com", "password": PASSWORD}
    )
    member_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    res = await client.post(
        "/items/import", json={"dataset_id": dataset_id}, headers=member_headers
    )
    assert res.status_code == 403


async def test_csv_export_round_trips_item_data(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    await _create_item(client, token, "ROD", name="Steel Rod", lead_time_days=14, barcode="ABC123")

    res = await client.get("/items/export", headers=headers)
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    text = res.text
    assert "ROD" in text
    assert "Steel Rod" in text
    assert "ABC123" in text
