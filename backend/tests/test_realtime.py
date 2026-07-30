from __future__ import annotations

import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.services import realtime as realtime_service
from app.services.connectors import get_connector

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
def _no_background_polling(monkeypatch):
    """No collector polls on its own here. A real collector task would poll
    and write samples through conftest's single StaticPool SQLite connection
    at the same time as the request under test, and cancelling it mid-write
    invalidates that shared connection for everyone (production pools one
    connection per session, so this is a test-DB artifact). Tests that need a
    sample drive `_poll_once()` by hand instead, which is deterministic and
    needs no sleeps."""
    monkeypatch.setattr(realtime_service.StreamCollector, "start", lambda self: None)


@pytest.fixture(autouse=True)
async def _stop_all_runners_after_each_test():
    """Streams are real background tasks registered in module-level state -
    without this, a collector from one test could keep polling into the next."""
    yield
    await realtime_service.stop_all_runners()


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


async def _drive_one_poll(stream_id):
    """One deterministic collection window for the stream's registered
    collector. Its `start` was no-op'd by _no_background_polling, so `_run`
    never built the connector: build it the same way `_run` does."""
    collector = realtime_service.get_runner(stream_id)
    assert collector is not None, "the route should have registered a collector"
    if collector._conn is None:
        collector._conn = get_connector(collector.integration_type, collector.integration_config)
    await collector._seed_counters()
    await collector._poll_once()
    return collector


@pytest.mark.asyncio
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

    # Collect exactly one window, against the fake REST server, by hand.
    await _drive_one_poll(stream_id)

    snap = (await client.get(f"/realtime/streams/{stream_id}/snapshot", headers=headers)).json()
    assert snap["status"] == "running"
    assert snap["rows_collected"] > 0

    # The fake REST server returns the same single row every poll: the first
    # window ingests it, later windows dedup to empty. Metrics therefore live
    # in the first non-empty history point, not necessarily the latest sample.
    history = (await client.get(f"/realtime/streams/{stream_id}/history", headers=headers)).json()
    assert len(history["points"]) > 0
    first_window = next(p for p in history["points"] if p["metrics"].get("throughput"))
    assert first_window["metrics"]["throughput"] > 0
    assert first_window["metrics"]["scrap_rate_pct"] == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_stop_then_start_resumes_collection(client, fake_rest_server):
    """/stop tears the collector out of the registry and reports "stopped";
    /start puts a fresh one back. Asserted on the registry, not on a written
    sample: whether a sample has landed yet is a timing question, and
    collection itself is covered by test_stream_collects_and_computes_metrics."""
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
    assert start.status_code == 200, start.text
    # "starting" (no sample yet), not "stopped": desired_status flipped back.
    assert start.json()["status"] == "starting"
    assert realtime_service.get_runner(stream_id) is not None


@pytest.mark.asyncio
async def test_stopped_stream_keeps_final_counters_and_reports_stopped(client, fake_rest_server):
    """A stopped stream reports its real final counters from its last
    persisted sample, with status overlaid to 'stopped' and name from the
    stream row (samples don't carry the human-readable name)."""
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

    # Seed a persisted sample directly (deterministic, no timing dependence).
    from app.db import session as db_session
    from app.models.realtime import RealtimeSample, RealtimeStream
    from sqlalchemy import select

    async with db_session.async_session_factory() as db:
        stream = (
            await db.execute(select(RealtimeStream).where(RealtimeStream.id == uuid.UUID(stream_id)))
        ).scalar_one()
        db.add(RealtimeSample(
            org_id=stream.org_id, stream_id=stream.id, status="running",
            rows_in_window=3, rows_collected=3, duration_s=5, last_tick_s=1.0,
            metrics={"throughput": 1.0}, raw_rows=[],
        ))
        await db.commit()

    stop = await client.post(f"/realtime/streams/{stream_id}/stop", headers=headers)
    assert stop.json()["status"] == "stopped"
    assert stop.json()["name"] == "Line 9"

    snap = (await client.get(f"/realtime/streams/{stream_id}/snapshot", headers=headers)).json()
    assert snap["status"] == "stopped"
    assert snap["name"] == "Line 9"
    assert snap["rows_collected"] == 3  # real final counters, not a zeroed shell
    assert snap["metrics"]["throughput"] == 1.0


