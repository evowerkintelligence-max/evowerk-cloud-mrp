from __future__ import annotations
from tests._test_secrets import TEST_PASSWORD

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = TEST_PASSWORD

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


async def test_group_by_query_succeeds(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)
    headers = {"Authorization": f"Bearer {token}"}

    # Columns are stored ALL_VARCHAR (see datasets.relation_for) - raw SQL sees
    # them as text, same as the desktop app's SQL box would; unlike the
    # structured query builder, there's no automatic numeric coercion here.
    res = await client.post(
        f"/datasets/{dataset_id}/sql",
        headers=headers,
        json={"sql": "SELECT sku, SUM(CAST(qty AS DOUBLE)) AS total_qty FROM dataset GROUP BY sku ORDER BY sku"},
    )
    assert res.status_code == 200
    rows = {r["sku"]: r["total_qty"] for r in res.json()["rows"]}
    assert rows == {"A-1": 13.0, "A-2": 5.0}


async def test_trailing_semicolon_and_whitespace_tolerated(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)
    headers = {"Authorization": f"Bearer {token}"}

    res = await client.post(
        f"/datasets/{dataset_id}/sql",
        headers=headers,
        json={"sql": "  SELECT * FROM dataset ;  "},
    )
    assert res.status_code == 200
    assert len(res.json()["rows"]) == 3


async def test_multi_statement_rejected(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)
    headers = {"Authorization": f"Bearer {token}"}

    res = await client.post(
        f"/datasets/{dataset_id}/sql",
        headers=headers,
        json={"sql": "SELECT 1; SELECT 2"},
    )
    assert res.status_code == 400


@pytest.mark.parametrize(
    "sql",
    [
        "ATTACH ':memory:' AS x",
        "DROP TABLE dataset",
        "PRAGMA memory_limit='1GB'",
        "CREATE TABLE evil AS SELECT 1",
    ],
)
async def test_non_select_statement_rejected(client, sql):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)
    headers = {"Authorization": f"Bearer {token}"}

    res = await client.post(f"/datasets/{dataset_id}/sql", headers=headers, json={"sql": sql})
    assert res.status_code == 400


async def test_filesystem_access_inside_select_rejected(client):
    """Proves the enable_external_access lockdown, not just the statement-type
    check: this is a plain SELECT, so only the lockdown layer stops it."""
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)
    headers = {"Authorization": f"Bearer {token}"}

    res = await client.post(
        f"/datasets/{dataset_id}/sql",
        headers=headers,
        json={"sql": "SELECT * FROM read_csv_auto('C:/Windows/win.ini')"},
    )
    assert res.status_code == 400


async def test_row_limit_enforced_without_explicit_limit(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)
    headers = {"Authorization": f"Bearer {token}"}

    res = await client.post(
        f"/datasets/{dataset_id}/sql",
        headers=headers,
        json={"sql": "SELECT * FROM dataset", "limit": 2},
    )
    assert res.status_code == 200
    assert len(res.json()["rows"]) == 2


async def test_slow_query_times_out(client, monkeypatch):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)
    headers = {"Authorization": f"Bearer {token}"}

    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "sql_query_timeout_seconds", 0.1)

    res = await client.post(
        f"/datasets/{dataset_id}/sql",
        headers=headers,
        # A big self-cross-join that takes tens of seconds uninterrupted -
        # proves con.interrupt() fires well before that, not that it happens
        # to be a fast query.
        json={"sql": "SELECT COUNT(*) FROM range(500000) a, range(500000) b"},
    )
    assert res.status_code == 408
