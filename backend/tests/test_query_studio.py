from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret

CSV = b"sku,qty,region\nA-1,10,east\nA-2,5,west\nA-1,3,west\n"


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post("/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD})
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"]


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def _upload(client, token, content=CSV, filename="stock.csv"):
    res = await client.post("/datasets", headers=_h(token), files={"file": (filename, content, "text/csv")})
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _base_query():
    return {"select": [{"column": "qty", "agg": "sum", "alias": "total_qty"}], "group_by": ["region"]}


async def test_explain_returns_sql_without_executing(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)

    res = await client.post(f"/datasets/{dataset_id}/query/explain", headers=_h(token), json=_base_query())
    assert res.status_code == 200, res.text
    sql = res.json()["sql"]
    assert "SELECT" in sql
    assert "GROUP BY" in sql
    assert "total_qty" in sql

    # Explaining never logs to history or touches data - only /query and /sql do.
    history = await client.get("/datasets/query-history", headers=_h(token))
    assert history.json() == []


async def test_structured_query_is_logged_to_history(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)

    run = await client.post(f"/datasets/{dataset_id}/query", headers=_h(token), json=_base_query())
    assert run.status_code == 200, run.text

    history = await client.get("/datasets/query-history", headers=_h(token))
    assert history.status_code == 200, history.text
    entries = history.json()
    assert len(entries) == 1
    assert entries[0]["action"] == "dataset.query"
    assert entries[0]["dataset_id"] == dataset_id
    assert entries[0]["query"]["group_by"] == ["region"]


async def test_sql_query_is_logged_to_history(client):
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)

    run = await client.post(
        f"/datasets/{dataset_id}/sql", headers=_h(token), json={"sql": "SELECT * FROM dataset LIMIT 5"}
    )
    assert run.status_code == 200, run.text

    history = await client.get("/datasets/query-history", headers=_h(token))
    entries = history.json()
    assert len(entries) == 1
    assert entries[0]["action"] == "dataset.sql_query"
    assert entries[0]["sql"] == "SELECT * FROM dataset LIMIT 5"


async def test_history_filters_by_dataset_and_deletes(client):
    token = await _register_and_login(client)
    ds_a = await _upload(client, token, filename="a.csv")
    ds_b = await _upload(client, token, filename="b.csv")

    await client.post(f"/datasets/{ds_a}/query", headers=_h(token), json=_base_query())
    await client.post(f"/datasets/{ds_b}/query", headers=_h(token), json=_base_query())

    all_history = await client.get("/datasets/query-history", headers=_h(token))
    assert len(all_history.json()) == 2

    filtered = await client.get("/datasets/query-history", params={"dataset_id": ds_a}, headers=_h(token))
    entries = filtered.json()
    assert len(entries) == 1
    assert entries[0]["dataset_id"] == ds_a

    deleted = await client.delete(f"/datasets/query-history/{entries[0]['id']}", headers=_h(token))
    assert deleted.status_code == 204

    remaining = await client.get("/datasets/query-history", headers=_h(token))
    assert len(remaining.json()) == 1

    cleared = await client.delete("/datasets/query-history", headers=_h(token))
    assert cleared.status_code == 204
    assert (await client.get("/datasets/query-history", headers=_h(token))).json() == []


async def test_clear_history_scoped_to_action_leaves_other_action_intact(client):
    """SqlEditor's "Clear all" only clears SQL history (?action=dataset.sql_query)
    - it must not also wipe dataset.query (query-builder) history, a different
    feature not shown on that page."""
    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)

    await client.post(f"/datasets/{dataset_id}/query", headers=_h(token), json=_base_query())
    await client.post(
        f"/datasets/{dataset_id}/sql", headers=_h(token), json={"sql": "SELECT * FROM dataset LIMIT 5"}
    )
    assert len((await client.get("/datasets/query-history", headers=_h(token))).json()) == 2

    cleared = await client.delete(
        "/datasets/query-history", params={"action": "dataset.sql_query"}, headers=_h(token)
    )
    assert cleared.status_code == 204

    remaining = (await client.get("/datasets/query-history", headers=_h(token))).json()
    assert len(remaining) == 1
    assert remaining[0]["action"] == "dataset.query"


async def test_delete_unknown_history_entry_404s(client):
    token = await _register_and_login(client)
    res = await client.delete(
        "/datasets/query-history/00000000-0000-0000-0000-000000000000", headers=_h(token)
    )
    assert res.status_code == 404


async def test_history_tenant_isolation(client):
    token_a = await _register_and_login(client, email="a@example.com", org_name="OrgA")
    dataset_id = await _upload(client, token_a)
    await client.post(f"/datasets/{dataset_id}/query", headers=_h(token_a), json=_base_query())

    token_b = await _register_and_login(client, email="b@example.com", org_name="OrgB")
    res = await client.get("/datasets/query-history", headers=_h(token_b))
    assert res.json() == []