@pytest.mark.asyncio
async def test_stopped_stream_samples_report_stopped_status(client, fake_rest_server):
    """/samples must agree with /snapshot: a stopped stream's persisted
    samples still say "running" (stopping writes no sample), so the route
    overlays "stopped" without touching the counters."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    base_url = f"http://127.0.0.1:{fake_rest_server.server_port}"
    integration_id = await _create_rest_integration(client, headers, base_url)

    create = await client.post(
        "/realtime/streams",
        headers=headers,
        json={"integration_id": integration_id, "name": "Line 11", "interval_seconds": 1, "column_map": {}},
    )
    stream_id = create.json()["stream_id"]

    from app.db import session as db_session
    from app.models.realtime import RealtimeSample, RealtimeStream
    from sqlalchemy import select

    async with db_session.async_session_factory() as db:
        stream = (
            await db.execute(select(RealtimeStream).where(RealtimeStream.id == uuid.UUID(stream_id)))
        ).scalar_one()
        db.add(RealtimeSample(
            org_id=stream.org_id, stream_id=stream.id, status="running",
            rows_in_window=2, rows_collected=7, duration_s=5, last_tick_s=1.0,
            metrics={"throughput": 2.0}, raw_rows=[],
        ))
        await db.commit()

    stop = await client.post(f"/realtime/streams/{stream_id}/stop", headers=headers)
    assert stop.status_code == 200, stop.text

    body = (await client.get(f"/realtime/streams/{stream_id}/samples", headers=headers)).json()
    assert len(body["samples"]) == 1
    sample = body["samples"][0]
    assert sample["status"] == "stopped"
    assert sample["rows_collected"] == 7  # only the status was overridden
    assert sample["metrics"]["throughput"] == 2.0


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
    await realtime_service.stop_all_runners()
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

    await realtime_service.stop_all_runners()
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


class _GrowingStubConnector:
    """Feeds fetch_all one queued batch per call, repeating the last. Each
    batch here adds exactly one row, so every poll dedups down to one new
    row and writes exactly one sample."""

    def __init__(self, batches):
        self.batches = list(batches)

    def fetch_all(self):
        if len(self.batches) > 1:
            return self.batches.pop(0)
        return self.batches[0]


@pytest.mark.asyncio
async def test_samples_endpoint_returns_only_new_rows_per_cursor(client, fake_rest_server):
    """The incremental-sync contract: a client that passes back the cursor
    it last received gets only samples written since, never a duplicate."""
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    base_url = f"http://127.0.0.1:{fake_rest_server.server_port}"
    integration_id = await _create_rest_integration(client, headers, base_url)

    create = await client.post(
        "/realtime/streams",
        headers=headers,
        json={"integration_id": integration_id, "interval_seconds": 1, "column_map": {"units_col": "units"}},
    )
    stream_id = create.json()["stream_id"]

    # Three hand-driven windows, one new row each: no sleeps, no background task.
    collector = realtime_service.get_runner(stream_id)
    assert collector is not None
    collector._conn = _GrowingStubConnector([
        [{"units": 1}],
        [{"units": 1}, {"units": 2}],
        [{"units": 1}, {"units": 2}, {"units": 3}],
    ])
    await collector._seed_counters()
    await collector._poll_once()

    first = (await client.get(f"/realtime/streams/{stream_id}/samples", headers=headers)).json()
    assert [s["rows_collected"] for s in first["samples"]] == [1]  # no cursor: latest only
    cursor = first["cursor"]
    assert cursor is not None

    await collector._poll_once()
    await collector._poll_once()

    page = (
        await client.get(f"/realtime/streams/{stream_id}/samples?since={cursor}", headers=headers)
    ).json()
    # Exactly the two windows written after the cursor, oldest first. The
    # sample the first call already delivered (rows_collected 1) is not resent.
    assert [s["rows_collected"] for s in page["samples"]] == [2, 3]
    assert page["cursor"] > cursor

    # The cursor is a real sample id, and the page is everything past it.
    from app.db import session as db_session
    from app.models.realtime import RealtimeSample
    from sqlalchemy import select

    async with db_session.async_session_factory() as db:
        ids = (
            await db.execute(
                select(RealtimeSample.id)
                .where(RealtimeSample.stream_id == uuid.UUID(stream_id))
                .order_by(RealtimeSample.id)
            )
        ).scalars().all()
    assert cursor == ids[0]
    assert page["cursor"] == ids[-1]
    assert len(page["samples"]) == len(ids) - 1  # nothing skipped, nothing duplicated


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
    # Every route that hands out another org's stream data, not just /snapshot:
    # /samples and /history are the ones that egress rows.
    for path in ("snapshot", "samples", "samples?since=0", "history"):
        res = await client.get(f"/realtime/streams/{stream_id}/{path}", headers=headers_b)
        assert res.status_code == 404, f"{path} -> {res.status_code}"


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
