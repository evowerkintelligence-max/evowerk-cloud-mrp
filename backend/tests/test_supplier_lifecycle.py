from __future__ import annotations

from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"]


async def _create_supplier(client, token, name="Acme Metals"):
    res = await client.post(
        "/suppliers", json={"name": name}, headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 201
    return res.json()


async def test_update_supplier_edits_fields(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    supplier = await _create_supplier(client, token)

    res = await client.patch(
        f"/suppliers/{supplier['id']}",
        json={"name": "Acme Metals Co", "email": "buy@acme.test", "phone": "555-0100"},
        headers=headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["name"] == "Acme Metals Co"
    assert body["email"] == "buy@acme.test"
    assert body["phone"] == "555-0100"


async def test_update_supplier_rejects_duplicate_name(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    await _create_supplier(client, token, name="Alpha")
    beta = await _create_supplier(client, token, name="Beta")

    res = await client.patch(f"/suppliers/{beta['id']}", json={"name": "Alpha"}, headers=headers)
    assert res.status_code == 409


async def test_deactivate_and_reactivate_supplier(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    supplier = await _create_supplier(client, token)
    assert supplier["is_active"] is True

    off = await client.post(f"/suppliers/{supplier['id']}/deactivate", headers=headers)
    assert off.status_code == 200
    assert off.json()["is_active"] is False

    # active_only excludes it, unfiltered list still shows it.
    active_list = await client.get("/suppliers?active_only=true", headers=headers)
    assert supplier["id"] not in {s["id"] for s in active_list.json()}
    full_list = await client.get("/suppliers", headers=headers)
    assert supplier["id"] in {s["id"] for s in full_list.json()}

    on = await client.post(f"/suppliers/{supplier['id']}/reactivate", headers=headers)
    assert on.status_code == 200
    assert on.json()["is_active"] is True


async def test_list_suppliers_search_and_pagination(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    for name in ["Alpha Supply", "Beta Supply", "Gamma Fasteners"]:
        await _create_supplier(client, token, name=name)

    search = await client.get("/suppliers?q=supply", headers=headers)
    assert {s["name"] for s in search.json()} == {"Alpha Supply", "Beta Supply"}

    page1 = await client.get("/suppliers?limit=2&offset=0", headers=headers)
    page2 = await client.get("/suppliers?limit=2&offset=2", headers=headers)
    assert len(page1.json()) == 2
    assert len(page2.json()) == 1
    assert {s["id"] for s in page1.json()} & {s["id"] for s in page2.json()} == set()


async def test_deactivate_supplier_requires_owner(client, test_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.security import hash_password
    from app.models.user import User

    token = await _register_and_login(client, email="owner2@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    supplier = await _create_supplier(client, token)
    org_id = (await client.get("/orgs/me", headers=headers)).json()["id"]

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        import uuid as uuid_module

        session.add(
            User(
                org_id=uuid_module.UUID(org_id),
                email="member2@example.com",
                password_hash=hash_password(PASSWORD),
                role="member",
                is_active=True,
            )
        )
        await session.commit()

    login = await client.post("/auth/login", json={"email": "member2@example.com", "password": PASSWORD})
    member_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    res = await client.post(f"/suppliers/{supplier['id']}/deactivate", headers=member_headers)
    assert res.status_code == 403


async def test_supplier_contacts_crud_and_primary_swap(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    supplier = await _create_supplier(client, token)

    c1 = await client.post(
        f"/suppliers/{supplier['id']}/contacts",
        json={"name": "Jane Doe", "role": "Sales Rep", "email": "jane@vendor.test", "is_primary": True},
        headers=headers,
    )
    assert c1.status_code == 201
    c1_id = c1.json()["id"]
    assert c1.json()["is_primary"] is True

    c2 = await client.post(
        f"/suppliers/{supplier['id']}/contacts",
        json={"name": "Bob Smith", "role": "AP", "is_primary": True},
        headers=headers,
    )
    assert c2.status_code == 201
    c2_id = c2.json()["id"]
    assert c2.json()["is_primary"] is True

    listing = await client.get(f"/suppliers/{supplier['id']}/contacts", headers=headers)
    by_id = {c["id"]: c for c in listing.json()}
    # Marking c2 primary must have unset c1's primary flag.
    assert by_id[c1_id]["is_primary"] is False
    assert by_id[c2_id]["is_primary"] is True

    updated = await client.patch(
        f"/suppliers/{supplier['id']}/contacts/{c1_id}",
        json={"name": "Jane D.", "role": "Sales Rep", "is_primary": False},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Jane D."

    deleted = await client.delete(f"/suppliers/{supplier['id']}/contacts/{c2_id}", headers=headers)
    assert deleted.status_code == 204

    listing2 = await client.get(f"/suppliers/{supplier['id']}/contacts", headers=headers)
    assert [c["id"] for c in listing2.json()] == [c1_id]


async def test_supplier_contact_mutations_require_owner(client, test_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.security import hash_password
    from app.models.user import User

    token = await _register_and_login(client, email="owner3@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    supplier = await _create_supplier(client, token)
    org_id = (await client.get("/orgs/me", headers=headers)).json()["id"]

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        import uuid as uuid_module

        session.add(
            User(
                org_id=uuid_module.UUID(org_id),
                email="member3@example.com",
                password_hash=hash_password(PASSWORD),
                role="member",
                is_active=True,
            )
        )
        await session.commit()

    login = await client.post("/auth/login", json={"email": "member3@example.com", "password": PASSWORD})
    member_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    res = await client.post(
        f"/suppliers/{supplier['id']}/contacts", json={"name": "X"}, headers=member_headers
    )
    assert res.status_code == 403


async def test_supplier_create_and_update_extended_fields(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    created = await client.post(
        "/suppliers",
        json={
            "name": "Acme Metals",
            "address_line1": "1 Foundry Rd",
            "city": "Pittsburgh",
            "state": "PA",
            "postal_code": "15201",
            "country": "USA",
            "payment_terms_days": 30,
            "lead_time_days": 14,
            "tags": ["metals", "domestic"],
        },
        headers=headers,
    )
    assert created.status_code == 201
    body = created.json()
    assert body["city"] == "Pittsburgh"
    assert body["payment_terms_days"] == 30
    assert body["lead_time_days"] == 14
    assert body["tags"] == ["metals", "domestic"]

    updated = await client.patch(
        f"/suppliers/{body['id']}",
        json={"name": "Acme Metals", "lead_time_days": 21, "tags": ["metals"]},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["lead_time_days"] == 21
    assert updated.json()["tags"] == ["metals"]


async def test_list_suppliers_filters_by_tag(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    await client.post("/suppliers", json={"name": "Alpha", "tags": ["metals"]}, headers=headers)
    await client.post("/suppliers", json={"name": "Beta", "tags": ["packaging"]}, headers=headers)
    await client.post(
        "/suppliers", json={"name": "Gamma", "tags": ["metals", "domestic"]}, headers=headers
    )

    res = await client.get("/suppliers?tag=metals", headers=headers)
    assert res.status_code == 200
    assert {s["name"] for s in res.json()} == {"Alpha", "Gamma"}


async def test_supplier_scorecard_no_data_yet(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    supplier = await _create_supplier(client, token)

    res = await client.get(f"/suppliers/{supplier['id']}/scorecard", headers=headers)
    assert res.status_code == 200
    assert res.json() == {"on_time_pct": None, "defect_pct": None, "sample_size": 0}


async def test_supplier_scorecard_on_time_and_defect_math(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    supplier = await _create_supplier(client, token)
    item = (
        await client.post(
            "/items",
            json={"sku": "ROD", "name": "Rod", "item_type": "raw_material", "unit_of_measure": "ea"},
            headers=headers,
        )
    ).json()

    async def _po_receive(expected_delivery_date, quantity, quantity_rejected):
        body = {"supplier_id": supplier["id"]}
        if expected_delivery_date is not None:
            body["expected_delivery_date"] = expected_delivery_date
        po = (await client.post("/purchase-orders", json=body, headers=headers)).json()
        lines = await client.patch(
            f"/purchase-orders/{po['id']}",
            json=[
                {"item_id": item["id"], "quantity_ordered": quantity, "unit_price": "1", "sequence": 0}
            ],
            headers=headers,
        )
        line_id = lines.json()["lines"][0]["id"]
        await client.post(f"/purchase-orders/{po['id']}/submit", headers=headers)
        await client.post(
            f"/purchase-orders/{po['id']}/receive",
            json=[{"po_line_id": line_id, "quantity": quantity, "quantity_rejected": quantity_rejected}],
            headers=headers,
        )

    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    await _po_receive(tomorrow, "10", "2")  # on time, 2 rejected
    await _po_receive(yesterday, "5", "0")  # late, none rejected
    await _po_receive(None, "5", "0")  # no due date, excluded from on_time_pct

    res = await client.get(f"/suppliers/{supplier['id']}/scorecard", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert body["sample_size"] == 2
    assert body["on_time_pct"] == pytest.approx(0.5)
    assert body["defect_pct"] == pytest.approx(0.1)
