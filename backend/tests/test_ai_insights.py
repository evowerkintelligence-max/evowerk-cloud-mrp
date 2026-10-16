from __future__ import annotations
from tests._test_secrets import TEST_PASSWORD

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = TEST_PASSWORD


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return reg.json(), login.json()["access_token"]


async def _ask(client, token, question, *, dataset_id=None, expect_status=200):
    body = {"question": question}
    if dataset_id:
        body["dataset_id"] = dataset_id
    res = await client.post("/ai/ask", json=body, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == expect_status, res.text
    return res.json() if expect_status == 200 else res


DATASET_CSV = b"sku,qty,region\nA-1,10,east\nA-2,5,west\nA-1,3,west\n"


async def _upload_dataset(client, token, *, content=DATASET_CSV, filename="stock.csv"):
    res = await client.post(
        "/datasets",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": (filename, content, "text/csv")},
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def test_empty_question_returns_400(client, test_engine):
    _, token = await _register_and_login(client)
    res = await client.post("/ai/ask", json={"question": "   "}, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 400


async def test_missing_dataset_id_returns_400(client, test_engine):
    _, token = await _register_and_login(client)
    res = await client.post("/ai/ask", json={"question": "how many rows?"}, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 400


async def test_dataset_ask_sum_aggregate(client, test_engine):
    _, token = await _register_and_login(client)
    dataset_id = await _upload_dataset(client, token)

    body = await _ask(client, token, "what is the total qty?", dataset_id=dataset_id)
    assert body["intents"] == ["dataset"]
    assert "18.00" in body["narrative"]  # 10 + 5 + 3
    assert body["warnings"] == []


async def test_dataset_ask_count(client, test_engine):
    _, token = await _register_and_login(client)
    dataset_id = await _upload_dataset(client, token)

    body = await _ask(client, token, "how many rows are there?", dataset_id=dataset_id)
    assert "3 row" in body["narrative"]


async def test_dataset_ask_non_numeric_column_rejected(client, test_engine):
    _, token = await _register_and_login(client)
    dataset_id = await _upload_dataset(client, token)

    body = await _ask(client, token, "average region", dataset_id=dataset_id)
    assert "isn't numeric" in body["narrative"]
    assert body["warnings"]  # names the numeric columns instead


async def test_dataset_ask_unmatched_question_falls_back_to_summary(client, test_engine):
    _, token = await _register_and_login(client)
    dataset_id = await _upload_dataset(client, token)

    body = await _ask(client, token, "tell me something interesting", dataset_id=dataset_id)
    assert "3 rows" in body["narrative"]
    assert "sku" in body["warnings"][0]
    assert "qty" in body["warnings"][0]


async def test_dataset_ask_unknown_dataset_404(client, test_engine):
    _, token = await _register_and_login(client)
    res = await _ask(
        client, token, "total qty", dataset_id="00000000-0000-0000-0000-000000000000", expect_status=404
    )
    assert res.status_code == 404


async def test_dataset_ask_org_isolation(client, test_engine):
    _, token_a = await _register_and_login(client, email="dsa@example.com", org_name="Org DS A")
    dataset_id = await _upload_dataset(client, token_a)

    _, token_b = await _register_and_login(client, email="dsb@example.com", org_name="Org DS B")
    await _ask(client, token_b, "total qty", dataset_id=dataset_id, expect_status=404)
