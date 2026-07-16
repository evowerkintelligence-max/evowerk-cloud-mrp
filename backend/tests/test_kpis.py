from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # satisfies policy: 12+ chars, letter + digit  # pragma: allowlist secret

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
