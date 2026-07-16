from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # satisfies policy: 12+ chars, letter + digit  # pragma: allowlist secret

CSV_A = b"units_produced,operating_hours\n100,10\n"
CSV_B = b"good_units,total_units\n90,100\n"


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post("/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD})
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"]


async def _upload(client, token, *, content, filename):
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.post("/datasets", headers=headers, files={"file": (filename, content, "text/csv")})
    assert res.status_code == 201
    return res.json()["id"]


async def test_dashboard_cards_spans_multiple_datasets(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_a = await _upload(client, token, content=CSV_A, filename="a.csv")
    dataset_b = await _upload(client, token, content=CSV_B, filename="b.csv")

    kpi_a = (
        await client.post(
            "/kpis",
            headers=headers,
            json={
                "name": "Throughput",
                "dataset_id": dataset_a,
                "sql": "SELECT SUM(TRY_CAST(units_produced AS DOUBLE)) / NULLIF(SUM(TRY_CAST(operating_hours AS DOUBLE)), 0) FROM {table}",
            },
        )
    ).json()["id"]
    kpi_b = (
        await client.post(
            "/kpis",
            headers=headers,
            json={
                "name": "First Pass Yield",
                "dataset_id": dataset_b,
                "sql": "SELECT (SUM(TRY_CAST(good_units AS DOUBLE)) * 100.0) / NULLIF(SUM(TRY_CAST(total_units AS DOUBLE)), 0) FROM {table}",
            },
        )
    ).json()["id"]

    dashboard = await client.post(
        "/kpi-dashboards",
        headers=headers,
        json={"name": "Plant overview", "tier": "supervisor", "kpi_ids": [kpi_a, kpi_b]},
    )
    assert dashboard.status_code == 201, dashboard.text
    dashboard_id = dashboard.json()["id"]
    assert dashboard.json()["refresh_seconds"] == 300

    cards = await client.get(f"/kpi-dashboards/{dashboard_id}/cards", headers=headers)
    assert cards.status_code == 200, cards.text
    by_name = {c["name"]: c for c in cards.json()}
    assert by_name["Throughput"]["value"] == pytest.approx(10.0)
    assert by_name["First Pass Yield"]["value"] == pytest.approx(90.0)
    assert by_name["Throughput"]["dataset_id"] == dataset_a
    assert by_name["First Pass Yield"]["dataset_id"] == dataset_b


async def test_dashboard_crud(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload(client, token, content=CSV_A, filename="c.csv")
    kpi_id = (
        await client.post(
            "/kpis",
            headers=headers,
            json={"name": "K", "dataset_id": dataset_id, "sql": "SELECT 1.0 FROM {table}"},
        )
    ).json()["id"]

    created = await client.post(
        "/kpi-dashboards", headers=headers, json={"name": "Floor", "tier": "operator", "kpi_ids": [kpi_id]}
    )
    assert created.status_code == 201
    dashboard_id = created.json()["id"]
    assert created.json()["refresh_seconds"] == 15

    listed = await client.get("/kpi-dashboards", headers=headers)
    assert any(d["id"] == dashboard_id for d in listed.json())

    patched = await client.patch(
        f"/kpi-dashboards/{dashboard_id}", headers=headers, json={"name": "Floor renamed"}
    )
    assert patched.status_code == 200
    assert patched.json()["name"] == "Floor renamed"

    deleted = await client.delete(f"/kpi-dashboards/{dashboard_id}", headers=headers)
    assert deleted.status_code == 204
    assert (await client.get("/kpi-dashboards", headers=headers)).json() == []


async def test_rbac_blocks_non_owner_from_dashboard_mutations(client, test_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.security import hash_password
    from app.models.user import User

    token = await _register_and_login(client, email="owner4@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    me = await client.get("/orgs/me", headers=headers)
    org_id = me.json()["id"]

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        import uuid as uuid_module

        session.add(
            User(
                org_id=uuid_module.UUID(org_id),
                email="member4@example.com",
                password_hash=hash_password(PASSWORD),
                role="member",
                is_active=True,
            )
        )
        await session.commit()

    login = await client.post("/auth/login", json={"email": "member4@example.com", "password": PASSWORD})
    member_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    res = await client.post(
        "/kpi-dashboards", headers=member_headers, json={"name": "x", "tier": "operator", "kpi_ids": []}
    )
    assert res.status_code == 403
