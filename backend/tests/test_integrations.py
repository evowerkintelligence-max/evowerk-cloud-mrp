from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # satisfies policy: 12+ chars, letter + digit  # pragma: allowlist secret


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post("/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD})
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"]


class _FakeRestHandler(BaseHTTPRequestHandler):
    records = [{"id": 1, "region": "east", "qty": 10}, {"id": 2, "region": "west", "qty": 5}]

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"value": self.records}).encode())

    def log_message(self, *args):
        pass  # keep test output quiet


@pytest.fixture
def fake_rest_server():
    server = HTTPServer(("127.0.0.1", 0), _FakeRestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    thread.join()


async def test_rest_integration_test_preview_sync(client, fake_rest_server):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    base_url = f"http://127.0.0.1:{fake_rest_server.server_port}"

    create = await client.post(
        "/integrations",
        headers=headers,
        json={
            "name": "Fake ERP",
            "type": "erp_rest",
            "config": {"base_url": base_url, "endpoint": "/records", "response_path": "value", "pagination": "none"},
        },
    )
    assert create.status_code == 201, create.text
    integration_id = create.json()["id"]

    test_res = await client.post(f"/integrations/{integration_id}/test", headers=headers)
    assert test_res.status_code == 200
    assert test_res.json()["ok"] is True
    assert test_res.json()["status"] == "connected"

    preview = await client.post(f"/integrations/{integration_id}/preview", headers=headers, params={"n": 10})
    assert preview.status_code == 200
    assert preview.json()["row_count"] == 2
    assert {"id", "region", "qty"} <= set(preview.json()["columns"])

    sync = await client.post(f"/integrations/{integration_id}/sync", headers=headers, json={})
    assert sync.status_code == 200, sync.text
    assert sync.json()["rows_synced"] == 2
    dataset_id = sync.json()["dataset"]["id"]

    ds = await client.get(f"/datasets/{dataset_id}", headers=headers)
    assert ds.status_code == 200
    assert ds.json()["row_count"] == 2

    # Integration metadata reflects the sync.
    item = await client.get(f"/integrations/{integration_id}", headers=headers)
    assert item.json()["last_sync_rows"] == 2
    assert item.json()["last_sync_dataset_id"] == dataset_id


async def test_secret_masked_and_preserved_on_update(client, fake_rest_server):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    create = await client.post(
        "/integrations",
        headers=headers,
        json={
            "name": "Secure API",
            "type": "erp_rest",
            "config": {
                "base_url": f"http://127.0.0.1:{fake_rest_server.server_port}",
                "endpoint": "/records",
                "auth_type": "bearer",
                "auth_value": "super-secret-token",
            },
        },
    )
    body = create.json()
    assert body["config"]["auth_value"] == "•" * 8  # masked, never echoed back

    # Update just the name — omitting auth_value must not wipe the stored secret.
    patched = await client.patch(
        f"/integrations/{body['id']}", headers=headers, json={"name": "Secure API v2"}
    )
    assert patched.status_code == 200
    assert patched.json()["config"]["auth_value"] == "•" * 8

    test_res = await client.post(f"/integrations/{body['id']}/test", headers=headers)
    assert test_res.status_code == 200
    assert test_res.json()["ok"] is True  # connector still had the real (decrypted) secret to use


async def test_csv_integration_requires_upload_then_syncs(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    create = await client.post(
        "/integrations", headers=headers, json={"name": "Nightly export", "type": "csv", "config": {}}
    )
    integration_id = create.json()["id"]

    # No file uploaded yet - test should fail cleanly, not crash.
    test_res = await client.post(f"/integrations/{integration_id}/test", headers=headers)
    assert test_res.json()["ok"] is False

    upload = await client.post(
        f"/integrations/{integration_id}/upload",
        headers=headers,
        files={"file": ("export.csv", b"sku,qty\nA-1,10\nA-2,5\n", "text/csv")},
    )
    assert upload.status_code == 200, upload.text
    assert upload.json()["config"]["file_path"]  # server-resolved path, not client-supplied

    sync = await client.post(f"/integrations/{integration_id}/sync", headers=headers, json={})
    assert sync.status_code == 200, sync.text
    assert sync.json()["rows_synced"] == 2


async def test_invalid_type_rejected(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.post("/integrations", headers=headers, json={"name": "Bad", "type": "ftp", "config": {}})
    assert res.status_code == 400


async def test_integration_types_endpoint_lists_all_nine_connectors(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.get("/integrations/types", headers=headers)
    assert res.status_code == 200
    ids = {t["id"] for t in res.json()["types"]}
    assert ids == {
        "csv", "excel", "sqlserver", "oracle", "sap_odata", "erp_rest", "mes_rest",
        "opcua", "mqtt", "opcda", "pi_webapi",
    }
