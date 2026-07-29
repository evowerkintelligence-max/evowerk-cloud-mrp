from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret

CSV = b"machine,downtime_minutes,date\nM1,30,2026-01-01\nM2,45,2026-01-02\nM1,10,2026-01-03\n"


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post("/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD})
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"]


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def _upload(client, token, data=CSV, filename="dt.csv"):
    res = await client.post("/datasets", files={"file": (filename, data, "text/csv")}, headers=_h(token))
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def test_message_without_datasets_prompts_upload(client):
    token = await _register_and_login(client)
    res = await client.post("/data-chat/message", headers=_h(token), json={"question": "what columns do I have?"})
    assert res.status_code == 200, res.text
    assert "upload" in res.json()["answer"].lower()


async def test_message_uses_heuristic_fallback_without_api_key(client):
    token = await _register_and_login(client)
    await _upload(client, token)

    res = await client.post("/data-chat/message", headers=_h(token), json={"question": "what columns do I have?"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert "machine" in body["answer"].lower()
    assert body["datasets_used"] == ["dt.csv"]


async def test_message_numeric_summary_heuristic(client):
    token = await _register_and_login(client)
    await _upload(client, token)

    res = await client.post("/data-chat/message", headers=_h(token), json={"question": "what is the average downtime?"})
    assert res.status_code == 200, res.text
    assert "downtime_minutes" in res.json()["answer"]


async def test_message_scoped_to_specific_dataset(client):
    token = await _register_and_login(client)
    await _upload(client, token, data=b"machine,val\nA,1\n", filename="first.csv")
    ds2 = await _upload(client, token, data=b"other,val\nB,2\n", filename="other.csv")

    res = await client.post(
        "/data-chat/message", headers=_h(token), json={"question": "what columns?", "dataset_ids": [ds2]}
    )
    assert res.status_code == 200
    assert res.json()["datasets_used"] == ["other.csv"]


async def test_heuristic_does_not_mistake_performing_for_min(client):
    """"perfor-min-g" used to match the numeric-stats branch by substring."""
    token = await _register_and_login(client)
    await _upload(client, token)

    res = await client.post(
        "/data-chat/message", headers=_h(token), json={"question": "what was the worst performing machine last month?"}
    )
    assert res.status_code == 200, res.text
    answer = res.json()["answer"]
    assert "numeric column statistics" not in answer
    assert "AI is not configured" in answer
