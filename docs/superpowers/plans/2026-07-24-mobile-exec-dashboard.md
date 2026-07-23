# Mobile Exec Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a read-only Expo/React Native mobile app (KPI dashboard cards + alert status) with push notifications, backed by two small additions to the existing FastAPI backend, per `docs/superpowers/specs/2026-07-24-mobile-exec-dashboard-design.md`.

**Architecture:** The mobile app is a pure consumer of endpoints that already exist (`/auth/login`, `/kpi-dashboards/{id}/cards`, `/alerts/rules`, `/alerts/scan`) plus one new pair of endpoints (`/device-tokens`) for push registration. The backend push path reuses `app/services/alerts.py`'s existing `scan_all` trigger loop, adding a data-minimal Expo push send alongside the existing webhook fire. The app has no fixed backend to call — every customer self-hosts — so it asks for a server URL on first launch instead of assuming a relative-path origin the way the web frontend does.

**Tech Stack:** Backend: FastAPI/SQLAlchemy/Alembic (existing). Mobile: Expo (managed) + React Native + TypeScript, `@react-navigation` (native-stack + bottom-tabs), `zustand` for state, `expo-secure-store` for persisted secrets, `expo-notifications` + `expo-device` for push.

## Global Constraints

- Never host customer data: the app talks directly to the customer's own deployment URL, never through any Evowerk-controlled server. (spec: "Connecting to a deployment")
- Push payloads are data-minimal: only `{rule_id}` travels through Apple/Google/Expo's relay — never `trigger.message`, `value`, or `offenders`. The real content is fetched by the app from the customer's own server after the user taps. (spec: "data-minimal push")
- v1 is read-only from the app: no create/edit/acknowledge actions, no column-mapping UI. (spec: Scope)
- The dashboard screen consumes `GET /kpi-dashboards/{id}/cards` (already-computed KPI cards), never the raw `/analytics/oee` or `/analytics/downtime` endpoints, which require a column-mapping payload unsuited to a phone. (spec: "Why reuse the KPI dashboard system")
- Testing: backend follows this repo's existing pytest/TDD convention (write failing test, then implementation). Mobile gets exactly one smoke test (`jest-expo`) plus manual Expo Go verification per screen — the spec explicitly scopes mobile testing down to this; do not add a broader RN test framework.
- Migration revision ids in this repo get a `# pragma: allowlist secret` comment (the repo's detect-secrets pre-commit hook flags bare hex strings) — see any file in `backend/alembic/versions/`.

## File Structure

Backend (existing repo):
- `backend/app/models/device_token.py` — new `DeviceToken` model
- `backend/alembic/versions/d1e5f8a72c93_device_tokens.py` — new migration
- `backend/alembic/env.py` — add `device_token` to the model-import list (modify)
- `backend/tests/conftest.py` — add `device_token` to the model-import list (modify)
- `backend/app/api/routes/device_tokens.py` — new register/unregister routes
- `backend/app/main.py` — register the new router (modify)
- `backend/app/services/alerts.py` — new `_send_push`, wired into `scan_all` (modify)
- `backend/tests/test_device_tokens.py` — new
- `backend/tests/test_alerts_push.py` — new

Mobile (new `mobile/` directory at repo root, its own Expo project):
- `mobile/App.tsx` — root component, push listener wiring
- `mobile/src/store/secureStorage.ts` — zustand `StateStorage` adapter over `expo-secure-store`
- `mobile/src/store/serverStore.ts` — persisted server URL
- `mobile/src/store/authStore.ts` — persisted access/refresh tokens
- `mobile/src/store/deviceTokenStore.ts` — in-memory Expo push token (session-only)
- `mobile/src/api/client.ts` — `apiFetch` (base URL + bearer + refresh-on-401)
- `mobile/src/api/kpiDashboards.ts` — dashboard/cards fetchers
- `mobile/src/api/alerts.ts` — alert rules/scan fetchers
- `mobile/src/push/registerPush.ts` — register/unregister Expo push token
- `mobile/src/screens/ServerUrlScreen.tsx`
- `mobile/src/screens/LoginScreen.tsx`
- `mobile/src/screens/DashboardScreen.tsx`
- `mobile/src/screens/AlertsScreen.tsx`
- `mobile/src/navigation/RootNavigator.tsx`
- `mobile/src/navigation/MainTabs.tsx`
- `mobile/src/navigation/navigationRef.ts`
- `mobile/App.test.tsx` — smoke test
- `mobile/README.md`

---

## Task 1: `DeviceToken` model + migration

**Files:**
- Create: `backend/app/models/device_token.py`
- Create: `backend/alembic/versions/d1e5f8a72c93_device_tokens.py`
- Modify: `backend/alembic/env.py`
- Modify: `backend/tests/conftest.py`
- Test: `backend/tests/test_device_tokens.py` (model-level case only in this task; API cases added in Task 2)

**Interfaces:**
- Produces: `app.models.device_token.DeviceToken` — columns `id: uuid.UUID`, `org_id: uuid.UUID`, `user_id: uuid.UUID`, `expo_push_token: str` (unique), `created_at: datetime`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_device_tokens.py`:

```python
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.device_token import DeviceToken

pytestmark = pytest.mark.asyncio


async def test_device_token_round_trips(db_session):
    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    db_session.add(DeviceToken(org_id=org_id, user_id=user_id, expo_push_token="ExponentPushToken[abc123]"))
    await db_session.commit()

    row = (
        await db_session.execute(select(DeviceToken).where(DeviceToken.org_id == org_id))
    ).scalar_one()
    assert row.user_id == user_id
    assert row.expo_push_token == "ExponentPushToken[abc123]"
    assert row.created_at is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_device_tokens.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.models.device_token'`

- [ ] **Step 3: Create the model**

Create `backend/app/models/device_token.py`:

```python
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DeviceToken(Base):
    """One row per (user, device) - a registered Expo push token for the
    mobile app. Re-registering the same token (app foreground, re-login on
    the same device) is an upsert on expo_push_token, not an append - see
    app/api/routes/device_tokens.py."""

    __tablename__ = "device_tokens"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    expo_push_token: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
```

- [ ] **Step 4: Register the model in `conftest.py` and `alembic/env.py`**

In `backend/tests/conftest.py`, add `device_token` to the model-import tuple (alphabetical, after `dataset`):

```python
from app.models import (  # noqa: F401 - register models
    alert,
    audit,
    automl_live_binding,
    automl_run,
    bom,
    dashboard,
    dataset,
    device_token,
    downtime_tracking,
    forecast,
    ...
```

In `backend/alembic/env.py`, add `device_token` to its own model-import tuple (alphabetical, after `dataset`... note this file's list has no `dataset` entry currently — insert alphabetically after `bom`, before `forecast`):

```python
from app.models import (  # noqa: F401 - registers models with Base.metadata
    alert,
    audit,
    bom,
    device_token,
    dataset,
    forecast,
    ...
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_device_tokens.py -v`
Expected: PASS

- [ ] **Step 6: Write the migration**

Create `backend/alembic/versions/d1e5f8a72c93_device_tokens.py`:

```python
"""device_tokens: registered Expo push tokens for the mobile app

Revision ID: d1e5f8a72c93
Revises: a1f4c8b02d17
Create Date: 2026-07-24

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d1e5f8a72c93"  # pragma: allowlist secret
down_revision = "a1f4c8b02d17"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("expo_push_token", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("expo_push_token", name="uq_device_tokens_expo_push_token"),
    )
    op.create_index("ix_device_tokens_org_id", "device_tokens", ["org_id"])
    op.create_index("ix_device_tokens_user_id", "device_tokens", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_device_tokens_user_id", table_name="device_tokens")
    op.drop_index("ix_device_tokens_org_id", table_name="device_tokens")
    op.drop_table("device_tokens")
```

Verify it's a valid, single head: run `cd backend && python -m alembic heads` — expected output: `d1e5f8a72c93 (head)`.

- [ ] **Step 7: Commit**

```bash
git add backend/app/models/device_token.py backend/alembic/versions/d1e5f8a72c93_device_tokens.py backend/alembic/env.py backend/tests/conftest.py backend/tests/test_device_tokens.py
git commit -m "feat: add DeviceToken model and migration"
```

---

## Task 2: Device token register/unregister API

**Files:**
- Create: `backend/app/api/routes/device_tokens.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_device_tokens.py`

**Interfaces:**
- Consumes: `DeviceToken` model (Task 1); `CurrentUser`/`get_current_user` from `app.api.deps`.
- Produces: `POST /device-tokens` (body `{expo_push_token: str}`, 204) and `DELETE /device-tokens?expo_push_token=...` (204) — both used by the mobile app's `src/push/registerPush.ts` (Task 7).

- [ ] **Step 1: Write the failing tests**

Replace the full contents of `backend/tests/test_device_tokens.py` (adds
helpers and 4 API-level tests on top of Task 1's model-level test):

```python
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.device_token import DeviceToken

pytestmark = pytest.mark.asyncio

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret


async def test_device_token_round_trips(db_session):
    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    db_session.add(DeviceToken(org_id=org_id, user_id=user_id, expo_push_token="ExponentPushToken[abc123]"))
    await db_session.commit()

    row = (
        await db_session.execute(select(DeviceToken).where(DeviceToken.org_id == org_id))
    ).scalar_one()
    assert row.user_id == user_id
    assert row.expo_push_token == "ExponentPushToken[abc123]"
    assert row.created_at is not None


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post("/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD})
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    return login.json()["access_token"]


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def test_register_device_token(client):
    token = await _register_and_login(client)
    res = await client.post(
        "/device-tokens", json={"expo_push_token": "ExponentPushToken[abc123]"}, headers=_h(token)
    )
    assert res.status_code == 204


async def test_register_device_token_upserts_on_repeat(client):
    token = await _register_and_login(client)
    payload = {"expo_push_token": "ExponentPushToken[abc123]"}
    first = await client.post("/device-tokens", json=payload, headers=_h(token))
    second = await client.post("/device-tokens", json=payload, headers=_h(token))
    assert first.status_code == 204
    assert second.status_code == 204


async def test_delete_device_token(client):
    token = await _register_and_login(client)
    await client.post("/device-tokens", json={"expo_push_token": "ExponentPushToken[abc123]"}, headers=_h(token))
    res = await client.delete(
        "/device-tokens", params={"expo_push_token": "ExponentPushToken[abc123]"}, headers=_h(token)
    )
    assert res.status_code == 204


async def test_delete_device_token_from_other_user_is_noop(client, db_session):
    token_a = await _register_and_login(client, email="owner-a@example.com", org_name="Acme A")
    token_b = await _register_and_login(client, email="owner-b@example.com", org_name="Acme B")
    await client.post(
        "/device-tokens", json={"expo_push_token": "ExponentPushToken[shared]"}, headers=_h(token_a)
    )

    res = await client.delete(
        "/device-tokens", params={"expo_push_token": "ExponentPushToken[shared]"}, headers=_h(token_b)
    )
    assert res.status_code == 204

    remaining = (
        await db_session.execute(
            select(DeviceToken).where(DeviceToken.expo_push_token == "ExponentPushToken[shared]")
        )
    ).scalar_one_or_none()
    assert remaining is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_device_tokens.py -v`
Expected: `test_device_token_round_trips` (from Task 1) still PASSes; the 4 new
API-level tests FAIL with `404 Not Found` (no `/device-tokens` route
registered yet).

- [ ] **Step 3: Write the routes**

Create `backend/app/api/routes/device_tokens.py`:

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.models.device_token import DeviceToken

router = APIRouter(prefix="/device-tokens", tags=["device-tokens"])


class DeviceTokenRequest(BaseModel):
    expo_push_token: str = Field(..., min_length=1)


@router.post("", status_code=status.HTTP_204_NO_CONTENT)
async def register_device_token(
    payload: DeviceTokenRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    existing = (
        await db.execute(select(DeviceToken).where(DeviceToken.expo_push_token == payload.expo_push_token))
    ).scalar_one_or_none()
    if existing is not None:
        existing.org_id = current.org_id
        existing.user_id = current.id
    else:
        db.add(DeviceToken(org_id=current.org_id, user_id=current.id, expo_push_token=payload.expo_push_token))
    await db.commit()


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def unregister_device_token(
    expo_push_token: str,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    existing = (
        await db.execute(
            select(DeviceToken).where(
                DeviceToken.expo_push_token == expo_push_token,
                DeviceToken.user_id == current.id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        await db.delete(existing)
        await db.commit()
```

- [ ] **Step 4: Register the router**

In `backend/app/main.py`, add `device_tokens` to the `from app.api.routes import (...)` tuple (alphabetical, between `data_chat` and `datasets`):

```python
    data_chat,
    datasets,
    device_tokens,
    downtime,
```

And add the include line, right after `app.include_router(alerts.router)`:

```python
app.include_router(alerts.router)
app.include_router(device_tokens.router)
app.include_router(cbm.router)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_device_tokens.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/routes/device_tokens.py backend/app/main.py backend/tests/test_device_tokens.py
git commit -m "feat: add device token register/unregister API"
```

---

## Task 3: Data-minimal push on alert trigger

**Files:**
- Modify: `backend/app/services/alerts.py`
- Create: `backend/tests/test_alerts_push.py`

**Interfaces:**
- Consumes: `DeviceToken` model (Task 1); existing `AlertTrigger` dataclass and `scan_all(db, org_id)` in this same file.
- Produces: `_send_push(db, org_id, trigger)` — internal, called only from `scan_all`. No new public interface (push is a side effect of the existing scan, same as the existing webhook).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_alerts_push.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_alerts_push.py -v`
Expected: FAIL with `AttributeError: module 'app.services.alerts' has no attribute '_EXPO_PUSH_URL'` (the `push_server` fixture's `monkeypatch.setattr` fails first, before any assertions run).

- [ ] **Step 3: Add the push send, wired into `scan_all`**

In `backend/app/services/alerts.py`, add the import next to the other model imports (after `from app.models.dataset import Dataset`):

```python
from app.models.dataset import Dataset
from app.models.device_token import DeviceToken
from app.services import datasets as ds_service
```

Add the two new constants next to `_WEBHOOK_TIMEOUT`:

```python
_MAX_OFFENDERS_SHOWN = 5
_WEBHOOK_TIMEOUT = 5.0
_EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
_PUSH_TIMEOUT = 5.0
```

Add `_send_push` directly after the existing `_fire_webhook` function (both live under the `# ─── Andon / relay output ─────` section header):

```python
async def _send_push(db: AsyncSession, org_id: uuid.UUID, trigger: AlertTrigger) -> None:
    """Data-minimal push to every device registered for this org: title/body
    carry no business content, only trigger.rule_id - the app fetches the
    real message directly from this server after the user taps, so alert
    content never transits Apple/Google/Expo's relay (see the mobile design
    spec's "Connecting to a deployment" / data-minimal push sections - never
    hosting customer data extends to never routing it through a push relay).
    One batched HTTP call per scan (Expo's API accepts a list of messages),
    not one call per registered device.
    ponytail: no retry/backoff/queue - same accepted-risk shape as
    _fire_webhook; add if proven insufficient."""
    tokens = (
        await db.execute(select(DeviceToken.expo_push_token).where(DeviceToken.org_id == org_id))
    ).scalars().all()
    if not tokens:
        return
    messages = [
        {
            "to": expo_token, "title": "Evowerk Alert",
            "body": "A rule triggered - open the app for details.",
            "data": {"rule_id": trigger.rule_id},
        }
        for expo_token in tokens
    ]
    try:
        async with httpx.AsyncClient(timeout=_PUSH_TIMEOUT) as client:
            await client.post(_EXPO_PUSH_URL, json=messages)
    except httpx.HTTPError as exc:
        logger.warning("Push notification failed for org %s: %s", org_id, exc)
```

In `scan_all`, extend the trigger loop (currently just `_fire_webhook`) to also call `_send_push`, only for triggered rules:

```python
    # scan_all only - not scan_one, which backs the rule editor's interactive
    # "Test" button and would otherwise spam an external relay on every keystroke.
    for trigger in results:
        rule = rule_by_id.get(trigger.rule_id)
        if rule is not None:
            await _fire_webhook(rule, trigger)
        if trigger.triggered:
            await _send_push(db, org_id, trigger)

    return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_alerts_push.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the full backend suite to check for regressions**

Run: `cd backend && python -m pytest -q`
Expected: PASS, no failures (in particular `tests/test_alerts_webhook.py` and `tests/test_device_tokens.py` still pass — the webhook path is untouched, only a new step was added alongside it).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/alerts.py backend/tests/test_alerts_push.py
git commit -m "feat: send data-minimal push notification when an alert rule triggers"
```

---

## Task 4: Scaffold the Expo mobile app

**Files:**
- Create: `mobile/` (via `create-expo-app`, then modified below)
- Create: `mobile/src/store/secureStorage.ts`
- Create: `mobile/src/navigation/RootNavigator.tsx`
- Modify: `mobile/App.tsx`

**Interfaces:**
- Produces: `secureStorage: StateStorage` (from `mobile/src/store/secureStorage.ts`) — a zustand-compatible async storage adapter, consumed by Tasks 5 and 6's persisted stores. `RootNavigator` component, consumed by `App.tsx` and extended in Tasks 5, 6, 9.

- [ ] **Step 1: Scaffold the project**

Run from the repo root:

```bash
npx create-expo-app@latest mobile --template blank-typescript
```

Expected: creates `mobile/` with `App.tsx`, `app.json`, `package.json`, `tsconfig.json`, `babel.config.js`.

- [ ] **Step 2: Install dependencies**

```bash
cd mobile
npx expo install react-native-screens react-native-safe-area-context expo-secure-store expo-notifications expo-device
npm install @react-navigation/native @react-navigation/native-stack @react-navigation/bottom-tabs zustand
```

- [ ] **Step 3: Write the secure storage adapter**

Create `mobile/src/store/secureStorage.ts`:

```typescript
import * as SecureStore from "expo-secure-store";
import type { StateStorage } from "zustand/middleware";

// zustand's persist middleware expects getItem/setItem/removeItem returning
// Promise<string | null> / Promise<void> - expo-secure-store's Async API
// matches that shape exactly.
export const secureStorage: StateStorage = {
  getItem: (name) => SecureStore.getItemAsync(name),
  setItem: (name, value) => SecureStore.setItemAsync(name, value),
  removeItem: (name) => SecureStore.deleteItemAsync(name),
};
```

- [ ] **Step 4: Write a placeholder root navigator**

Create `mobile/src/navigation/RootNavigator.tsx`:

```typescript
import { createNativeStackNavigator } from "@react-navigation/native-stack";
import { StyleSheet, Text, View } from "react-native";

const Stack = createNativeStackNavigator();

function PlaceholderScreen() {
  return (
    <View style={styles.container}>
      <Text>Evowerk Mobile</Text>
    </View>
  );
}

export function RootNavigator() {
  return (
    <Stack.Navigator screenOptions={{ headerShown: false }}>
      <Stack.Screen name="Placeholder" component={PlaceholderScreen} />
    </Stack.Navigator>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, alignItems: "center", justifyContent: "center" },
});
```

- [ ] **Step 5: Wire it into `App.tsx`**

Replace the contents of `mobile/App.tsx`:

```typescript
import { NavigationContainer } from "@react-navigation/native";
import { SafeAreaProvider } from "react-native-safe-area-context";
import { RootNavigator } from "./src/navigation/RootNavigator";

export default function App() {
  return (
    <SafeAreaProvider>
      <NavigationContainer>
        <RootNavigator />
      </NavigationContainer>
    </SafeAreaProvider>
  );
}
```

- [ ] **Step 6: Typecheck**

Run: `cd mobile && npx tsc --noEmit`
Expected: no output, exit code 0.

- [ ] **Step 7: Manual verification**

Run: `cd mobile && npx expo start`
Expected: QR code prints; opening it in Expo Go (or pressing `i`/`a` for a simulator/emulator) shows a blank screen with the text "Evowerk Mobile".

- [ ] **Step 8: Commit**

```bash
cd mobile && git add -A
git commit -m "feat: scaffold Expo mobile app"
```

---

## Task 5: Server URL screen

**Files:**
- Create: `mobile/src/store/serverStore.ts`
- Create: `mobile/src/screens/ServerUrlScreen.tsx`
- Modify: `mobile/src/navigation/RootNavigator.tsx`

**Interfaces:**
- Consumes: `secureStorage` (Task 4).
- Produces: `useServerStore` (zustand hook: `{ serverUrl: string | null, setServerUrl(url), clear() }`), consumed by `mobile/src/api/client.ts` (Task 6) and `RootNavigator` (this task, extended in Task 6).

- [ ] **Step 1: Write the server URL store**

Create `mobile/src/store/serverStore.ts`:

```typescript
import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import { secureStorage } from "./secureStorage";

interface ServerState {
  serverUrl: string | null;
  setServerUrl: (url: string) => void;
  clear: () => void;
}

export const useServerStore = create<ServerState>()(
  persist(
    (set) => ({
      serverUrl: null,
      // Strip a trailing slash so `${serverUrl}${path}` (path always starts
      // with "/") never produces a doubled "//".
      setServerUrl: (url) => set({ serverUrl: url.replace(/\/+$/, "") }),
      clear: () => set({ serverUrl: null }),
    }),
    { name: "server", storage: createJSONStorage(() => secureStorage) },
  ),
);
```

- [ ] **Step 2: Write the screen**

Create `mobile/src/screens/ServerUrlScreen.tsx`:

```typescript
import { useState } from "react";
import { Button, StyleSheet, Text, TextInput, View } from "react-native";
import { useServerStore } from "../store/serverStore";

export function ServerUrlScreen() {
  const setServerUrl = useServerStore((s) => s.setServerUrl);
  const [value, setValue] = useState("");
  const [error, setError] = useState("");

  function handleContinue() {
    const trimmed = value.trim();
    if (!/^https?:\/\/.+/.test(trimmed)) {
      setError("Enter a full URL, e.g. https://mrp.yourcompany.com");
      return;
    }
    setError("");
    setServerUrl(trimmed);
  }

  return (
    <View style={styles.container}>
      <Text style={styles.label}>Your Evowerk server URL</Text>
      <TextInput
        style={styles.input}
        placeholder="https://mrp.yourcompany.com"
        autoCapitalize="none"
        autoCorrect={false}
        keyboardType="url"
        value={value}
        onChangeText={setValue}
      />
      {error ? <Text style={styles.error}>{error}</Text> : null}
      <Button title="Continue" onPress={handleContinue} />
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, justifyContent: "center", padding: 24 },
  label: { fontSize: 16, marginBottom: 8 },
  input: { borderWidth: 1, borderColor: "#ccc", borderRadius: 8, padding: 12, marginBottom: 8 },
  error: { color: "red", marginBottom: 8 },
});
```

- [ ] **Step 3: Branch the root navigator on `serverUrl`**

Replace `mobile/src/navigation/RootNavigator.tsx`:

```typescript
import { createNativeStackNavigator } from "@react-navigation/native-stack";
import { StyleSheet, Text, View } from "react-native";
import { useServerStore } from "../store/serverStore";
import { ServerUrlScreen } from "../screens/ServerUrlScreen";

const Stack = createNativeStackNavigator();

function PlaceholderScreen() {
  return (
    <View style={styles.container}>
      <Text>Evowerk Mobile</Text>
    </View>
  );
}

export function RootNavigator() {
  const serverUrl = useServerStore((s) => s.serverUrl);

  return (
    <Stack.Navigator screenOptions={{ headerShown: false }}>
      {!serverUrl ? (
        <Stack.Screen name="ServerUrl" component={ServerUrlScreen} />
      ) : (
        <Stack.Screen name="Placeholder" component={PlaceholderScreen} />
      )}
    </Stack.Navigator>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, alignItems: "center", justifyContent: "center" },
});
```

- [ ] **Step 4: Typecheck**

Run: `cd mobile && npx tsc --noEmit`
Expected: no output, exit code 0.

- [ ] **Step 5: Manual verification**

Run: `cd mobile && npx expo start`, open in Expo Go.
Expected: app shows the "Your Evowerk server URL" screen. Typing a non-URL value and pressing Continue shows the inline error. Typing `https://example.com` and pressing Continue navigates to the placeholder "Evowerk Mobile" screen. Reload the app (shake device → Reload, or press `r` in the terminal) — expected: it goes straight to the placeholder screen (the URL persisted).

- [ ] **Step 6: Commit**

```bash
cd mobile && git add -A
git commit -m "feat: add server URL onboarding screen"
```

---

## Task 6: Login (with MFA) + authenticated API client

**Files:**
- Create: `mobile/src/store/authStore.ts`
- Create: `mobile/src/api/client.ts`
- Create: `mobile/src/screens/LoginScreen.tsx`
- Modify: `mobile/src/navigation/RootNavigator.tsx`

**Interfaces:**
- Consumes: `useServerStore` (Task 5).
- Produces: `useAuthStore` (`{ accessToken, refreshToken, setTokens(access, refresh), clear() }`), consumed by every later API call and by Task 7's sign-out. `apiFetch(path: string, init?: RequestInit): Promise<Response>` from `mobile/src/api/client.ts`, consumed by Tasks 7, 8, 9.

- [ ] **Step 1: Write the auth store**

Create `mobile/src/store/authStore.ts`:

```typescript
import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import { secureStorage } from "./secureStorage";

interface AuthState {
  accessToken: string | null;
  refreshToken: string | null;
  setTokens: (accessToken: string, refreshToken: string) => void;
  clear: () => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      accessToken: null,
      refreshToken: null,
      setTokens: (accessToken, refreshToken) => set({ accessToken, refreshToken }),
      clear: () => set({ accessToken: null, refreshToken: null }),
    }),
    { name: "auth", storage: createJSONStorage(() => secureStorage) },
  ),
);
```

- [ ] **Step 2: Write the API client**

Create `mobile/src/api/client.ts` (same shared-in-flight-refresh shape as the web frontend's `frontend/src/api/client.ts`, but with an explicit base URL instead of a same-origin relative path, and no `FormData` handling - v1 is read-only, no uploads):

```typescript
import { useAuthStore } from "../store/authStore";
import { useServerStore } from "../store/serverStore";

let inFlightRefresh: Promise<boolean> | null = null;

function baseUrl(): string {
  const url = useServerStore.getState().serverUrl;
  if (!url) throw new Error("No server URL configured");
  return url;
}

async function doRefresh(): Promise<boolean> {
  const { refreshToken, setTokens, clear } = useAuthStore.getState();
  if (!refreshToken) return false;

  const res = await fetch(`${baseUrl()}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
  });
  if (!res.ok) {
    clear();
    return false;
  }
  const data = await res.json();
  setTokens(data.access_token, data.refresh_token);
  return true;
}

function refreshTokens(): Promise<boolean> {
  if (!inFlightRefresh) {
    inFlightRefresh = doRefresh().finally(() => {
      inFlightRefresh = null;
    });
  }
  return inFlightRefresh;
}

export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const { accessToken } = useAuthStore.getState();
  const headers = new Headers(init.headers);
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  if (init.body && typeof init.body === "string") headers.set("Content-Type", "application/json");

  const url = `${baseUrl()}${path}`;
  let res = await fetch(url, { ...init, headers });

  if (res.status === 401 && (await refreshTokens())) {
    const retryHeaders = new Headers(init.headers);
    retryHeaders.set("Authorization", `Bearer ${useAuthStore.getState().accessToken}`);
    if (init.body && typeof init.body === "string") retryHeaders.set("Content-Type", "application/json");
    res = await fetch(url, { ...init, headers: retryHeaders });
  }

  return res;
}
```

- [ ] **Step 3: Write the login screen**

Create `mobile/src/screens/LoginScreen.tsx`:

```typescript
import { useState } from "react";
import { Button, StyleSheet, Text, TextInput, View } from "react-native";
import { apiFetch } from "../api/client";
import { useAuthStore } from "../store/authStore";

export function LoginScreen() {
  const setTokens = useAuthStore((s) => s.setTokens);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [totpCode, setTotpCode] = useState("");
  const [mfaRequired, setMfaRequired] = useState(false);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit() {
    setSubmitting(true);
    setError("");
    try {
      const res = await apiFetch("/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password, totp_code: mfaRequired ? totpCode : undefined }),
      });
      if (!res.ok) {
        setError(res.status === 401 ? "Invalid email, password, or code." : "Login failed - try again.");
        return;
      }
      const data = await res.json();
      if (data.mfa_required) {
        setMfaRequired(true);
        return;
      }
      setTokens(data.access_token, data.refresh_token);
    } catch {
      setError("Could not reach the server. Check your connection.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <View style={styles.container}>
      <Text style={styles.label}>Email</Text>
      <TextInput
        style={styles.input}
        autoCapitalize="none"
        autoCorrect={false}
        keyboardType="email-address"
        value={email}
        onChangeText={setEmail}
        editable={!mfaRequired}
      />
      <Text style={styles.label}>Password</Text>
      <TextInput
        style={styles.input}
        secureTextEntry
        value={password}
        onChangeText={setPassword}
        editable={!mfaRequired}
      />
      {mfaRequired ? (
        <>
          <Text style={styles.label}>Authenticator code</Text>
          <TextInput
            style={styles.input}
            keyboardType="number-pad"
            value={totpCode}
            onChangeText={setTotpCode}
          />
        </>
      ) : null}
      {error ? <Text style={styles.error}>{error}</Text> : null}
      <Button title={submitting ? "Signing in..." : "Sign in"} onPress={handleSubmit} disabled={submitting} />
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, justifyContent: "center", padding: 24 },
  label: { fontSize: 14, marginBottom: 4 },
  input: { borderWidth: 1, borderColor: "#ccc", borderRadius: 8, padding: 12, marginBottom: 12 },
  error: { color: "red", marginBottom: 12 },
});
```

- [ ] **Step 4: Branch the root navigator on `accessToken`**

Replace `mobile/src/navigation/RootNavigator.tsx`:

```typescript
import { createNativeStackNavigator } from "@react-navigation/native-stack";
import { StyleSheet, Text, View } from "react-native";
import { useAuthStore } from "../store/authStore";
import { useServerStore } from "../store/serverStore";
import { ServerUrlScreen } from "../screens/ServerUrlScreen";
import { LoginScreen } from "../screens/LoginScreen";

const Stack = createNativeStackNavigator();

function PlaceholderScreen() {
  return (
    <View style={styles.container}>
      <Text>Evowerk Mobile</Text>
    </View>
  );
}

export function RootNavigator() {
  const serverUrl = useServerStore((s) => s.serverUrl);
  const accessToken = useAuthStore((s) => s.accessToken);

  return (
    <Stack.Navigator screenOptions={{ headerShown: false }}>
      {!serverUrl ? (
        <Stack.Screen name="ServerUrl" component={ServerUrlScreen} />
      ) : !accessToken ? (
        <Stack.Screen name="Login" component={LoginScreen} />
      ) : (
        <Stack.Screen name="Placeholder" component={PlaceholderScreen} />
      )}
    </Stack.Navigator>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, alignItems: "center", justifyContent: "center" },
});
```

- [ ] **Step 5: Typecheck**

Run: `cd mobile && npx tsc --noEmit`
Expected: no output, exit code 0.

- [ ] **Step 6: Manual verification**

Prerequisite: a running backend + an org registered on it. Run `cd backend && uvicorn app.main:app --reload`, then register a test org via the web frontend's sign-up page (or `POST /auth/register` with `org_name`, `email`, and a password of your choosing).

Run: `cd mobile && npx expo start`, open in Expo Go on a device/simulator that can reach the backend host (use your machine's LAN IP, not `localhost`, if testing on a physical device).
Expected: enter the server URL (e.g. `http://192.168.1.x:8000`) → Login screen appears → entering the registered email/password signs in and lands on the "Evowerk Mobile" placeholder screen. Entering a wrong password shows "Invalid email, password, or code."

- [ ] **Step 7: Commit**

```bash
cd mobile && git add -A
git commit -m "feat: add login screen and authenticated API client"
```

---

## Task 7: Push token registration

**Files:**
- Create: `mobile/src/store/deviceTokenStore.ts`
- Create: `mobile/src/push/registerPush.ts`
- Modify: `mobile/App.tsx`

**Interfaces:**
- Consumes: `apiFetch` (Task 6); `useAuthStore` (Task 6).
- Produces: `registerForPush(): Promise<string | null>`, `unregisterPush(token: string | null): Promise<void>` from `mobile/src/push/registerPush.ts` — `unregisterPush` consumed by Task 9's sign-out. `useDeviceTokenStore` (`{ token: string | null, setToken(token) }`), consumed by Task 9.

- [ ] **Step 1: Write the device token store**

Create `mobile/src/store/deviceTokenStore.ts`:

```typescript
import { create } from "zustand";

// Not persisted - the spec calls for re-registering on every app foreground
// (Expo push tokens can rotate), so there is no need to remember this
// across app restarts; App.tsx re-derives it on mount.
interface DeviceTokenState {
  token: string | null;
  setToken: (token: string | null) => void;
}

export const useDeviceTokenStore = create<DeviceTokenState>((set) => ({
  token: null,
  setToken: (token) => set({ token }),
}));
```

- [ ] **Step 2: Write the register/unregister functions**

Create `mobile/src/push/registerPush.ts`:

```typescript
import * as Device from "expo-device";
import * as Notifications from "expo-notifications";
import { apiFetch } from "../api/client";

export async function registerForPush(): Promise<string | null> {
  // Push tokens aren't issued to simulators/emulators.
  if (!Device.isDevice) return null;

  const existing = await Notifications.getPermissionsAsync();
  let status = existing.status;
  if (status !== "granted") {
    const requested = await Notifications.requestPermissionsAsync();
    status = requested.status;
  }
  if (status !== "granted") return null;

  const { data: token } = await Notifications.getExpoPushTokenAsync();
  await apiFetch("/device-tokens", {
    method: "POST",
    body: JSON.stringify({ expo_push_token: token }),
  });
  return token;
}

export async function unregisterPush(token: string | null): Promise<void> {
  if (!token) return;
  await apiFetch(`/device-tokens?expo_push_token=${encodeURIComponent(token)}`, { method: "DELETE" });
}
```

- [ ] **Step 3: Wire registration into `App.tsx`**

Replace `mobile/App.tsx`:

```typescript
import { useEffect } from "react";
import { AppState } from "react-native";
import { NavigationContainer } from "@react-navigation/native";
import { SafeAreaProvider } from "react-native-safe-area-context";
import * as Notifications from "expo-notifications";
import { RootNavigator } from "./src/navigation/RootNavigator";
import { useAuthStore } from "./src/store/authStore";
import { useDeviceTokenStore } from "./src/store/deviceTokenStore";
import { registerForPush } from "./src/push/registerPush";

Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: true,
    shouldPlaySound: false,
    shouldSetBadge: false,
  }),
});

export default function App() {
  const accessToken = useAuthStore((s) => s.accessToken);
  const setToken = useDeviceTokenStore((s) => s.setToken);

  useEffect(() => {
    if (!accessToken) return;
    registerForPush().then(setToken);

    const sub = AppState.addEventListener("change", (state) => {
      if (state === "active") registerForPush().then(setToken);
    });
    return () => sub.remove();
  }, [accessToken]);

  return (
    <SafeAreaProvider>
      <NavigationContainer>
        <RootNavigator />
      </NavigationContainer>
    </SafeAreaProvider>
  );
}
```

- [ ] **Step 4: Typecheck**

Run: `cd mobile && npx tsc --noEmit`
Expected: no output, exit code 0.

- [ ] **Step 5: Manual verification**

Run: `cd mobile && npx expo start` on a **physical device** via Expo Go (push tokens require a real device, not a simulator).
Expected: after signing in, an OS permission prompt for notifications appears; accepting it triggers a `POST /device-tokens` call. Confirm via the backend: query the `device_tokens` table (or temporarily log in `register_device_token`) and see one row for the signed-in user.

- [ ] **Step 6: Commit**

```bash
cd mobile && git add -A
git commit -m "feat: register Expo push token on login and app foreground"
```

---

## Task 8: Dashboard screen (KPI cards)

**Files:**
- Create: `mobile/src/api/kpiDashboards.ts`
- Create: `mobile/src/screens/DashboardScreen.tsx`

**Interfaces:**
- Consumes: `apiFetch` (Task 6).
- Produces: `DashboardScreen` component, consumed by `MainTabs` (Task 9).

- [ ] **Step 1: Write the API fetchers**

Create `mobile/src/api/kpiDashboards.ts`:

```typescript
import { apiFetch } from "./client";

export interface KpiDashboard {
  id: string;
  name: string;
  tier: string;
  kpi_ids: string[];
  refresh_seconds: number;
}

export interface KpiCard {
  kpi_id: string;
  name: string;
  unit: string | null;
  direction: string;
  value: number | null;
  eval: { status?: "unknown" | "ok" | "warn" | "breach" } | null;
  error: string | null;
}

export async function fetchDashboards(): Promise<KpiDashboard[]> {
  const res = await apiFetch("/kpi-dashboards");
  if (!res.ok) throw new Error("Failed to load dashboards");
  return res.json();
}

export async function fetchDashboardCards(dashboardId: string): Promise<KpiCard[]> {
  const res = await apiFetch(`/kpi-dashboards/${dashboardId}/cards`);
  if (!res.ok) throw new Error("Failed to load dashboard cards");
  return res.json();
}
```

- [ ] **Step 2: Write the screen**

Create `mobile/src/screens/DashboardScreen.tsx`:

```typescript
import { useCallback, useEffect, useRef, useState } from "react";
import { FlatList, RefreshControl, StyleSheet, Text, View } from "react-native";
import { fetchDashboardCards, fetchDashboards, KpiCard, KpiDashboard } from "../api/kpiDashboards";

const POLL_MS = 60_000;

const STATUS_COLORS: Record<string, string> = {
  ok: "#2e7d32",
  warn: "#f9a825",
  breach: "#c62828",
  unknown: "#9e9e9e",
};

export function DashboardScreen() {
  const [dashboard, setDashboard] = useState<KpiDashboard | null>(null);
  const [cards, setCards] = useState<KpiCard[]>([]);
  const [error, setError] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  // load() reads the current dashboard via a ref, not the `dashboard` state
  // captured at effect-setup time, so the 60s poll interval always targets
  // whichever dashboard was resolved on the first load, not a stale null.
  const dashboardRef = useRef<KpiDashboard | null>(null);

  const load = useCallback(async () => {
    try {
      let current = dashboardRef.current;
      if (!current) {
        const dashboards = await fetchDashboards();
        current = dashboards.find((d) => d.tier === "supervisor") ?? dashboards[0] ?? null;
        dashboardRef.current = current;
        setDashboard(current);
      }
      if (!current) {
        setError("No dashboards configured for this org yet.");
        return;
      }
      const nextCards = await fetchDashboardCards(current.id);
      setCards(nextCards);
      setError("");
    } catch {
      setError("Could not load dashboard - pull to retry.");
    }
  }, []);

  useEffect(() => {
    load();
    const interval = setInterval(load, POLL_MS);
    return () => clearInterval(interval);
  }, [load]);

  async function onRefresh() {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  }

  return (
    <FlatList
      style={styles.container}
      data={cards}
      keyExtractor={(item) => item.kpi_id}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
      ListHeaderComponent={error ? <Text style={styles.error}>{error}</Text> : null}
      renderItem={({ item }) => (
        <View style={[styles.card, { borderLeftColor: STATUS_COLORS[item.eval?.status ?? "unknown"] }]}>
          <Text style={styles.cardName}>{item.name}</Text>
          <Text style={styles.cardValue}>
            {item.value !== null ? item.value.toFixed(1) : "-"} {item.unit ?? ""}
          </Text>
          {item.error ? <Text style={styles.error}>{item.error}</Text> : null}
        </View>
      )}
    />
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, padding: 12 },
  card: { borderLeftWidth: 6, borderRadius: 8, backgroundColor: "#f5f5f5", padding: 16, marginBottom: 12 },
  cardName: { fontSize: 14, color: "#555" },
  cardValue: { fontSize: 28, fontWeight: "700", marginTop: 4 },
  error: { color: "red", padding: 8 },
});
```

- [ ] **Step 3: Typecheck**

Run: `cd mobile && npx tsc --noEmit`
Expected: no output, exit code 0. (`DashboardScreen` isn't wired into navigation until Task 9 — this step only confirms it compiles.)

- [ ] **Step 4: Commit**

```bash
cd mobile && git add -A
git commit -m "feat: add dashboard screen with KPI cards"
```

---

## Task 9: Alerts screen, tab navigation, push tap deep link, sign out

**Files:**
- Create: `mobile/src/api/alerts.ts`
- Create: `mobile/src/screens/AlertsScreen.tsx`
- Create: `mobile/src/navigation/MainTabs.tsx`
- Create: `mobile/src/navigation/navigationRef.ts`
- Modify: `mobile/src/navigation/RootNavigator.tsx`
- Modify: `mobile/src/screens/DashboardScreen.tsx`
- Modify: `mobile/App.tsx`

**Interfaces:**
- Consumes: `apiFetch` (Task 6); `DashboardScreen` (Task 8); `unregisterPush`, `useDeviceTokenStore` (Task 7); `useAuthStore` (Task 6).
- Produces: `MainTabs` component (replaces `RootNavigator`'s placeholder screen); `navigationRef`, consumed by `App.tsx`'s notification-tap handler.

- [ ] **Step 1: Write the alerts API fetchers**

Create `mobile/src/api/alerts.ts`:

```typescript
import { apiFetch } from "./client";

export interface AlertTrigger {
  rule_id: string;
  rule_name: string;
  triggered: boolean;
  message: string;
  severity: string;
}

export async function scanAlerts(): Promise<AlertTrigger[]> {
  const res = await apiFetch("/alerts/scan", { method: "POST" });
  if (!res.ok) throw new Error("Failed to scan alerts");
  const data = await res.json();
  return data.results;
}
```

- [ ] **Step 2: Write the alerts screen**

Create `mobile/src/screens/AlertsScreen.tsx`:

```typescript
import { useCallback, useEffect, useState } from "react";
import { FlatList, RefreshControl, StyleSheet, Text, View } from "react-native";
import { AlertTrigger, scanAlerts } from "../api/alerts";

const POLL_MS = 60_000;

export function AlertsScreen() {
  const [triggers, setTriggers] = useState<AlertTrigger[]>([]);
  const [error, setError] = useState("");
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      const results = await scanAlerts();
      setTriggers(results);
      setError("");
    } catch {
      setError("Could not load alerts - pull to retry.");
    }
  }, []);

  useEffect(() => {
    load();
    const interval = setInterval(load, POLL_MS);
    return () => clearInterval(interval);
  }, [load]);

  async function onRefresh() {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  }

  return (
    <FlatList
      style={styles.container}
      data={triggers}
      keyExtractor={(item) => item.rule_id}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
      ListHeaderComponent={error ? <Text style={styles.error}>{error}</Text> : null}
      renderItem={({ item }) => (
        <View style={[styles.row, item.triggered && styles.rowTriggered]}>
          <Text style={styles.name}>{item.rule_name}</Text>
          <Text style={styles.message}>{item.message}</Text>
        </View>
      )}
    />
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, padding: 12 },
  row: { padding: 16, borderRadius: 8, backgroundColor: "#f5f5f5", marginBottom: 8 },
  rowTriggered: { backgroundColor: "#fdecea" },
  name: { fontWeight: "600" },
  message: { color: "#555", marginTop: 4 },
  error: { color: "red", padding: 8 },
});
```

- [ ] **Step 3: Add sign-out to the dashboard screen**

In `mobile/src/screens/DashboardScreen.tsx`, change the existing `react-native` import line from:

```typescript
import { FlatList, RefreshControl, StyleSheet, Text, View } from "react-native";
```

to:

```typescript
import { Button, FlatList, RefreshControl, StyleSheet, Text, View } from "react-native";
```

and add these three new imports directly below it:

```typescript
import { unregisterPush } from "../push/registerPush";
import { useAuthStore } from "../store/authStore";
import { useDeviceTokenStore } from "../store/deviceTokenStore";
```

Add this component above the `DashboardScreen` export:

```typescript
function SignOutButton() {
  const clear = useAuthStore((s) => s.clear);
  const token = useDeviceTokenStore((s) => s.token);

  async function handleSignOut() {
    await unregisterPush(token);
    clear();
  }

  return <Button title="Sign out" onPress={handleSignOut} />;
}
```

Add `ListFooterComponent={<SignOutButton />}` as a prop on the `<FlatList>` in `DashboardScreen`, alongside the existing `ListHeaderComponent` prop.

- [ ] **Step 4: Write the tab navigator**

Create `mobile/src/navigation/MainTabs.tsx`:

```typescript
import { createBottomTabNavigator } from "@react-navigation/bottom-tabs";
import { DashboardScreen } from "../screens/DashboardScreen";
import { AlertsScreen } from "../screens/AlertsScreen";

const Tab = createBottomTabNavigator();

export function MainTabs() {
  return (
    <Tab.Navigator>
      <Tab.Screen name="Dashboard" component={DashboardScreen} />
      <Tab.Screen name="Alerts" component={AlertsScreen} />
    </Tab.Navigator>
  );
}
```

- [ ] **Step 5: Write the navigation ref (for navigating from outside a component, e.g. a push-tap handler)**

Create `mobile/src/navigation/navigationRef.ts`:

```typescript
import { createNavigationContainerRef } from "@react-navigation/native";

export const navigationRef = createNavigationContainerRef();
```

- [ ] **Step 6: Wire `MainTabs` into the root navigator**

Replace `mobile/src/navigation/RootNavigator.tsx`:

```typescript
import { createNativeStackNavigator } from "@react-navigation/native-stack";
import { useAuthStore } from "../store/authStore";
import { useServerStore } from "../store/serverStore";
import { ServerUrlScreen } from "../screens/ServerUrlScreen";
import { LoginScreen } from "../screens/LoginScreen";
import { MainTabs } from "./MainTabs";

const Stack = createNativeStackNavigator();

export function RootNavigator() {
  const serverUrl = useServerStore((s) => s.serverUrl);
  const accessToken = useAuthStore((s) => s.accessToken);

  return (
    <Stack.Navigator screenOptions={{ headerShown: false }}>
      {!serverUrl ? (
        <Stack.Screen name="ServerUrl" component={ServerUrlScreen} />
      ) : !accessToken ? (
        <Stack.Screen name="Login" component={LoginScreen} />
      ) : (
        <Stack.Screen name="Main" component={MainTabs} />
      )}
    </Stack.Navigator>
  );
}
```

- [ ] **Step 7: Wire the navigation ref and push-tap handler into `App.tsx`**

Replace `mobile/App.tsx`:

```typescript
import { useEffect } from "react";
import { AppState } from "react-native";
import { NavigationContainer } from "@react-navigation/native";
import { SafeAreaProvider } from "react-native-safe-area-context";
import * as Notifications from "expo-notifications";
import { RootNavigator } from "./src/navigation/RootNavigator";
import { navigationRef } from "./src/navigation/navigationRef";
import { useAuthStore } from "./src/store/authStore";
import { useDeviceTokenStore } from "./src/store/deviceTokenStore";
import { registerForPush } from "./src/push/registerPush";

Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: true,
    shouldPlaySound: false,
    shouldSetBadge: false,
  }),
});

export default function App() {
  const accessToken = useAuthStore((s) => s.accessToken);
  const setToken = useDeviceTokenStore((s) => s.setToken);

  useEffect(() => {
    if (!accessToken) return;
    registerForPush().then(setToken);

    const sub = AppState.addEventListener("change", (state) => {
      if (state === "active") registerForPush().then(setToken);
    });
    return () => sub.remove();
  }, [accessToken]);

  useEffect(() => {
    // Tapping a push notification deep-links to Alerts - the notification
    // itself carries no message content (see backend/app/services/alerts.py
    // _send_push), so this just navigates; AlertsScreen fetches the real
    // state on mount.
    const sub = Notifications.addNotificationResponseReceivedListener(() => {
      if (navigationRef.isReady()) navigationRef.navigate("Main" as never, { screen: "Alerts" } as never);
    });
    return () => sub.remove();
  }, []);

  return (
    <SafeAreaProvider>
      <NavigationContainer ref={navigationRef}>
        <RootNavigator />
      </NavigationContainer>
    </SafeAreaProvider>
  );
}
```

- [ ] **Step 8: Typecheck**

Run: `cd mobile && npx tsc --noEmit`
Expected: no output, exit code 0.

- [ ] **Step 9: Manual verification**

Run: `cd mobile && npx expo start`, sign in on a physical device (per Task 7's push prerequisites).
Expected: two tabs, "Dashboard" and "Alerts", both visible after login. Dashboard shows KPI cards (requires an org with at least one `KpiDashboard` already configured via the web app - see spec's "Why reuse the KPI dashboard system"; if none exists, expect the "No dashboards configured for this org yet." message instead, which is correct behavior). Alerts shows the org's alert rules with live triggered/ok state. Pressing "Sign out" on Dashboard returns to the Login screen. Trigger a real alert (e.g. via the web app's rule Test button against a breaching dataset) and confirm a push notification arrives on the device; tapping it opens the app to the Alerts tab.

- [ ] **Step 10: Commit**

```bash
cd mobile && git add -A
git commit -m "feat: add alerts screen, tab navigation, and push-tap deep link"
```

---

## Task 10: Smoke test + README

**Files:**
- Create: `mobile/App.test.tsx`
- Create: `mobile/README.md`
- Modify: `mobile/package.json`

**Interfaces:**
- Consumes: `ServerUrlScreen` (Task 5) — this is the app's actual first screen (server URL, entered before login), so the smoke test renders it rather than the login screen the original spec text named before this task's design added the server-URL step.

- [ ] **Step 1: Install the test dependencies**

```bash
cd mobile
npm install --save-dev jest-expo jest @testing-library/react-native @types/jest
```

- [ ] **Step 2: Configure the test script**

In `mobile/package.json`, add a `"test"` script and a `"jest"` config block (as siblings of the existing `"scripts"`/other top-level keys):

```json
  "scripts": {
    "start": "expo start",
    "android": "expo start --android",
    "ios": "expo start --ios",
    "web": "expo start --web",
    "test": "jest"
  },
  "jest": {
    "preset": "jest-expo"
  },
```

- [ ] **Step 3: Write the failing test**

Create `mobile/App.test.tsx`:

```typescript
import { render, screen } from "@testing-library/react-native";
import { ServerUrlScreen } from "./src/screens/ServerUrlScreen";

test("renders the server URL connect screen", () => {
  render(<ServerUrlScreen />);
  expect(screen.getByText("Your Evowerk server URL")).toBeTruthy();
});
```

This shouldn't actually fail at this point since `ServerUrlScreen` already exists (Task 5) - this step is "write the test", not "watch it fail for a missing feature". Continue to Step 4 to confirm it runs.

- [ ] **Step 4: Run the test**

Run: `cd mobile && npm test`
Expected: PASS (1 test).

- [ ] **Step 5: Write the README**

Create `mobile/README.md`:

```markdown
# Evowerk Mobile (exec dashboard)

Read-only Expo/React Native app: KPI dashboard cards, alert status, and
push notifications when an alert rule triggers. Talks directly to a
customer's own self-hosted Evowerk deployment - there is no Evowerk-hosted
backend for it to call. See
`docs/superpowers/specs/2026-07-24-mobile-exec-dashboard-design.md` for the
full design.

## Run locally

    npm install
    npx expo start

Scan the QR code with Expo Go (iOS/Android), or press `i` / `a` in the
terminal for a simulator/emulator. Push notifications require a physical
device - simulators don't receive them.

On first launch, enter your org's deployment URL (e.g.
`https://mrp.yourcompany.com`, or your machine's LAN IP + port if testing
against a local backend), then sign in with your normal Evowerk
credentials.

## Test

    npx tsc --noEmit
    npm test
```

- [ ] **Step 6: Commit**

```bash
cd mobile && git add -A
git commit -m "test: add smoke test and README"
```

---

## Final verification

- [ ] Backend: `cd backend && python -m pytest -q` — full suite passes.
- [ ] Mobile: `cd mobile && npx tsc --noEmit && npm test` — both pass.
- [ ] Manual: fresh install walkthrough on a physical device — server URL → login → Dashboard cards render → Alerts shows rule status → trigger a real alert and confirm the push arrives and tapping it opens Alerts → Sign out returns to Login.
