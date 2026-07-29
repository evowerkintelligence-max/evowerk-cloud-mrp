from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post("/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD})
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    return login.json()["access_token"]


async def test_create_operator(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.post("/operators", json={"name": "J Kim", "code": "E-100"}, headers=headers)
    assert res.status_code == 201
    body = res.json()
    assert body["name"] == "J Kim"
    assert body["code"] == "E-100"
    assert body["badge_id"] is None
    assert body["status"] == "active"
    assert body["needs_review"] is False


async def test_create_duplicate_name_rejected(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    await client.post("/operators", json={"name": "J Kim"}, headers=headers)
    dup = await client.post("/operators", json={"name": "j kim"}, headers=headers)
    assert dup.status_code == 409


async def test_create_duplicate_badge_id_rejected(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    await client.post("/operators", json={"name": "A", "badge_id": "B1"}, headers=headers)
    dup = await client.post("/operators", json={"name": "B", "badge_id": "B1"}, headers=headers)
    assert dup.status_code == 409


async def test_list_needs_review_filter(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    await client.post("/operators", json={"name": "Johnson"}, headers=headers)
    close = (await client.post("/operators", json={"name": "Jonson"}, headers=headers)).json()

    flagged = await client.get("/operators?needs_review=true", headers=headers)
    assert len(flagged.json()) == 1
    assert flagged.json()[0]["id"] == close["id"]


async def test_list_includes_duplicate_hint_for_needs_review_rows(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    target = (await client.post("/operators", json={"name": "Johnson"}, headers=headers)).json()
    close = (await client.post("/operators", json={"name": "Jonson"}, headers=headers)).json()

    listed = (await client.get("/operators", headers=headers)).json()
    by_id = {o["id"]: o for o in listed}
    assert by_id[close["id"]]["duplicate_of_id"] == target["id"]
    assert by_id[close["id"]]["duplicate_of_name"] == "Johnson"
    assert by_id[target["id"]]["duplicate_of_id"] is None
    assert by_id[target["id"]]["duplicate_of_name"] is None


async def test_patch_operator_clears_needs_review(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    await client.post("/operators", json={"name": "Johnson"}, headers=headers)
    close = (await client.post("/operators", json={"name": "Jonson"}, headers=headers)).json()
    assert close["needs_review"] is True

    patched = await client.patch(
        f"/operators/{close['id']}", json={"needs_review": False, "name": "Jonson"}, headers=headers
    )
    assert patched.status_code == 200
    assert patched.json()["needs_review"] is False


async def test_merge_deletes_source_and_keeps_target(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    target = (await client.post("/operators", json={"name": "J Kim"}, headers=headers)).json()
    source = (await client.post("/operators", json={"name": "J. Kim"}, headers=headers)).json()

    merged = await client.post(
        f"/operators/{source['id']}/merge", json={"target_operator_id": target["id"]}, headers=headers
    )
    assert merged.status_code == 200
    assert merged.json()["id"] == target["id"]

    listed = await client.get("/operators", headers=headers)
    ids = {o["id"] for o in listed.json()}
    assert target["id"] in ids
    assert source["id"] not in ids


async def test_merge_into_self_rejected(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    op = (await client.post("/operators", json={"name": "J Kim"}, headers=headers)).json()
    res = await client.post(f"/operators/{op['id']}/merge", json={"target_operator_id": op["id"]}, headers=headers)
    assert res.status_code == 400


async def test_delete_operator(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    op = (await client.post("/operators", json={"name": "J Kim"}, headers=headers)).json()
    res = await client.delete(f"/operators/{op['id']}", headers=headers)
    assert res.status_code == 204
    listed = await client.get("/operators", headers=headers)
    assert listed.json() == []
