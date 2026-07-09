from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"

CSV = b"planned,operating,total,good\n100,90,180,171\n100,80,160,152\n"


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"]


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def _upload(client, token, data=CSV, filename="prod.csv"):
    return await client.post(
        "/datasets", files={"file": (filename, data, "text/csv")}, headers=_h(token)
    )


async def test_upload_list_get_delete(client):
    token = await _register_and_login(client)

    res = await _upload(client, token)
    assert res.status_code == 201, res.text
    ds = res.json()
    assert ds["row_count"] == 2
    assert [c["name"] for c in ds["columns"]] == ["planned", "operating", "total", "good"]

    lst = await client.get("/datasets", headers=_h(token))
    assert lst.status_code == 200
    assert len(lst.json()) == 1

    detail = await client.get(f"/datasets/{ds['id']}", headers=_h(token))
    assert detail.status_code == 200
    assert len(detail.json()["preview"]) == 2

    delete = await client.delete(f"/datasets/{ds['id']}", headers=_h(token))
    assert delete.status_code == 204

    gone = await client.get(f"/datasets/{ds['id']}", headers=_h(token))
    assert gone.status_code == 404


async def test_tenant_isolation(client):
    token_a = await _register_and_login(client, email="a@example.com", org_name="OrgA")
    token_b = await _register_and_login(client, email="b@example.com", org_name="OrgB")

    res = await _upload(client, token_a)
    dataset_id = res.json()["id"]

    # Org B sees none of Org A's datasets, and cannot fetch one by id.
    assert (await client.get("/datasets", headers=_h(token_b))).json() == []
    cross = await client.get(f"/datasets/{dataset_id}", headers=_h(token_b))
    assert cross.status_code == 404


async def test_bad_extension_rejected(client):
    token = await _register_and_login(client)
    res = await client.post(
        "/datasets",
        files={"file": ("malware.exe", b"MZ\x90\x00", "application/octet-stream")},
        headers=_h(token),
    )
    assert res.status_code == 400


async def test_empty_file_rejected(client):
    token = await _register_and_login(client)
    res = await _upload(client, token, data=b"")
    assert res.status_code == 400
