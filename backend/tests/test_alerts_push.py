from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.services import alerts as alerts_service

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret
PT_CSV_MISS = b"actual,target\n80,100\n"
PT_CSV_MEET = b"actual,target\n100,100\n"


class _RecordingHandler(BaseHTTPRequestHandler):
    received: list[list[dict]] = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        self.__class__.received.append(json.loads(body))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def push_server(monkeypatch):
    _RecordingHandler.received = []
    server = HTTPServer(("127.0.0.1", 0), _RecordingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(alerts_service, "_EXPO_PUSH_URL", f"http://127.0.0.1:{server.server_port}/push")
    yield server, _RecordingHandler
    server.shutdown()
    thread.join()


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post("/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD})
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    return login.json()["access_token"]


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def _upload(client, token, data):
    res = await client.post("/datasets", files={"file": ("pt.csv", data, "text/csv")}, headers=_h(token))
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def _create_production_target_rule(client, token, dataset_id):
    rule = await client.post(
        "/alerts/rules",
        json={
            "name": "Line 1 pace", "type": "production_target", "dataset_id": dataset_id,
            "config": {"actual_col": "actual", "target_col": "target", "miss_threshold_pct": 5},
        },
        headers=_h(token),
    )
    assert rule.status_code == 201, rule.text
    return rule.json()["id"]


async def test_scan_all_sends_data_minimal_push_when_rule_triggers(client, push_server):
    _, handler = push_server

    token = await _register_and_login(client)
    await client.post(
        "/device-tokens", json={"expo_push_token": "ExponentPushToken[abc]"}, headers=_h(token)
    )
    dataset_id = await _upload(client, token, PT_CSV_MISS)
    rule_id = await _create_production_target_rule(client, token, dataset_id)

    scan = await client.post("/alerts/scan", headers=_h(token))
    assert scan.status_code == 200, scan.text
    assert scan.json()["triggered_count"] == 1

    assert len(handler.received) == 1
    assert handler.received[0] == [
        {
            "to": "ExponentPushToken[abc]",
            "title": "Evowerk Alert",
            "body": "A rule triggered - open the app for details.",
            "data": {"rule_id": rule_id},
        }
    ]


async def test_scan_all_does_not_push_when_rule_not_triggered(client, push_server):
    _, handler = push_server

    token = await _register_and_login(client)
    await client.post(
        "/device-tokens", json={"expo_push_token": "ExponentPushToken[abc]"}, headers=_h(token)
    )
    dataset_id = await _upload(client, token, PT_CSV_MEET)
    await _create_production_target_rule(client, token, dataset_id)

    scan = await client.post("/alerts/scan", headers=_h(token))
    assert scan.status_code == 200, scan.text
    assert scan.json()["triggered_count"] == 0
    assert len(handler.received) == 0


async def test_scan_all_skips_push_when_no_device_tokens_registered(client, push_server):
    _, handler = push_server

    token = await _register_and_login(client)
    dataset_id = await _upload(client, token, PT_CSV_MISS)
    await _create_production_target_rule(client, token, dataset_id)

    scan = await client.post("/alerts/scan", headers=_h(token))
    assert scan.status_code == 200, scan.text
    assert scan.json()["triggered_count"] == 1
    assert len(handler.received) == 0


async def test_scan_one_does_not_send_push(client, push_server):
    """scan_one backs the rule editor's interactive Test button - same
    reasoning as the existing scan_one/webhook test in test_alerts_webhook.py:
    firing a push on every keystroke while tuning a threshold would be spam."""
    _, handler = push_server

    token = await _register_and_login(client)
    await client.post(
        "/device-tokens", json={"expo_push_token": "ExponentPushToken[abc]"}, headers=_h(token)
    )
    dataset_id = await _upload(client, token, PT_CSV_MISS)
    rule_id = await _create_production_target_rule(client, token, dataset_id)

    res = await client.post(f"/alerts/scan/{rule_id}", headers=_h(token))
    assert res.status_code == 200, res.text
    assert res.json()["triggered"] is True
    assert len(handler.received) == 0


async def test_scan_all_push_batch_excludes_other_orgs_tokens(client, push_server):
    """Verify org isolation: org A's alerts include only org A's tokens, never org B's."""
    _, handler = push_server

    token_a = await _register_and_login(client, email="owner-a@example.com", org_name="Acme A")
    token_b = await _register_and_login(client, email="owner-b@example.com", org_name="Acme B")

    await client.post(
        "/device-tokens", json={"expo_push_token": "ExponentPushToken[org-a-device]"}, headers=_h(token_a)
    )
    await client.post(
        "/device-tokens", json={"expo_push_token": "ExponentPushToken[org-b-device]"}, headers=_h(token_b)
    )

    dataset_id = await _upload(client, token_a, PT_CSV_MISS)
    await _create_production_target_rule(client, token_a, dataset_id)

    scan = await client.post("/alerts/scan", headers=_h(token_a))
    assert scan.status_code == 200, scan.text
    assert scan.json()["triggered_count"] == 1

    assert len(handler.received) == 1
    tokens_notified = {msg["to"] for msg in handler.received[0]}
    assert tokens_notified == {"ExponentPushToken[org-a-device]"}
