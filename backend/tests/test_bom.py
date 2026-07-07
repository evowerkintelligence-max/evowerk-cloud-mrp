from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.security import hash_password
from app.models.user import User

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # satisfies policy: 12+ chars, letter + digit


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    token = login.json()["access_token"]
    me = await client.get("/orgs/me", headers={"Authorization": f"Bearer {token}"})
    return token, me.json()["id"]


async def _create_item(client, token, sku="RAW-1", name="Steel Rod", item_type="raw_material"):
    res = await client.post(
        "/items",
        json={"sku": sku, "name": name, "item_type": item_type, "unit_of_measure": "ea"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 201
    return res.json()


async def test_item_create_and_duplicate_sku_rejected(client):
    token, _ = await _register_and_login(client)
    await _create_item(client, token, sku="DUPE-1")

    res = await client.post(
        "/items",
        json={"sku": "DUPE-1", "name": "Other", "item_type": "component", "unit_of_measure": "ea"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 409


async def test_bom_create_edit_lines_and_activate(client):
    token, _ = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    parent = await _create_item(client, token, sku="FG-1", name="Widget", item_type="finished_good")
    comp = await _create_item(client, token, sku="RAW-2", name="Bolt", item_type="raw_material")

    bom = await client.post("/boms", json={"item_id": parent["id"]}, headers=headers)
    assert bom.status_code == 201
    bom_data = bom.json()
    assert bom_data["status"] == "draft"
    assert bom_data["version"] == 1

    lines = await client.patch(
        f"/boms/{bom_data['id']}",
        json=[{"component_item_id": comp["id"], "quantity": "4.5", "sequence": 0}],
        headers=headers,
    )
    assert lines.status_code == 200
    assert len(lines.json()["lines"]) == 1
    assert lines.json()["lines"][0]["component_sku"] == "RAW-2"

    activate = await client.post(f"/boms/{bom_data['id']}/activate", headers=headers)
    assert activate.status_code == 200
    assert activate.json()["status"] == "active"

    # Draft-only edit guard: an active BOM's lines can no longer be edited.
    edit_after_activate = await client.patch(
        f"/boms/{bom_data['id']}",
        json=[{"component_item_id": comp["id"], "quantity": "1", "sequence": 0}],
        headers=headers,
    )
    assert edit_after_activate.status_code == 409


async def test_activate_supersedes_previous_active_version(client):
    token, _ = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    parent = await _create_item(client, token, sku="FG-2", name="Gadget", item_type="finished_good")
    comp = await _create_item(client, token, sku="RAW-3", name="Screw", item_type="raw_material")

    async def _make_active_bom():
        bom = await client.post("/boms", json={"item_id": parent["id"]}, headers=headers)
        bom_id = bom.json()["id"]
        await client.patch(
            f"/boms/{bom_id}",
            json=[{"component_item_id": comp["id"], "quantity": "1", "sequence": 0}],
            headers=headers,
        )
        activated = await client.post(f"/boms/{bom_id}/activate", headers=headers)
        return activated.json()

    v1 = await _make_active_bom()
    assert v1["version"] == 1
    v2 = await _make_active_bom()
    assert v2["version"] == 2

    boms = await client.get("/boms", headers=headers)
    by_version = {b["version"]: b["status"] for b in boms.json() if b["item_id"] == parent["id"]}
    assert by_version == {1: "archived", 2: "active"}


async def test_activate_rejects_immediate_self_reference(client):
    token, _ = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    item = await _create_item(client, token, sku="FG-3", name="Loopy", item_type="finished_good")
    bom = await client.post("/boms", json={"item_id": item["id"]}, headers=headers)
    bom_id = bom.json()["id"]

    res = await client.patch(
        f"/boms/{bom_id}",
        json=[{"component_item_id": item["id"], "quantity": "1", "sequence": 0}],
        headers=headers,
    )
    assert res.status_code == 409


async def test_activate_rejects_multilevel_cycle(client):
    """A -> B (active), then trying to activate a B -> A BOM must be rejected -
    otherwise A indirectly requires itself to be built."""
    token, _ = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    item_a = await _create_item(client, token, sku="A", name="Item A", item_type="finished_good")
    item_b = await _create_item(client, token, sku="B", name="Item B", item_type="component")

    bom_a = await client.post("/boms", json={"item_id": item_a["id"]}, headers=headers)
    bom_a_id = bom_a.json()["id"]
    await client.patch(
        f"/boms/{bom_a_id}",
        json=[{"component_item_id": item_b["id"], "quantity": "1", "sequence": 0}],
        headers=headers,
    )
    activate_a = await client.post(f"/boms/{bom_a_id}/activate", headers=headers)
    assert activate_a.status_code == 200

    bom_b = await client.post("/boms", json={"item_id": item_b["id"]}, headers=headers)
    bom_b_id = bom_b.json()["id"]
    await client.patch(
        f"/boms/{bom_b_id}",
        json=[{"component_item_id": item_a["id"], "quantity": "1", "sequence": 0}],
        headers=headers,
    )
    activate_b = await client.post(f"/boms/{bom_b_id}/activate", headers=headers)
    assert activate_b.status_code == 409


async def test_bom_cross_tenant_isolation(client):
    token_a, _ = await _register_and_login(client, email="orga@example.com", org_name="OrgA")
    token_b, _ = await _register_and_login(client, email="orgb@example.com", org_name="OrgB")

    item_a = await _create_item(client, token_a, sku="ORGA-1", name="Only OrgA's")
    bom_a = await client.post(
        "/boms", json={"item_id": item_a["id"]}, headers={"Authorization": f"Bearer {token_a}"}
    )
    bom_a_id = bom_a.json()["id"]

    # Org B can't see org A's item or BOM at all.
    items_b = await client.get("/items", headers={"Authorization": f"Bearer {token_b}"})
    assert all(item["id"] != item_a["id"] for item in items_b.json())

    get_bom_as_b = await client.get(
        f"/boms/{bom_a_id}", headers={"Authorization": f"Bearer {token_b}"}
    )
    assert get_bom_as_b.status_code == 404


async def test_rbac_blocks_non_owner_from_mutations(client, test_engine):
    token, org_id = await _register_and_login(client, email="owner2@example.com")

    # The register endpoint always creates "owner" users (see auth.py) - there's
    # no invite flow yet to produce a non-owner user via the API, so this test
    # constructs one directly against the test DB to prove require_role("owner")
    # actually blocks a "member" role rather than testing dead code.
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        member = User(
            org_id=uuid.UUID(org_id),
            email="member@example.com",
            password_hash=hash_password(PASSWORD),
            role="member",
            is_active=True,
        )
        session.add(member)
        await session.commit()

    login = await client.post("/auth/login", json={"email": "member@example.com", "password": PASSWORD})
    assert login.status_code == 200
    member_token = login.json()["access_token"]

    res = await client.post(
        "/items",
        json={"sku": "BLOCKED-1", "name": "Should not be created", "item_type": "component", "unit_of_measure": "ea"},
        headers={"Authorization": f"Bearer {member_token}"},
    )
    assert res.status_code == 403

    # Reads are still open to non-owners.
    read = await client.get("/items", headers={"Authorization": f"Bearer {member_token}"})
    assert read.status_code == 200
