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


async def test_scan_all_does_not_repush_while_rule_stays_triggered(client, push_server):
    """The bug this fixes: a sustained breach must push once on the off->on
    transition, not on every subsequent scan while it's still triggered -
    otherwise a client polling /alerts/scan every 60s (see the mobile app's
    AlertsScreen) turns into a repeat-notification spam loop."""
    _, handler = push_server

    token = await _register_and_login(client)
    await client.post(
        "/device-tokens", json={"expo_push_token": "ExponentPushToken[abc]"}, headers=_h(token)
    )
    dataset_id = await _upload(client, token, PT_CSV_MISS)
    await _create_production_target_rule(client, token, dataset_id)

    first_scan = await client.post("/alerts/scan", headers=_h(token))
    assert first_scan.status_code == 200, first_scan.text
    assert first_scan.json()["triggered_count"] == 1
    assert len(handler.received) == 1

    second_scan = await client.post("/alerts/scan", headers=_h(token))
    assert second_scan.status_code == 200, second_scan.text
    assert second_scan.json()["triggered_count"] == 1  # still triggered...
    assert len(handler.received) == 1  # ...but no second push


async def test_scan_all_repushes_after_resolve_and_retrigger(client, push_server):
    """The flag must not be a one-way latch: once a rule resolves (goes
    on->off) its last_triggered resets, so a later re-trigger of the SAME
    rule pushes again instead of staying silent forever after the first
    breach. Repoints one rule's dataset_id across three scans (miss -> meet
    -> miss) rather than creating a second rule, so a passing test can only
    mean the original rule's flag actually flipped back to False - a fresh
    rule would push on its own first scan regardless of the reset logic."""
    _, handler = push_server

    token = await _register_and_login(client)
    await client.post(
        "/device-tokens", json={"expo_push_token": "ExponentPushToken[abc]"}, headers=_h(token)
    )
    miss_dataset_id = await _upload(client, token, PT_CSV_MISS)
    rule_id = await _create_production_target_rule(client, token, miss_dataset_id)

    first_scan = await client.post("/alerts/scan", headers=_h(token))
    assert first_scan.status_code == 200, first_scan.text
    assert first_scan.json()["triggered_count"] == 1
    assert len(handler.received) == 1  # off->on: pushes
    assert handler.received[0][0]["data"]["rule_id"] == rule_id

    # Scan again while still triggered: this is what forces last_triggered to
    # actually latch True (not just stay at its False default) - without it,
    # a fully-disabled reset line would be indistinguishable from a working
    # one for the rest of this test.
    still_triggered_scan = await client.post("/alerts/scan", headers=_h(token))
    assert still_triggered_scan.status_code == 200, still_triggered_scan.text
    assert still_triggered_scan.json()["triggered_count"] == 1
    assert len(handler.received) == 1  # still triggered: no repush

    # Resolve: repoint the SAME rule at a dataset that meets target.
    meet_dataset_id = await _upload(client, token, PT_CSV_MEET)
    update = await client.put(
        f"/alerts/rules/{rule_id}", json={"dataset_id": meet_dataset_id}, headers=_h(token)
    )
    assert update.status_code == 200, update.text

    resolved_scan = await client.post("/alerts/scan", headers=_h(token))
    assert resolved_scan.status_code == 200, resolved_scan.text
    assert resolved_scan.json()["triggered_count"] == 0
    assert len(handler.received) == 1  # on->off: no push

    # Re-trigger: repoint the SAME rule back to a dataset that misses target.
    miss_dataset_id_2 = await _upload(client, token, PT_CSV_MISS)
    update_2 = await client.put(
        f"/alerts/rules/{rule_id}", json={"dataset_id": miss_dataset_id_2}, headers=_h(token)
    )
    assert update_2.status_code == 200, update_2.text

    retrigger_scan = await client.post("/alerts/scan", headers=_h(token))
    assert retrigger_scan.status_code == 200, retrigger_scan.text
    assert retrigger_scan.json()["triggered_count"] == 1

    assert len(handler.received) == 2  # off->on again: pushes
    assert handler.received[1][0]["data"]["rule_id"] == rule_id


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
