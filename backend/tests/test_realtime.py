from __future__ import annotations

import asyncio
import json
import os
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.services import realtime as realtime_service
from app.services import realtime_cache

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret


class _FakeRestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"value": [{"units": 10, "scrap": 1}]}).encode())

    def log_message(self, *args):
        pass


@pytest.fixture
def fake_rest_server():
    server = HTTPServer(("127.0.0.1", 0), _FakeRestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    thread.join()


@pytest.fixture(autouse=True)
def _stop_all_runners_after_each_test():
    """Streams are real background threads registered in module-level state -
    without this, a runner from one test could keep polling into the next."""
    yield
    realtime_service.stop_all_runners()


@pytest.fixture(autouse=True)
async def _cache_consumer():
    """The ASGITransport test client never runs app.main's lifespan (see
    conftest.client), so realtime_cache's background consumer - which
    _snapshot_dict reads from - never starts on its own in this process.
    Only start it when a broker is actually reachable (REDPANDA_BROKERS set,
    same gate every other live-infra test in this plan uses); otherwise
    leave it unstarted so these tests keep exercising the "not seen yet"
    fallback path exactly as before, without needing live infra."""
    if os.environ.get("REDPANDA_BROKERS"):
        await realtime_cache.start_cache_consumer()
        yield
        await realtime_cache.stop_cache_consumer()
    else:
        yield


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post("/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD})
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"]


async def _create_rest_integration(client, headers, base_url):
    res = await client.post(
        "/integrations",
        headers=headers,
        json={
            "name": "Fake feed",
            "type": "erp_rest",
            "config": {"base_url": base_url, "endpoint": "/records", "response_path": "value", "pagination": "none"},
        },
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _h(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get("REDPANDA_BROKERS"),
    reason="needs a live Redpanda + Flink cluster to actually produce a windowed "
    "metrics snapshot (e.g. `docker compose up redpanda flink-jobmanager "
    "flink-taskmanager` and submit realtime_metrics_job.py) - without one, "
    "realtime_cache never populates and this can only ever see the "
    "not-yet-seen fallback, same as every other live-infra test in this plan",
)
async def test_stream_collects_and_computes_metrics(client, fake_rest_server):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    base_url = f"http://127.0.0.1:{fake_rest_server.server_port}"
    integration_id = await _create_rest_integration(client, headers, base_url)

    create = await client.post(
        "/realtime/streams",
        headers=headers,
        json={
            "integration_id": integration_id,
            "name": "Line 1",
            "interval_seconds": 1,
            "column_map": {"units_col": "units", "scrap_col": "scrap", "total_units_col": "units"},
        },
    )
    assert create.status_code == 200, create.text
    stream_id = create.json()["stream_id"]

    # Give the background collector a couple of ticks. Poll budget is wider
    # than the Flink job's fixed 5s tumbling window (~12s total) so metrics
    # have time to arrive even against live infra.
    for _ in range(40):
        snap = (await client.get(f"/realtime/streams/{stream_id}/snapshot", headers=headers)).json()
        if snap["rows_collected"] > 0:
            break
        await asyncio.sleep(0.3)

    assert snap["status"] == "running"
    assert snap["rows_collected"] > 0
    assert snap["metrics"]["throughput"] > 0
    assert snap["metrics"]["scrap_rate_pct"] == pytest.approx(10.0)

    history = (await client.get(f"/realtime/streams/{stream_id}/history", headers=headers)).json()
    assert len(history["points"]) > 0


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get("REDPANDA_BROKERS"),
    reason="needs a live Redpanda + Flink cluster - see "
    "test_stream_collects_and_computes_metrics for why",
)
async def test_stop_then_start_resumes_collection(client, fake_rest_server):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    base_url = f"http://127.0.0.1:{fake_rest_server.server_port}"
    integration_id = await _create_rest_integration(client, headers, base_url)

    create = await client.post(
        "/realtime/streams",
        headers=headers,
        json={"integration_id": integration_id, "interval_seconds": 1, "column_map": {}},
    )
    stream_id = create.json()["stream_id"]

    stop = await client.post(f"/realtime/streams/{stream_id}/stop", headers=headers)
    assert stop.json()["status"] == "stopped"
    assert realtime_service.get_runner(stream_id) is None

    start = await client.post(f"/realtime/streams/{stream_id}/start", headers=headers)
    assert realtime_service.get_runner(stream_id) is not None

    # The /start response's own status field reflects whatever realtime_cache
    # has cached at that instant, which is empty immediately after a restart -
    # a real snapshot needs a Flink window (fixed 5s) to close first. Poll the
    # same way test_stream_collects_and_computes_metrics does rather than
    # asserting on the immediate response.
    snap = start.json()
    for _ in range(40):
        if snap["status"] == "running":
            break
        snap = (await client.get(f"/realtime/streams/{stream_id}/snapshot", headers=headers)).json()
        await asyncio.sleep(0.3)
    assert snap["status"] == "running"


@pytest.mark.asyncio
async def test_stopped_stream_with_stale_cache_reports_stopped_and_real_name(client, fake_rest_server, monkeypatch):
    """Regression test for the final-review bug in _snapshot_dict: once
    realtime_cache has ever cached a real snapshot for a stream_id,
    get_snapshot() keeps returning it forever (nothing overwrites/evicts a
    stopped stream's last snapshot) - and that cached snapshot's "name" is
    the raw stream_id (the Flink job's MetricSnapshot.name = key), not
    stream.name. No live broker is needed to prove _snapshot_dict handles
    both: a fake cached snapshot is injected directly into a CacheConsumer
    instance rather than running one against Redpanda."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    base_url = f"http://127.0.0.1:{fake_rest_server.server_port}"
    integration_id = await _create_rest_integration(client, headers, base_url)

    create = await client.post(
        "/realtime/streams",
        headers=headers,
        json={"integration_id": integration_id, "name": "Line 9", "interval_seconds": 1, "column_map": {}},
    )
    stream_id = create.json()["stream_id"]

    fake_consumer = realtime_cache.CacheConsumer(group_id="test", brokers="unused")
    fake_consumer._snapshots[stream_id] = {
        "ts": 1.0, "stream_id": stream_id, "name": stream_id,
        "status": "running", "error": None,
        "rows_in_window": 3, "rows_collected": 3,
        "duration": "00:00:05", "duration_s": 5.0, "last_tick_s": 1.0,
        "metrics": {"throughput": 1.0},
    }
    monkeypatch.setattr(realtime_cache, "_instance", fake_consumer)

    # While running, the cached data is used but "name" is overlaid with the
    # real stream name instead of the raw stream_id.
    running_snap = (await client.get(f"/realtime/streams/{stream_id}/snapshot", headers=headers)).json()
    assert running_snap["status"] == "running"
    assert running_snap["name"] == "Line 9"

    # Stop the stream. The cache still holds the stale "running" snapshot
    # (nothing ever overwrites it), but the reported status must reflect the
    # stop, not the stale cache.
    stop = await client.post(f"/realtime/streams/{stream_id}/stop", headers=headers)
    assert stop.json()["status"] == "stopped"
    assert stop.json()["name"] == "Line 9"

    snap = (await client.get(f"/realtime/streams/{stream_id}/snapshot", headers=headers)).json()
    assert snap["status"] == "stopped"
    assert snap["name"] == "Line 9"


@pytest.mark.asyncio
async def test_resume_all_streams_reattaches_after_simulated_restart(client, fake_rest_server):
    """The whole point of persisting stream definitions in the DB: a process
    restart (redeploy/crash) shouldn't permanently lose a "running" stream."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    base_url = f"http://127.0.0.1:{fake_rest_server.server_port}"
    integration_id = await _create_rest_integration(client, headers, base_url)

    create = await client.post(
        "/realtime/streams",
        headers=headers,
        json={"integration_id": integration_id, "interval_seconds": 1, "column_map": {}},
    )
    stream_id = create.json()["stream_id"]
    assert realtime_service.get_runner(stream_id) is not None

    # Simulate a process restart: the in-memory registry is wiped, but the DB
    # row (desired_status="running") survives.
    realtime_service.stop_all_runners()
    assert realtime_service.get_runner(stream_id) is None

    await realtime_service.resume_all_streams()
    assert realtime_service.get_runner(stream_id) is not None


@pytest.mark.asyncio
async def test_resume_all_streams_skips_stopped_streams(client, fake_rest_server):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    base_url = f"http://127.0.0.1:{fake_rest_server.server_port}"
    integration_id = await _create_rest_integration(client, headers, base_url)

    create = await client.post(
        "/realtime/streams",
        headers=headers,
        json={"integration_id": integration_id, "interval_seconds": 1, "column_map": {}},
    )
    stream_id = create.json()["stream_id"]
    await client.post(f"/realtime/streams/{stream_id}/stop", headers=headers)

    realtime_service.stop_all_runners()
    await realtime_service.resume_all_streams()
    assert realtime_service.get_runner(stream_id) is None


@pytest.mark.asyncio
async def test_delete_stream_stops_runner_and_removes_definition(client, fake_rest_server):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    base_url = f"http://127.0.0.1:{fake_rest_server.server_port}"
    integration_id = await _create_rest_integration(client, headers, base_url)

    create = await client.post(
        "/realtime/streams",
        headers=headers,
        json={"integration_id": integration_id, "interval_seconds": 1, "column_map": {}},
    )
    stream_id = create.json()["stream_id"]

    delete = await client.delete(f"/realtime/streams/{stream_id}", headers=headers)
    assert delete.status_code == 204
    assert realtime_service.get_runner(stream_id) is None

    missing = await client.get(f"/realtime/streams/{stream_id}/snapshot", headers=headers)
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_sse_generator_yields_first_snapshot_frame(client, fake_rest_server):
    """Exercises app.api.routes.realtime._sse_generator directly rather than
    through a live client.stream() HTTP round-trip: httpx's ASGITransport
    doesn't reliably signal "client disconnected" back to the app when a
    stream() block exits early, so an app-side `while True` generator like
    this one can hang forever waiting for a disconnect the test transport
    never delivers. The SSE route's headers are static config passed
    straight to StreamingResponse (media_type/Cache-Control), not logic, so
    they don't need their own live-request test; this test covers what
    actually is this module's logic - the row lookup and snapshot framing."""
    from app.api.routes.realtime import _sse_generator

    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    base_url = f"http://127.0.0.1:{fake_rest_server.server_port}"
    integration_id = await _create_rest_integration(client, headers, base_url)

    create = await client.post(
        "/realtime/streams",
        headers=headers,
        json={"integration_id": integration_id, "interval_seconds": 1, "column_map": {}},
    )
    stream_id = create.json()["stream_id"]
    org_id = uuid.UUID((await client.get("/orgs/me", headers=headers)).json()["id"])
    await client.post(f"/realtime/streams/{stream_id}/stop", headers=headers)

    gen = _sse_generator(uuid.UUID(stream_id), org_id, request=None)
    try:
        first = await gen.__anext__()
        assert first.startswith("data: ")
        payload = json.loads(first[len("data: ") :])
        assert payload["stream_id"] == stream_id
        assert payload["status"] == "stopped"
    finally:
        await gen.aclose()


@pytest.mark.asyncio
async def test_list_streams_attaches_alerts_to_the_right_stream(client, fake_rest_server):
    """GET /realtime/streams batches every stream's stream_metric alert rules
    into one query (app.services.alerts.stream_alerts_bulk) - this checks the
    SQL-side stream_id filter actually scopes each rule to its own stream
    instead of leaking a rule onto every stream in the response."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    base_url = f"http://127.0.0.1:{fake_rest_server.server_port}"
    integration_id = await _create_rest_integration(client, headers, base_url)

    stream_a = (
        await client.post(
            "/realtime/streams", headers=headers,
            json={"integration_id": integration_id, "name": "A", "interval_seconds": 1, "column_map": {}},
        )
    ).json()["stream_id"]
    stream_b = (
        await client.post(
            "/realtime/streams", headers=headers,
            json={"integration_id": integration_id, "name": "B", "interval_seconds": 1, "column_map": {}},
        )
    ).json()["stream_id"]

    rule = await client.post(
        "/alerts/rules", headers=headers,
        json={
            "name": "A watch", "type": "stream_metric",
            "config": {"stream_id": stream_a, "metric_name": "throughput", "comparison": "gt", "threshold": -1},
        },
    )
    assert rule.status_code == 201, rule.text

    listed = (await client.get("/realtime/streams", headers=headers)).json()["streams"]
    by_id = {s["stream_id"]: s for s in listed}
    assert len(by_id[stream_a]["alerts"]) == 1
    assert by_id[stream_a]["alerts"][0]["rule_name"] == "A watch"
    assert by_id[stream_b]["alerts"] == []


@pytest.mark.asyncio
async def test_cannot_access_another_orgs_stream(client, fake_rest_server):
    token_a = await _register_and_login(client, email="a@example.com", org_name="OrgA")
    headers_a = {"Authorization": f"Bearer {token_a}"}
    base_url = f"http://127.0.0.1:{fake_rest_server.server_port}"
    integration_id = await _create_rest_integration(client, headers_a, base_url)
    create = await client.post(
        "/realtime/streams",
        headers=headers_a,
        json={"integration_id": integration_id, "interval_seconds": 1, "column_map": {}},
    )
    stream_id = create.json()["stream_id"]

    token_b = await _register_and_login(client, email="b@example.com", org_name="OrgB")
    headers_b = {"Authorization": f"Bearer {token_b}"}
    res = await client.get(f"/realtime/streams/{stream_id}/snapshot", headers=headers_b)
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_create_stream_with_machine_and_sensor_map(client):
    token = await _register_and_login(client)
    integ = await client.post(
        "/integrations",
        headers=_h(token),
        json={"name": "Line 1 MQTT", "type": "mqtt", "config": {"broker": "test", "topic": "sensors/#"}},
    )
    assert integ.status_code == 201, integ.text
    integration_id = integ.json()["id"]

    res = await client.post(
        "/realtime/streams",
        headers=_h(token),
        json={
            "integration_id": integration_id,
            "name": "Line 1 sensors",
            "machine": "M1",
            "sensor_map": {"vib_x": "vibration_x_mm_s", "brg_temp": "bearing_temp_c"},
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["stream_id"]
