from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.security import hash_password
from app.models.user import User
from tests._bom_helpers import (
    PASSWORD,
    _register_and_login,
    _create_item,
)

pytestmark = pytest.mark.asyncio


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
        json={
            "lines": [{"component_item_id": comp["id"], "quantity": "4.5", "sequence": 0}],
            "expected_updated_at": bom_data["updated_at"],
        },
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
        json={
            "lines": [{"component_item_id": comp["id"], "quantity": "1", "sequence": 0}],
            "expected_updated_at": lines.json()["updated_at"],
        },
        headers=headers,
    )
    assert edit_after_activate.status_code == 409


async def test_bom_line_scrap_pct_saved_and_validated(client):
    token, _ = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    parent = await _create_item(client, token, sku="FG-SCRAP", name="Widget", item_type="finished_good")
    comp = await _create_item(client, token, sku="RAW-SCRAP", name="Bolt", item_type="raw_material")

    bom = await client.post("/boms", json={"item_id": parent["id"]}, headers=headers)
    bom_data = bom.json()

    lines = await client.patch(
        f"/boms/{bom_data['id']}",
        json={
            "lines": [{"component_item_id": comp["id"], "quantity": "1", "sequence": 0, "scrap_pct": "12.5"}],
            "expected_updated_at": bom_data["updated_at"],
        },
        headers=headers,
    )
    assert lines.status_code == 200
    assert lines.json()["lines"][0]["scrap_pct"] == "12.50"

    rejected = await client.patch(
        f"/boms/{bom_data['id']}",
        json={
            "lines": [{"component_item_id": comp["id"], "quantity": "1", "sequence": 0, "scrap_pct": "101"}],
            "expected_updated_at": lines.json()["updated_at"],
        },
        headers=headers,
    )
    assert rejected.status_code == 400


async def test_bom_patch_rejects_stale_updated_at(client):
    """Two editors load the same draft; the second one's PATCH must be
    rejected as stale instead of silently clobbering the first one's lines."""
    token, _ = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    parent = await _create_item(client, token, sku="FG-1B", name="Widget B", item_type="finished_good")
    comp = await _create_item(client, token, sku="RAW-2B", name="Bolt B", item_type="raw_material")

    bom = await client.post("/boms", json={"item_id": parent["id"]}, headers=headers)
    bom_data = bom.json()

    first_edit = await client.patch(
        f"/boms/{bom_data['id']}",
        json={
            "lines": [{"component_item_id": comp["id"], "quantity": "1", "sequence": 0}],
            "expected_updated_at": bom_data["updated_at"],
        },
        headers=headers,
    )
    assert first_edit.status_code == 200

    stale_edit = await client.patch(
        f"/boms/{bom_data['id']}",
        json={
            "lines": [{"component_item_id": comp["id"], "quantity": "2", "sequence": 0}],
            "expected_updated_at": bom_data["updated_at"],
        },
        headers=headers,
    )
    assert stale_edit.status_code == 409


async def test_delete_draft_bom(client):
    token, _ = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    item = await _create_item(client, token, sku="FG-DEL", name="Deletable", item_type="finished_good")
    bom = await client.post("/boms", json={"item_id": item["id"]}, headers=headers)
    bom_id = bom.json()["id"]

    delete = await client.delete(f"/boms/{bom_id}", headers=headers)
    assert delete.status_code == 204

    get_after_delete = await client.get(f"/boms/{bom_id}", headers=headers)
    assert get_after_delete.status_code == 404


async def test_delete_active_bom_rejected(client):
    token, _ = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    parent = await _create_item(client, token, sku="FG-DEL2", name="Not Deletable", item_type="finished_good")
    comp = await _create_item(client, token, sku="RAW-DEL2", name="Comp", item_type="raw_material")

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
    await client.post(f"/boms/{bom_data['id']}/activate", headers=headers)

    delete = await client.delete(f"/boms/{bom_data['id']}", headers=headers)
    assert delete.status_code == 409


async def test_activate_supersedes_previous_active_version(client):
    token, _ = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    parent = await _create_item(client, token, sku="FG-2", name="Gadget", item_type="finished_good")
    comp = await _create_item(client, token, sku="RAW-3", name="Screw", item_type="raw_material")

    async def _make_active_bom():
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
        activated = await client.post(f"/boms/{bom_data['id']}/activate", headers=headers)
        return activated.json()

    v1 = await _make_active_bom()
    assert v1["version"] == 1
    v2 = await _make_active_bom()
    assert v2["version"] == 2

    boms = await client.get("/boms", headers=headers)
    by_version = {b["version"]: b["status"] for b in boms.json() if b["item_id"] == parent["id"]}
    assert by_version == {1: "archived", 2: "active"}


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
            hashed_password=hash_password(PASSWORD),
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


async def test_revise_active_bom_copies_lines_into_new_draft(client):
    token, _ = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    parent = await _create_item(client, token, sku="REV-FG", name="Widget", item_type="finished_good")
    comp = await _create_item(client, token, sku="REV-1", name="Bolt", item_type="raw_material")

    bom = await client.post("/boms", json={"item_id": parent["id"]}, headers=headers)
    bom_data = bom.json()
    await client.patch(
        f"/boms/{bom_data['id']}",
        json={
            "lines": [
                {"component_item_id": comp["id"], "quantity": "4.5", "sequence": 0, "scrap_pct": "5"}
            ],
            "expected_updated_at": bom_data["updated_at"],
        },
        headers=headers,
    )
    activate = await client.post(f"/boms/{bom_data['id']}/activate", headers=headers)
    assert activate.status_code == 200

    revise = await client.post(f"/boms/{bom_data['id']}/revise", headers=headers)
    assert revise.status_code == 201
    revised = revise.json()
    assert revised["status"] == "draft"
    assert revised["version"] == 2
    assert len(revised["lines"]) == 1
    assert revised["lines"][0]["component_sku"] == "REV-1"
    assert revised["lines"][0]["quantity"] == "4.5000"
    assert revised["lines"][0]["scrap_pct"] == "5.00"

    # Original active version is untouched.
    original = await client.get(f"/boms/{bom_data['id']}", headers=headers)
    assert original.json()["status"] == "active"
    assert len(original.json()["lines"]) == 1


async def test_revise_rejects_non_active_bom(client):
    token, _ = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token, sku="REV-2", name="Draft Only", item_type="finished_good")
    bom = await client.post("/boms", json={"item_id": item["id"]}, headers=headers)

    revise = await client.post(f"/boms/{bom.json()['id']}/revise", headers=headers)
    assert revise.status_code == 409
