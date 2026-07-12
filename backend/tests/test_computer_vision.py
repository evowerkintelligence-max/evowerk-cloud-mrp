from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret

# A minimal valid 1x1 PNG (base64-decoded), just to exercise the upload path -
# no GEMINI_API_KEY is set in tests, so this never reaches the vision API.
_PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753"
    "de0000000c4944415478da6360606060000000050001a5f645400000000049454e44ae426082"
)


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post("/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD})
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"]


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def test_analyze_falls_back_to_demo_without_api_key(client):
    token = await _register_and_login(client)
    res = await client.post(
        "/computer-vision/analyze",
        headers=_h(token),
        data={"task": "defect_detection"},
        files={"image": ("part.png", _PNG_1X1, "image/png")},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["mode"] == "demo"
    assert body["task"] == "defect_detection"
    assert len(body["findings"]) >= 1


async def test_analyze_ppe_task_demo(client):
    token = await _register_and_login(client)
    res = await client.post(
        "/computer-vision/analyze",
        headers=_h(token),
        data={"task": "ppe_compliance"},
        files={"image": ("part.png", _PNG_1X1, "image/png")},
    )
    assert res.status_code == 200
    assert res.json()["compliance_pct"] == 50.0


async def test_analyze_rejects_unknown_task(client):
    token = await _register_and_login(client)
    res = await client.post(
        "/computer-vision/analyze",
        headers=_h(token),
        data={"task": "not_a_real_task"},
        files={"image": ("part.png", _PNG_1X1, "image/png")},
    )
    assert res.status_code == 400


async def test_analyze_rejects_bad_mime(client):
    token = await _register_and_login(client)
    res = await client.post(
        "/computer-vision/analyze",
        headers=_h(token),
        data={"task": "defect_detection"},
        files={"image": ("part.txt", b"not an image", "text/plain")},
    )
    assert res.status_code == 400
