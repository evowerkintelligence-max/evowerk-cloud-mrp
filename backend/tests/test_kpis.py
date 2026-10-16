from __future__ import annotations
from tests._test_secrets import TEST_PASSWORD

import uuid

import pytest

from app.db import session as db_session
from app.models.dataset import Dataset
from app.models.kpi import Kpi
from app.services import kpis as kpi_service
from app.services.kpis import _compute_breakdown

pytestmark = pytest.mark.asyncio

PASSWORD = TEST_PASSWORD

CSV = b"scrap_units,total_units\n5,100\n"


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post("/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD})
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"]


async def _upload(client, token, *, content=CSV, filename="scrap.csv"):
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.post("/datasets", headers=headers, files={"file": (filename, content, "text/csv")})
    assert res.status_code == 201
    return res.json()["id"]


async def test_kpi_compute_caches_until_forced(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload(client, token)

    create = await client.post(
        "/kpis",
        headers=headers,
        json={
            "name": "Scrap Rate (%)",
            "dataset_id": dataset_id,
            "sql": "SELECT (SUM(TRY_CAST(scrap_units AS DOUBLE)) * 100.0) / NULLIF(SUM(TRY_CAST(total_units AS DOUBLE)), 0) FROM {table}",
            "unit": "%",
            "direction": "lower_is_better",
        },
    )
    assert create.status_code == 201, create.text
    kpi_id = create.json()["id"]

    computed = await client.post(
        "/kpis/compute", headers=headers, json={"dataset_id": dataset_id, "kpi_ids": [kpi_id]}
    )
    assert computed.status_code == 200, computed.text
    item = computed.json()[0]
    assert item["value"] == pytest.approx(5.0)
    assert item["cache_hit"] is False

    # Second compute without force reuses the cached value — no new history row.
    cached = await client.post(
        "/kpis/compute", headers=headers, json={"dataset_id": dataset_id, "kpi_ids": [kpi_id]}
    )
    cached_item = cached.json()[0]
    assert cached_item["cache_hit"] is True
    assert cached_item["value"] == pytest.approx(5.0)

    history_before = await client.get(f"/kpis/{kpi_id}/history", headers=headers, params={"dataset_id": dataset_id})
    assert len(history_before.json()) == 1

    # force=True recomputes and appends a new history point.
    forced = await client.post(
        "/kpis/compute", headers=headers, json={"dataset_id": dataset_id, "kpi_ids": [kpi_id], "force": True}
    )
    forced_item = forced.json()[0]
    assert forced_item["cache_hit"] is False

    history = await client.get(f"/kpis/{kpi_id}/history", headers=headers, params={"dataset_id": dataset_id})
    assert history.status_code == 200
    assert len(history.json()) == 2


async def test_kpi_static_breach_eval(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload(client, token)

    create = await client.post(
        "/kpis",
        headers=headers,
        json={
            "name": "Scrap Rate (%)",
            "dataset_id": dataset_id,
            "sql": "SELECT (SUM(TRY_CAST(scrap_units AS DOUBLE)) * 100.0) / NULLIF(SUM(TRY_CAST(total_units AS DOUBLE)), 0) FROM {table}",
            "eval_policy": {"static": {"warn_high": 3, "breach_high": 4}},
        },
    )
    kpi_id = create.json()["id"]

    computed = await client.post(
        "/kpis/compute",
        headers=headers,
        json={"dataset_id": dataset_id, "kpi_ids": [kpi_id], "include_eval": True},
    )
    item = computed.json()[0]
    assert item["value"] == pytest.approx(5.0)
    assert item["eval"]["status"] == "breach"
    assert "static_breach_high" in item["eval"]["reasons"]


async def test_kpi_sql_missing_table_placeholder_rejected(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload(client, token)

    create = await client.post(
        "/kpis",
        headers=headers,
        json={"name": "Bad KPI", "dataset_id": dataset_id, "sql": "SELECT 1"},
    )
    kpi_id = create.json()["id"]

    computed = await client.post(
        "/kpis/compute", headers=headers, json={"dataset_id": dataset_id, "kpi_ids": [kpi_id]}
    )
    item = computed.json()[0]
    assert item["value"] is None
    assert "table" in item["error"].lower()


CSV_BREAKDOWN = b"machine_id,scrap_units,total_units,reason\nM1,5,100,jam\nM1,3,100,jam\nM2,8,100,misfeed\nM2,2,100,jam\n"


async def test_kpi_breakdown_count_pareto(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload(client, token, content=CSV_BREAKDOWN, filename="scrap2.csv")

    create = await client.post(
        "/kpis",
        headers=headers,
        json={
            "name": "Scrap Rate (%)",
            "dataset_id": dataset_id,
            "sql": "SELECT (SUM(TRY_CAST(scrap_units AS DOUBLE)) * 100.0) / NULLIF(SUM(TRY_CAST(total_units AS DOUBLE)), 0) FROM {table}",
            "breakdown_group_col": "reason",
            "breakdown_agg": "count",
        },
    )
    assert create.status_code == 201, create.text
    kpi_id = create.json()["id"]

    computed = await client.post(
        "/kpis/compute", headers=headers, json={"dataset_id": dataset_id, "kpi_ids": [kpi_id]}
    )
    item = computed.json()[0]
    assert item["breakdown"] is not None
    labels = {b["label"]: b["value"] for b in item["breakdown"]}
    assert labels["jam"] == 3
    assert labels["misfeed"] == 1


async def test_kpi_breakdown_not_recomputed_on_cache_hit(client, monkeypatch):
    """Finding 1: the breakdown must be cached in KpiValue.meta alongside the
    main value, not re-derived by re-running _compute_breakdown (which
    re-materializes the whole dataset) on every cache-hit poll."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload(client, token, content=CSV_BREAKDOWN, filename="scrap6.csv")

    create = await client.post(
        "/kpis",
        headers=headers,
        json={
            "name": "Scrap Rate (%)",
            "dataset_id": dataset_id,
            "sql": "SELECT (SUM(TRY_CAST(scrap_units AS DOUBLE)) * 100.0) / NULLIF(SUM(TRY_CAST(total_units AS DOUBLE)), 0) FROM {table}",
            "breakdown_group_col": "reason",
            "breakdown_agg": "count",
        },
    )
    assert create.status_code == 201, create.text
    kpi_id = create.json()["id"]

    calls = {"n": 0}
    original = kpi_service._compute_breakdown

    async def _spy(dataset, kpi):
        calls["n"] += 1
        return await original(dataset, kpi)

    monkeypatch.setattr(kpi_service, "_compute_breakdown", _spy)

    first = await client.post(
        "/kpis/compute", headers=headers, json={"dataset_id": dataset_id, "kpi_ids": [kpi_id]}
    )
    first_item = first.json()[0]
    assert first_item["cache_hit"] is False
    assert calls["n"] == 1
    labels = {b["label"]: b["value"] for b in first_item["breakdown"]}
    assert labels["jam"] == 3

    # Cache-hit poll must read the breakdown back from meta, not call
    # _compute_breakdown again.
    second = await client.post(
        "/kpis/compute", headers=headers, json={"dataset_id": dataset_id, "kpi_ids": [kpi_id]}
    )
    second_item = second.json()[0]
    assert second_item["cache_hit"] is True
    assert calls["n"] == 1
    labels2 = {b["label"]: b["value"] for b in second_item["breakdown"]}
    assert labels2["jam"] == 3
    assert labels2["misfeed"] == 1


async def test_kpi_breakdown_avg_per_group(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload(client, token, content=CSV_BREAKDOWN, filename="scrap3.csv")

    create = await client.post(
        "/kpis",
        headers=headers,
        json={
            "name": "Scrap Units",
            "dataset_id": dataset_id,
            "sql": "SELECT AVG(TRY_CAST(scrap_units AS DOUBLE)) FROM {table}",
            "breakdown_group_col": "machine_id",
            "breakdown_agg": "avg",
            "breakdown_value_col": "scrap_units",
        },
    )
    assert create.status_code == 201, create.text
    kpi_id = create.json()["id"]

    computed = await client.post(
        "/kpis/compute", headers=headers, json={"dataset_id": dataset_id, "kpi_ids": [kpi_id]}
    )
    item = computed.json()[0]
    labels = {b["label"]: b["value"] for b in item["breakdown"]}
    assert labels["M1"] == pytest.approx(4.0)
    assert labels["M2"] == pytest.approx(5.0)


async def test_kpi_breakdown_invalid_agg_fails_soft(client):
    """_compute_breakdown docs promise it degrades to None on a bad breakdown
    config rather than raising. breakdown_agg outside count/avg/sum used to
    blow through the dict lookup in _breakdown_sql with a raw KeyError."""
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token, content=CSV_BREAKDOWN, filename="scrap4.csv")

    async with db_session.async_session_factory() as session:
        dataset = await session.get(Dataset, uuid.UUID(dataset_id))
        kpi = Kpi(
            org_id=dataset.org_id,
            dataset_id=dataset.id,
            name="Bad Agg",
            sql="SELECT 1 FROM {table}",
            created_by=uuid.uuid4(),
            breakdown_group_col="reason",
            breakdown_agg="median",
        )
        result = await _compute_breakdown(dataset, kpi)

    assert result is None


async def test_kpi_breakdown_avg_requires_value_col(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload(client, token, content=CSV_BREAKDOWN, filename="scrap4.csv")

    create = await client.post(
        "/kpis",
        headers=headers,
        json={
            "name": "Bad breakdown",
            "dataset_id": dataset_id,
            "sql": "SELECT AVG(TRY_CAST(scrap_units AS DOUBLE)) FROM {table}",
            "breakdown_group_col": "machine_id",
            "breakdown_agg": "avg",
        },
    )
    assert create.status_code == 400


async def test_kpi_breakdown_rejects_unknown_column(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload(client, token, content=CSV_BREAKDOWN, filename="scrap5.csv")

    create = await client.post(
        "/kpis",
        headers=headers,
        json={
            "name": "Bad column",
            "dataset_id": dataset_id,
            "sql": "SELECT AVG(TRY_CAST(scrap_units AS DOUBLE)) FROM {table}",
            "breakdown_group_col": "not_a_real_column",
            "breakdown_agg": "count",
        },
    )
    assert create.status_code == 400


async def test_rbac_blocks_non_owner_from_kpi_mutations(client, test_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.security import hash_password
    from app.models.user import User

    token = await _register_and_login(client, email="owner3@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    me = await client.get("/orgs/me", headers=headers)
    org_id = me.json()["id"]
    dataset_id = await _upload(client, token)

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        import uuid as uuid_module

        session.add(
            User(
                org_id=uuid_module.UUID(org_id),
                email="member3@example.com",
                hashed_password=hash_password(PASSWORD),
                role="member",
                is_active=True,
            )
        )
        await session.commit()

    login = await client.post("/auth/login", json={"email": "member3@example.com", "password": PASSWORD})
    member_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    res = await client.post(
        "/kpis",
        headers=member_headers,
        json={"name": "x", "dataset_id": dataset_id, "sql": "SELECT 1 FROM {table}"},
    )
    assert res.status_code == 403

    res = await client.post("/kpis/seed/manufacturing", headers=member_headers, json={"dataset_id": dataset_id})
    assert res.status_code == 403


async def test_delete_kpi_removes_it_from_dashboards(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload(client, token)

    create = await client.post(
        "/kpis",
        headers=headers,
        json={
            "name": "Scrap Rate (%)",
            "dataset_id": dataset_id,
            "sql": "SELECT (SUM(TRY_CAST(scrap_units AS DOUBLE)) * 100.0) / NULLIF(SUM(TRY_CAST(total_units AS DOUBLE)), 0) FROM {table}",
        },
    )
    kpi_id = create.json()["id"]

    other = await client.post(
        "/kpis",
        headers=headers,
        json={"name": "Other", "dataset_id": dataset_id, "sql": "SELECT 1.0 FROM {table}"},
    )
    other_id = other.json()["id"]

    dashboard = await client.post(
        "/kpi-dashboards",
        headers=headers,
        json={"name": "Floor", "tier": "operator", "kpi_ids": [kpi_id, other_id]},
    )
    dashboard_id = dashboard.json()["id"]

    deleted = await client.delete(f"/kpis/{kpi_id}", headers=headers)
    assert deleted.status_code == 204

    listed = await client.get("/kpi-dashboards", headers=headers)
    dash = next(d for d in listed.json() if d["id"] == dashboard_id)
    assert dash["kpi_ids"] == [other_id]


async def test_seed_manufacturing_skips_existing(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = await _upload(client, token)

    first = await client.post("/kpis/seed/manufacturing", headers=headers, json={"dataset_id": dataset_id})
    assert first.status_code == 200
    assert first.json()["created"] == 10
    assert first.json()["skipped"] == 0

    second = await client.post("/kpis/seed/manufacturing", headers=headers, json={"dataset_id": dataset_id})
    assert second.json()["created"] == 0
    assert second.json()["skipped"] == 10
