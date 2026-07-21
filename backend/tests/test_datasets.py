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


async def test_rbac_blocks_non_owner_from_delete(client, test_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.security import hash_password
    from app.models.user import User

    token = await _register_and_login(client, email="owner2@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    me = await client.get("/orgs/me", headers=headers)
    org_id = me.json()["id"]
    dataset_id = (await _upload(client, token)).json()["id"]

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        import uuid as uuid_module

        session.add(
            User(
                org_id=uuid_module.UUID(org_id),
                email="member2@example.com",
                hashed_password=hash_password(PASSWORD),
                role="member",
                is_active=True,
            )
        )
        await session.commit()

    login = await client.post("/auth/login", json={"email": "member2@example.com", "password": PASSWORD})
    member_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    res = await client.delete(f"/datasets/{dataset_id}", headers=member_headers)
    assert res.status_code == 403
    assert (await client.get(f"/datasets/{dataset_id}", headers=headers)).status_code == 200


async def test_tenant_isolation(client):
    token_a = await _register_and_login(client, email="a@example.com", org_name="A Co")
    token_b = await _register_and_login(client, email="b@example.com", org_name="B Co")
    dataset_id = (await _upload(client, token_a)).json()["id"]

    res = await client.get(
        f"/datasets/{dataset_id}", headers={"Authorization": f"Bearer {token_b}"}
    )
    assert res.status_code == 404


async def test_clean_trims_dedupes_and_fills(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dirty = b"sku,qty,region\n A-1 ,10,east\nA-2,,west\nA-1,10,east\n"
    dataset_id = (await _upload(client, token, filename="dirty.csv", content=dirty)).json()["id"]

    res = await client.post(
        f"/datasets/{dataset_id}/clean",
        headers=headers,
        json={
            "trim_strings": True,
            "drop_duplicates": True,
            "fill_missing": True,
            "fill_strategy": "custom",
            "fill_value": "0",
        },
    )
    assert res.status_code == 201
    body = res.json()
    assert set(body["ops"]) == {"Fill missing with custom", "Trim strings", "Drop duplicates"}
    cleaned_id = body["dataset"]["id"]
    assert body["dataset"]["row_count"] == 2  # the duplicate "A-1,10,east" row is dropped

    preview = (await client.get(f"/datasets/{cleaned_id}", headers=headers)).json()["preview"]
    row_a1 = next(r for r in preview if r["sku"] == "A-1")
    assert row_a1["qty"] == "10"
    row_a2 = next(r for r in preview if r["sku"] == "A-2")
    assert row_a2["qty"] == "0"


async def test_clean_removes_outliers(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    rows = "sku,qty\n" + "".join(f"A-{i},{10 + i}\n" for i in range(9)) + "A-9,1000\n"
    dataset_id = (await _upload(client, token, filename="outliers.csv", content=rows.encode())).json()["id"]

    res = await client.post(
        f"/datasets/{dataset_id}/clean",
        headers=headers,
        json={
            "trim_strings": False,
            "drop_duplicates": False,
            "remove_outliers": True,
            "outlier_columns": ["qty"],
            "outlier_strategy": "remove",
        },
    )
    assert res.status_code == 201
    body = res.json()
    assert "Remove outliers (IQR 1.5)" in body["ops"]
    assert body["dataset"]["row_count"] == 9


async def test_clean_rejects_cross_tenant_dataset(client):
    token_a = await _register_and_login(client, email="a@example.com", org_name="A Co")
    token_b = await _register_and_login(client, email="b@example.com", org_name="B Co")
    dataset_id = (await _upload(client, token_a)).json()["id"]

    res = await client.post(
        f"/datasets/{dataset_id}/clean",
        headers={"Authorization": f"Bearer {token_b}"},
        json={},
    )
    assert res.status_code == 404


async def test_select_columns_drops_and_reports_the_rest(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = (await _upload(client, token)).json()["id"]

    res = await client.post(
        f"/datasets/{dataset_id}/transform/select-columns",
        headers=headers,
        json={"columns": ["sku", "qty"]},
    )
    assert res.status_code == 201
    body = res.json()
    assert body["dropped_columns"] == ["region"]
    assert {c["name"] for c in body["dataset"]["columns"]} == {"sku", "qty"}


async def test_select_columns_rejects_when_none_exist(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    dataset_id = (await _upload(client, token)).json()["id"]

    res = await client.post(
        f"/datasets/{dataset_id}/transform/select-columns",
        headers=headers,
        json={"columns": ["nonexistent"]},
    )
    assert res.status_code == 400


async def test_pivot_wide_to_tall_and_back(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    wide = b"timestamp,temp,pressure\n2024-01-01,20,100\n2024-01-02,21,101\n"
    dataset_id = (await _upload(client, token, filename="wide.csv", content=wide)).json()["id"]

    tall_res = await client.post(
        f"/datasets/{dataset_id}/transform/pivot",
        headers=headers,
        json={"direction": "wide_to_tall", "timestamp_col": "timestamp"},
    )
    assert tall_res.status_code == 201
    tall = tall_res.json()
    assert {c["name"] for c in tall["columns"]} == {"timestamp", "tag", "value"}
    assert tall["row_count"] == 4  # 2 rows x 2 tag columns

    wide_res = await client.post(
        f"/datasets/{tall['id']}/transform/pivot",
        headers=headers,
        json={"direction": "tall_to_wide", "timestamp_col": "timestamp"},
    )
    assert wide_res.status_code == 201
    wide_back = wide_res.json()
    assert {c["name"] for c in wide_back["columns"]} == {"timestamp", "temp", "pressure"}
    assert wide_back["row_count"] == 2


async def test_pivot_requires_timestamp_column(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    no_ts = b"a,b,c\n1,2,3\n"
    dataset_id = (await _upload(client, token, filename="no_ts.csv", content=no_ts)).json()["id"]

    res = await client.post(
        f"/datasets/{dataset_id}/transform/pivot",
        headers=headers,
        json={"direction": "tall_to_wide", "timestamp_col": "nonexistent"},
    )
    assert res.status_code == 400
