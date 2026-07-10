from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # satisfies policy: 12+ chars, letter + digit  # pragma: allowlist secret

CSV = b"sku,qty,region\nA-1,10,east\nA-2,5,west\nA-1,3,west\n"


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"]


async def _upload(client, token, *, filename="stock.csv", content=CSV, name=None):
    headers = {"Authorization": f"Bearer {token}"}
    data = {"name": name} if name else {}
    return await client.post(
        "/datasets",
        headers=headers,
        files={"file": (filename, content, "text/csv")},
        data=data,
    )


async def test_upload_sniffs_columns_and_row_count(client):
    token = await _register_and_login(client)
    res = await _upload(client, token, name="Stock levels")
    assert res.status_code == 201
    body = res.json()
    assert body["name"] == "Stock levels"
    assert body["row_count"] == 3
    assert {c["name"] for c in body["columns"]} == {"sku", "qty", "region"}


async def test_upload_rejects_unsupported_extension(client):
    token = await _register_and_login(client)
    res = await _upload(client, token, filename="stock.exe", content=b"not a dataset")
    assert res.status_code == 400


async def test_upload_rejects_empty_file(client):
    token = await _register_and_login(client)
    res = await _upload(client, token, content=b"")
    assert res.status_code == 400


async def test_list_preview_health_and_delete(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    uploaded = (await _upload(client, token)).json()
    dataset_id = uploaded["id"]

    listed = await client.get("/datasets", headers=headers)
    assert listed.status_code == 200
    assert any(d["id"] == dataset_id for d in listed.json())

    detail = await client.get(f"/datasets/{dataset_id}", headers=headers)
    assert detail.status_code == 200
    assert len(detail.json()["preview"]) == 3

    health = await client.get(f"/datasets/{dataset_id}/health", headers=headers)
    assert health.status_code == 200
    assert health.json()["row_count"] == 3

    deleted = await client.delete(f"/datasets/{dataset_id}", headers=headers)
    assert deleted.status_code == 204
    assert (await client.get(f"/datasets/{dataset_id}", headers=headers)).status_code == 404


async def test_tenant_isolation(client):
    token_a = await _register_and_login(client, email="a@example.com", org_name="A Co")
    token_b = await _register_and_login(client, email="b@example.com", org_name="B Co")
    dataset_id = (await _upload(client, token_a)).json()["id"]

    res = await client.get(
        f"/datasets/{dataset_id}", headers={"Authorization": f"Bearer {token_b}"}
    )
    assert res.status_code == 404
