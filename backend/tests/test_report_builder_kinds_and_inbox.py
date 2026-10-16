from __future__ import annotations

import uuid

import pytest

from tests._report_builder_helpers import (
    PASSWORD,
    _register_and_login,
)

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("kind", ["inventory-analytics", "rca", "scenario"])
async def test_creating_a_report_with_a_retired_inventory_analytics_kind_is_rejected(client, kind):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    res = await client.post(
        "/reports/custom", headers=headers,
        json={"name": "Old capture", "sections": [{"type": "kind_report", "report_kind": kind}]},
    )

    assert res.status_code == 400
    assert "retired" in res.json()["detail"].lower()


async def test_kind_report_section_rejects_unknown_kind(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    created = await client.post(
        "/reports/custom", headers=headers,
        json={"name": "Bad kind", "sections": [{"type": "kind_report", "report_kind": "not-a-kind"}]},
    )
    report_id = created.json()["id"]

    res = await client.get(f"/reports/custom/{report_id}/render?format=html", headers=headers)
    assert res.status_code == 400


async def test_creating_a_report_with_a_retired_kind_is_rejected(client):
    """Unlike the old-spec render tolerance below, a NEW spec has no excuse
    to reference a kind that's been retired - reject it at creation instead
    of silently accepting it and letting the section render empty forever."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    res = await client.post(
        "/reports/custom", headers=headers,
        json={"name": "Old plan", "sections": [{"type": "kind_report", "report_kind": "mrp-plan"}]},
    )

    assert res.status_code == 400
    assert "retired" in res.json()["detail"].lower()


async def test_updating_a_report_to_add_a_retired_kind_is_rejected(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    created = await client.post(
        "/reports/custom", headers=headers,
        json={"name": "Fine for now", "sections": []},
    )
    report_id = created.json()["id"]

    res = await client.put(
        f"/reports/custom/{report_id}", headers=headers,
        json={"name": "Fine for now", "sections": [{"type": "kind_report", "report_kind": "mrp-plan"}]},
    )

    assert res.status_code == 400
    assert "retired" in res.json()["detail"].lower()


async def test_inbox_item_with_a_retired_kind_is_rejected(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    res = await client.post(
        "/reports/inbox", headers=headers,
        json={"label": "Old plan", "type": "kind_report", "report_kind": "mrp-plan"},
    )

    assert res.status_code == 400
    assert "retired" in res.json()["detail"].lower()


async def test_kind_report_section_with_retired_kind_still_renders_other_sections(client, test_engine):
    """A report saved before the "mrp-plan" export was retired still carries
    it in its stored spec (KINDS no longer includes it). Rendering must skip
    that one section rather than failing the whole report."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.models.report_spec import SavedReport

    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    me = await client.get("/orgs/me", headers=headers)
    org_id = me.json()["id"]
    user_id = me.json()["user_id"]

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(
            SavedReport(
                org_id=uuid.UUID(org_id),
                name="Old export",
                sections=[
                    {"type": "text", "title": "Notes", "text": "Still here"},
                    {"type": "kind_report", "report_kind": "mrp-plan"},
                ],
                created_by=uuid.UUID(user_id),
            )
        )
        await session.commit()

    listed = await client.get("/reports/custom", headers=headers)
    report_id = listed.json()[0]["id"]

    res = await client.get(f"/reports/custom/{report_id}/render?format=html", headers=headers)
    assert res.status_code == 200, res.text
    assert b"Still here" in res.content


async def test_inbox_create_list_and_delete(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    created = await client.post(
        "/reports/inbox", headers=headers,
        json={"label": "OEE — July", "type": "text", "text": "captured content"},
    )
    assert created.status_code == 201
    item = created.json()
    assert item["label"] == "OEE — July"
    assert item["spec"]["type"] == "text"

    listed = await client.get("/reports/inbox", headers=headers)
    assert listed.status_code == 200
    assert any(i["id"] == item["id"] for i in listed.json())

    deleted = await client.delete(f"/reports/inbox/{item['id']}", headers=headers)
    assert deleted.status_code == 204
    assert not any(i["id"] == item["id"] for i in (await client.get("/reports/inbox", headers=headers)).json())


async def test_inbox_delete_allowed_for_creator_not_other_members(client, test_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.security import hash_password
    from app.models.user import User

    token = await _register_and_login(client, email="ibowner@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    me = await client.get("/orgs/me", headers=headers)
    org_id = me.json()["id"]

    created = await client.post(
        "/reports/inbox", headers=headers,
        json={"label": "Owner's capture", "type": "text", "text": "hi"},
    )
    item_id = created.json()["id"]

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        import uuid as uuid_module

        session.add(
            User(
                org_id=uuid_module.UUID(org_id),
                email="ibmember@example.com",
                hashed_password=hash_password(PASSWORD),
                role="member",
                is_active=True,
            )
        )
        await session.commit()

    login = await client.post("/auth/login", json={"email": "ibmember@example.com", "password": PASSWORD})
    member_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    # a different member can't delete someone else's inbox item...
    res = await client.delete(f"/reports/inbox/{item_id}", headers=member_headers)
    assert res.status_code == 403

    # ...but the creator can delete their own.
    res = await client.delete(f"/reports/inbox/{item_id}", headers=headers)
    assert res.status_code == 204


async def test_inbox_delete_allowed_for_owner_deleting_someone_elses_item(client, test_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.security import hash_password
    from app.models.user import User

    token = await _register_and_login(client, email="ib2owner@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    me = await client.get("/orgs/me", headers=headers)
    org_id = me.json()["id"]

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        import uuid as uuid_module

        session.add(
            User(
                org_id=uuid_module.UUID(org_id),
                email="ib2member@example.com",
                hashed_password=hash_password(PASSWORD),
                role="member",
                is_active=True,
            )
        )
        await session.commit()

    login = await client.post("/auth/login", json={"email": "ib2member@example.com", "password": PASSWORD})
    member_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    created = await client.post(
        "/reports/inbox", headers=member_headers,
        json={"label": "Member's capture", "type": "text", "text": "hi"},
    )
    item_id = created.json()["id"]

    # the org owner can delete a different member's inbox item.
    res = await client.delete(f"/reports/inbox/{item_id}", headers=headers)
    assert res.status_code == 204


async def test_inbox_cross_org_isolation(client):
    token_a = await _register_and_login(client, email="iba@example.com", org_name="InboxOrgA")
    headers_a = {"Authorization": f"Bearer {token_a}"}
    created = await client.post(
        "/reports/inbox", headers=headers_a,
        json={"label": "OrgA capture", "type": "text", "text": "secret"},
    )
    item_id = created.json()["id"]

    token_b = await _register_and_login(client, email="ibb@example.com", org_name="InboxOrgB")
    headers_b = {"Authorization": f"Bearer {token_b}"}

    listed_b = await client.get("/reports/inbox", headers=headers_b)
    assert not any(i["id"] == item_id for i in listed_b.json())
    assert (await client.delete(f"/reports/inbox/{item_id}", headers=headers_b)).status_code == 404
