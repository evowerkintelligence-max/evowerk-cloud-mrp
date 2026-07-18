from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.db import session as db_session

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret

# actual=80 vs target=100 -> 20% miss, above the 5% threshold -> triggered.
PT_CSV = b"actual,target\n80,100\n"


class _RecordingHandler(BaseHTTPRequestHandler):
    received: list[dict] = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        self.__class__.received.append(json.loads(body))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def webhook_server():
    _RecordingHandler.received = []
    server = HTTPServer(("127.0.0.1", 0), _RecordingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, _RecordingHandler
    server.shutdown()
    thread.join()


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post(
        "/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD}
    )
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    return login.json()["access_token"]


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def _upload(client, token, data=PT_CSV):
    res = await client.post("/datasets", files={"file": ("pt.csv", data, "text/csv")}, headers=_h(token))
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def test_scan_all_fires_webhook_with_live_triggered_state(client, webhook_server):
    server, handler = webhook_server
    url = f"http://127.0.0.1:{server.server_port}/andon"

    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)
    rule = await client.post(
        "/alerts/rules",
        json={
            "name": "Line 1 pace", "type": "production_target", "dataset_id": dataset_id,
            "config": {"actual_col": "actual", "target_col": "target", "miss_threshold_pct": 5, "webhook_url": url},
        },
        headers=_h(token),
    )
    assert rule.status_code == 201, rule.text

    scan = await client.post("/alerts/scan", headers=_h(token))
    assert scan.status_code == 200, scan.text
    body = scan.json()
    assert body["triggered_count"] == 1

    assert len(handler.received) == 1
    payload = handler.received[0]
    assert payload["triggered"] is True
    assert payload["type"] == "production_target"
    assert "message" in payload and "evaluated_at" in payload


async def test_scan_one_does_not_fire_webhook(client, webhook_server):
    """scan_one backs the rule editor's interactive "Test" button - firing an
    external relay on every keystroke while tuning a threshold would be spam."""
    server, handler = webhook_server
    url = f"http://127.0.0.1:{server.server_port}/andon"

    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)
    rule = await client.post(
        "/alerts/rules",
        json={
            "name": "Line 1 pace", "type": "production_target", "dataset_id": dataset_id,
            "config": {"actual_col": "actual", "target_col": "target", "miss_threshold_pct": 5, "webhook_url": url},
        },
        headers=_h(token),
    )
    rule_id = rule.json()["id"]

    res = await client.post(f"/alerts/scan/{rule_id}", headers=_h(token))
    assert res.status_code == 200, res.text
    assert res.json()["triggered"] is True
    assert len(handler.received) == 0


async def test_scan_all_skips_webhook_when_not_configured(client, webhook_server):
    server, handler = webhook_server

    token = await _register_and_login(client)
    dataset_id = await _upload(client, token)
    await client.post(
        "/alerts/rules",
        json={
            "name": "Line 1 pace", "type": "production_target", "dataset_id": dataset_id,
            "config": {"actual_col": "actual", "target_col": "target", "miss_threshold_pct": 5},
        },
        headers=_h(token),
    )

    scan = await client.post("/alerts/scan", headers=_h(token))
    assert scan.status_code == 200, scan.text
    assert len(handler.received) == 0


async def test_cbm_anomaly_rule_fires_webhook(client, webhook_server):
    server, handler = webhook_server
    url = f"http://127.0.0.1:{server.server_port}/andon"

    token = await _register_and_login(client)
    integ = await client.post(
        "/integrations", headers=_h(token),
        json={"name": "Line 1 MQTT", "type": "mqtt", "config": {"broker": "test", "topic": "sensors/#"}},
    )
    stream = await client.post(
        "/realtime/streams", headers=_h(token),
        json={"integration_id": integ.json()["id"], "name": "Line 1", "machine": "M1", "sensor_map": {"vib_x": "v"}},
    )
    assert stream.status_code == 200, stream.text
    stream_id = stream.json()["stream_id"]

    async with db_session.async_session_factory() as db:
        from app.models.realtime import RealtimeStream
        from app.models.sensor import SensorAnomaly, SensorReading
        from sqlalchemy import select

        org_id = (
            await db.execute(select(RealtimeStream.org_id).where(RealtimeStream.id == uuid.UUID(stream_id)))
        ).scalar_one()

        for _ in range(24):
            db.add(SensorReading(org_id=org_id, machine="M1", sensor="vib_x", value=10.0, ts=datetime.now(timezone.utc)))
        reading = SensorReading(org_id=org_id, machine="M1", sensor="vib_x", value=50.0, ts=datetime.now(timezone.utc))
        db.add(reading)
        await db.flush()
        db.add(SensorAnomaly(org_id=org_id, machine="M1", sensor="vib_x", reading_id=reading.id, violations=[1]))
        await db.commit()

    rule = await client.post(
        "/alerts/rules",
        json={
            "name": "CBM anomaly", "type": "cbm_anomaly", "severity": "warning",
            "config": {"machine": "M1", "webhook_url": url},
        },
        headers=_h(token),
    )
    assert rule.status_code == 201, rule.text

    scan = await client.post("/alerts/scan", headers=_h(token))
    assert scan.status_code == 200, scan.text
    assert scan.json()["triggered_count"] == 1
    assert len(handler.received) == 1
    assert handler.received[0]["triggered"] is True


async def test_cbm_rul_rule_reports_insufficient_data_without_breakdown_history(client, webhook_server):
    server, handler = webhook_server
    url = f"http://127.0.0.1:{server.server_port}/andon"

    token = await _register_and_login(client)
    integ = await client.post(
        "/integrations", headers=_h(token),
        json={"name": "Line 1 MQTT", "type": "mqtt", "config": {"broker": "test", "topic": "sensors/#"}},
    )
    stream = await client.post(
        "/realtime/streams", headers=_h(token),
        json={"integration_id": integ.json()["id"], "name": "Line 1", "machine": "M9", "sensor_map": {"vib_x": "v"}},
    )
    assert stream.status_code == 200, stream.text

    rule = await client.post(
        "/alerts/rules",
        json={
            "name": "CBM RUL", "type": "cbm_rul", "severity": "warning",
            "config": {"machine": "M9", "threshold": 0.65, "webhook_url": url},
        },
        headers=_h(token),
    )
    assert rule.status_code == 201, rule.text

    scan = await client.post("/alerts/scan", headers=_h(token))
    assert scan.status_code == 200, scan.text
    assert scan.json()["triggered_count"] == 0
    assert len(handler.received) == 1
    assert handler.received[0]["triggered"] is False
    assert "insufficient" in handler.received[0]["message"].lower()
