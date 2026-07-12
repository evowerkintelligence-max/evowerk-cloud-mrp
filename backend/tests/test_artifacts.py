from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # satisfies policy: 12+ chars, letter + digit  # pragma: allowlist secret


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post("/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD})
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"]


async def _create_artifact(db_session, *, org_id, created_by):
    from app.services import artifacts as artifacts_service

    return await artifacts_service.create_artifact(
        db_session,
        org_id=org_id,
        created_by=created_by,
        kind="export",
        filename="report.csv",
        data=b"a,b\n1,2\n",
        content_type="text/csv",
    )


async def test_create_list_get_download_delete_artifact(client, test_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    # There's no /auth/me endpoint - fetch the just-registered user directly
    # from the DB, via the same session factory conftest wires up for the
    # test client, to get the org_id/user_id needed to seed an artifact row.
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as db:
        from sqlalchemy import select

        from app.models.user import User

        result = await db.execute(select(User).where(User.email == "owner@example.com"))
        user = result.scalar_one()
        artifact = await _create_artifact(db, org_id=user.org_id, created_by=user.id)

    list_res = await client.get("/artifacts", headers=headers)
    assert list_res.status_code == 200
    body = list_res.json()
    assert body["total"] == 1
    assert body["items"][0]["filename"] == "report.csv"

    get_res = await client.get(f"/artifacts/{artifact.id}", headers=headers)
    assert get_res.status_code == 200
    assert get_res.json()["kind"] == "export"

    download_res = await client.get(f"/artifacts/{artifact.id}/download", headers=headers)
    assert download_res.status_code == 200
    assert download_res.content == b"a,b\n1,2\n"
    assert "report.csv" in download_res.headers["content-disposition"]

    delete_res = await client.delete(f"/artifacts/{artifact.id}", headers=headers)
    assert delete_res.status_code == 204

    get_after_delete = await client.get(f"/artifacts/{artifact.id}", headers=headers)
    assert get_after_delete.status_code == 404


async def test_list_artifacts_scoped_to_org(client, test_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    token_a = await _register_and_login(client, email="a@example.com", org_name="OrgA")
    token_b = await _register_and_login(client, email="b@example.com", org_name="OrgB")

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as db:
        from sqlalchemy import select

        from app.models.user import User

        result = await db.execute(select(User).where(User.email == "a@example.com"))
        user_a = result.scalar_one()
        await _create_artifact(db, org_id=user_a.org_id, created_by=user_a.id)

    res_b = await client.get("/artifacts", headers={"Authorization": f"Bearer {token_b}"})
    assert res_b.status_code == 200
    assert res_b.json()["total"] == 0

    res_a = await client.get("/artifacts", headers={"Authorization": f"Bearer {token_a}"})
    assert res_a.status_code == 200
    assert res_a.json()["total"] == 1
