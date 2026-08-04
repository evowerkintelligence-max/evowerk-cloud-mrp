# Scheduled Cloud-DB Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the in-app Redpanda/Flink live-streaming pipeline with an APScheduler-driven periodic pull sync against a customer-owned Azure/AWS database, while preserving OEE metrics, CBM sensor/anomaly scoring, and AutoML live scoring/drift-retrain on the new batch cadence.

**Architecture:** One APScheduler job per `SyncSchedule` wakes on an interval, calls the target connector's `fetch_since(cursor_column, cursor_value)` to pull only new rows, computes OEE metrics + CBM sensor scoring + AutoML scoring/drift inline in the same job, and persists everything to Postgres (`SyncTick`/`SyncRow`). The frontend polls REST instead of consuming SSE.

**Tech Stack:** FastAPI, SQLAlchemy (async ORM + sync connector engines via `asyncio.to_thread`), Alembic, APScheduler (`AsyncIOScheduler`), pytest/pytest-asyncio, React/TanStack Query.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-04-scheduled-cloud-sync-design.md` — every task below implements one of its sections.
- No new infrastructure services. No Kafka/Redpanda/Flink anywhere in the result.
- Every DB-query connector (`sqlserver`, `oracle`, `postgres`, `mysql`) must implement `fetch_since(cursor_column, cursor_value, max_rows) -> tuple[list[dict], str | None]`. Connector types without it are excluded from scheduled-sync creation.
- `interval_seconds` minimum enforced at 60 (a scheduler cadence, not a poll-loop cadence).
- All new/changed backend code follows existing patterns in the file it touches (blocking I/O via `asyncio.to_thread`, `# nosec B608` comments on admin-configured SQL string building, async SQLAlchemy elsewhere).
- Every task ends green: relevant tests pass before moving to the next task.

---

### Task 1: Relocate pure metrics calculation out of `backend/streaming/`

**Files:**
- Create: `backend/app/services/metrics_calc.py`
- Create: `backend/tests/test_metrics_calc.py`
- Delete (end of task, after the new test passes): `backend/streaming/metrics.py`, `backend/streaming/test_metrics.py`

**Interfaces:**
- Produces: `compute_window_metrics(rows: list[dict[str, Any]], column_map: dict[str, str]) -> dict[str, Any]` — used by Task 6's `sync_scheduler._run_sync`.

- [ ] **Step 1: Copy the existing test file to its new location, updating the import**

Read `backend/streaming/test_metrics.py` first to get its exact current test bodies, then write `backend/tests/test_metrics_calc.py` with the same test functions, changing only:
```python
from app.services.metrics_calc import compute_window_metrics
```
(replacing whatever import path the original file used).

- [ ] **Step 2: Run the new test file to verify it fails (module doesn't exist yet)**

Run: `cd backend && pytest tests/test_metrics_calc.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.metrics_calc'`

- [ ] **Step 3: Create `app/services/metrics_calc.py` with the relocated function**

```python
"""Pure metric-computation formulas for scheduled-sync batches. Relocated
unchanged from backend/streaming/metrics.py (the old Flink job's shared
formula module) - see docs/superpowers/specs/2026-08-04-scheduled-cloud-sync-design.md.
No behavior change: same formulas, now called once per scheduled sync batch
(app.services.sync_scheduler) instead of once per Flink 5s tumbling window."""
from __future__ import annotations

from typing import Any


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def compute_window_metrics(rows: list[dict[str, Any]], column_map: dict[str, str]) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    cm = column_map

    uc = cm.get("units_col")
    if uc:
        vals = [_safe_float(r.get(uc, 0)) for r in rows]
        total_units = sum(vals)
        metrics["throughput"] = round(total_units, 1)

    sc = cm.get("scrap_col")
    tuc = cm.get("total_units_col") or uc
    if sc and tuc:
        total = sum(_safe_float(r.get(tuc, 0)) for r in rows)
        scrap = sum(_safe_float(r.get(sc, 0)) for r in rows)
        if total > 0:
            scrap_pct = round(scrap / total * 100, 2)
            metrics["scrap_rate_pct"] = scrap_pct
            metrics["fpy_pct"] = round(100 - scrap_pct, 2)

    dc = cm.get("downtime_col")
    if dc:
        metrics["total_downtime_min"] = round(sum(_safe_float(r.get(dc, 0)) for r in rows), 1)

    pt = cm.get("planned_time_col")
    ot = cm.get("operating_time_col")
    guc = cm.get("good_units_col")
    tpc = cm.get("target_rate_col")
    if pt and ot and guc and uc:
        tp_total = sum(_safe_float(r.get(pt, 0)) for r in rows)
        op_total = sum(_safe_float(r.get(ot, 0)) for r in rows)
        u_total = sum(_safe_float(r.get(uc, 0)) for r in rows)
        g_total = sum(_safe_float(r.get(guc, 0)) for r in rows)
        avail = (op_total / tp_total * 100) if tp_total > 0 else 0.0
        quality = (g_total / u_total * 100) if u_total > 0 else 0.0
        perf = 100.0
        if tpc:
            target_total = sum(_safe_float(r.get(tpc, 0)) for r in rows)
            if target_total > 0:
                perf = u_total / target_total * 100
        oee = avail * (perf / 100) * (quality / 100)
        metrics["availability_pct"] = round(avail, 1)
        metrics["quality_pct"] = round(quality, 1)
        metrics["performance_pct"] = round(perf, 1)
        metrics["oee_pct"] = round(oee, 1)

    if not uc:
        metrics.setdefault("throughput", 0.0)

    return metrics
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && pytest tests/test_metrics_calc.py -v`
Expected: PASS

- [ ] **Step 5: Delete the old streaming copies and commit**

```bash
rm backend/streaming/metrics.py backend/streaming/test_metrics.py
git add backend/app/services/metrics_calc.py backend/tests/test_metrics_calc.py backend/streaming/metrics.py backend/streaming/test_metrics.py
git commit -m "refactor: relocate compute_window_metrics out of backend/streaming"
```

---

### Task 2: Incremental `fetch_since` on the existing SQL connectors

**Files:**
- Modify: `backend/app/services/connectors/sqlserver_connector.py`
- Modify: `backend/app/services/connectors/oracle_connector.py`
- Test: `backend/tests/test_connectors_fetch_since.py` (new)

**Interfaces:**
- Produces: `SqlServerConnector.fetch_since(cursor_column: str, cursor_value: str | None, max_rows: int = 500_000) -> tuple[list[dict[str, Any]], str | None]`, same signature on `OracleConnector`. Returned tuple is `(rows, new_cursor_value)`; `new_cursor_value` is `cursor_value` unchanged if `rows` is empty.
- Consumes: nothing new — reuses each connector's existing `self.query`/`self._engine()`.

These need a live SQL Server/Oracle instance to integration-test for real. This task tests the **query-building logic only** (given a fake engine/connection double) so the suite stays fast and infra-free; the connectors' `test()`/`preview()`/`fetch_all()` methods already have no such test today either — same posture, not a new gap.

- [ ] **Step 1: Write the failing tests (query construction, not live execution)**

```python
# backend/tests/test_connectors_fetch_since.py
from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.services.connectors.sqlserver_connector import SqlServerConnector
from app.services.connectors.oracle_connector import OracleConnector


def _fake_engine(rows: list[dict]):
    """A MagicMock standing in for a SQLAlchemy Engine/Connection whose
    execute() returns `rows` - lets fetch_since be tested without a real DB."""
    keys = list(rows[0].keys()) if rows else []
    result = MagicMock()
    result.keys.return_value = keys
    result.fetchall.return_value = [tuple(r[k] for k in keys) for r in rows]

    conn = MagicMock()
    conn.execute.return_value = result
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False

    engine = MagicMock()
    engine.connect.return_value = conn
    return engine, conn


def test_sqlserver_fetch_since_first_run_has_no_cursor_filter():
    connector = SqlServerConnector({
        "host": "h", "database": "d", "username": "u", "password": "p",
        "query": "SELECT * FROM ticks",
    })
    engine, conn = _fake_engine([{"ts": "2026-08-01T00:00:00", "units": 5}])
    with patch.object(connector, "_engine", return_value=engine), \
         patch.object(connector, "_check_deps"):
        rows, new_cursor = connector.fetch_since("ts", None, max_rows=100)

    sql_text = str(conn.execute.call_args[0][0])
    assert "WHERE" not in sql_text.upper()
    assert "ORDER BY ts" in sql_text
    assert rows == [{"ts": "2026-08-01T00:00:00", "units": 5}]
    assert new_cursor == "2026-08-01T00:00:00"


def test_sqlserver_fetch_since_subsequent_run_filters_by_cursor():
    connector = SqlServerConnector({
        "host": "h", "database": "d", "username": "u", "password": "p",
        "query": "SELECT * FROM ticks",
    })
    engine, conn = _fake_engine([{"ts": "2026-08-01T00:05:00", "units": 3}])
    with patch.object(connector, "_engine", return_value=engine), \
         patch.object(connector, "_check_deps"):
        rows, new_cursor = connector.fetch_since("ts", "2026-08-01T00:00:00", max_rows=100)

    sql_text = str(conn.execute.call_args[0][0])
    params = conn.execute.call_args[0][1]
    assert "WHERE ts > :cursor_value" in sql_text
    assert params == {"cursor_value": "2026-08-01T00:00:00"}
    assert new_cursor == "2026-08-01T00:05:00"


def test_sqlserver_fetch_since_no_new_rows_keeps_old_cursor():
    connector = SqlServerConnector({
        "host": "h", "database": "d", "username": "u", "password": "p",
        "query": "SELECT * FROM ticks",
    })
    engine, conn = _fake_engine([])
    with patch.object(connector, "_engine", return_value=engine), \
         patch.object(connector, "_check_deps"):
        rows, new_cursor = connector.fetch_since("ts", "2026-08-01T00:00:00", max_rows=100)

    assert rows == []
    assert new_cursor == "2026-08-01T00:00:00"


def test_oracle_fetch_since_subsequent_run_filters_by_cursor():
    connector = OracleConnector({
        "host": "h", "service_name": "svc", "username": "u", "password": "p",
        "query": "SELECT * FROM ticks",
    })
    engine, conn = _fake_engine([{"ts": "2026-08-01T00:05:00", "units": 3}])
    with patch.object(connector, "_engine", return_value=engine), \
         patch.object(connector, "_check_deps"):
        rows, new_cursor = connector.fetch_since("ts", "2026-08-01T00:00:00", max_rows=100)

    sql_text = str(conn.execute.call_args[0][0])
    assert "WHERE ts > :cursor_value" in sql_text
    assert "ROWNUM" in sql_text
    assert new_cursor == "2026-08-01T00:05:00"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_connectors_fetch_since.py -v`
Expected: FAIL with `AttributeError: 'SqlServerConnector' object has no attribute 'fetch_since'`

- [ ] **Step 3: Add `fetch_since` to `SqlServerConnector`**

In `backend/app/services/connectors/sqlserver_connector.py`, add after `fetch_all`:

```python
    def fetch_since(
        self, cursor_column: str, cursor_value: str | None, max_rows: int = 500_000
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Incremental pull: only rows newer than cursor_value, ordered by
        cursor_column ascending so the last row is the new cursor. First run
        (cursor_value=None) pulls everything up to max_rows."""
        eng = self._engine()
        try:
            base = f"SELECT * FROM ({self.query}) AS _src"  # nosec B608 - self.query is admin-configured, same trust note as preview()
            if cursor_value is not None:
                wrapped = (
                    f"SELECT TOP {max_rows} * FROM ({base}) AS _cursor "
                    f"WHERE {cursor_column} > :cursor_value ORDER BY {cursor_column}"
                )  # nosec B608 - cursor_column is admin-picked from preview() output, not request-time user input
                params: dict[str, Any] = {"cursor_value": cursor_value}
            else:
                wrapped = f"SELECT TOP {max_rows} * FROM ({base}) AS _cursor ORDER BY {cursor_column}"  # nosec B608
                params = {}
            with eng.connect() as conn:
                result = conn.execute(text(wrapped), params)
                cols = list(result.keys())
                rows = [dict(zip(cols, row)) for row in result.fetchall()]
        finally:
            eng.dispose()

        new_cursor = str(rows[-1][cursor_column]) if rows else cursor_value
        return rows, new_cursor
```

- [ ] **Step 4: Add `fetch_since` to `OracleConnector`**

In `backend/app/services/connectors/oracle_connector.py`, add after `fetch_all`:

```python
    def fetch_since(
        self, cursor_column: str, cursor_value: str | None, max_rows: int = 500_000
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Same contract as SqlServerConnector.fetch_since - Oracle has no TOP,
        so the row cap is a ROWNUM filter on the outer query instead."""
        eng = self._engine()
        try:
            base = f"SELECT * FROM ({self.query})"
            if cursor_value is not None:
                inner = f"SELECT * FROM ({base}) WHERE {cursor_column} > :cursor_value ORDER BY {cursor_column}"  # nosec B608
                wrapped = f"SELECT * FROM ({inner}) WHERE ROWNUM <= {max_rows}"  # nosec B608
                params: dict[str, Any] = {"cursor_value": cursor_value}
            else:
                inner = f"SELECT * FROM ({base}) ORDER BY {cursor_column}"  # nosec B608
                wrapped = f"SELECT * FROM ({inner}) WHERE ROWNUM <= {max_rows}"  # nosec B608
                params = {}
            with eng.connect() as conn:
                result = conn.execute(text(wrapped), params)
                cols = list(result.keys())
                rows = [dict(zip(cols, row)) for row in result.fetchall()]
        finally:
            eng.dispose()

        new_cursor = str(rows[-1][cursor_column]) if rows else cursor_value
        return rows, new_cursor
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_connectors_fetch_since.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/connectors/sqlserver_connector.py backend/app/services/connectors/oracle_connector.py backend/tests/test_connectors_fetch_since.py
git commit -m "feat: add cursor-based incremental fetch to SQL Server and Oracle connectors"
```

---

### Task 3: Postgres connector

**Files:**
- Create: `backend/app/services/connectors/postgres_connector.py`
- Modify: `backend/app/services/connectors/__init__.py`
- Modify: `backend/tests/test_connectors_fetch_since.py` (add Postgres cases)
- Modify: `backend/requirements.txt` (add `psycopg2-binary`)

**Interfaces:**
- Produces: `PostgresConnector` with `test()`/`preview(n)`/`fetch_all(max_rows)`/`iter_rows(max_rows)`/`fetch_since(cursor_column, cursor_value, max_rows)`, same shapes as `SqlServerConnector`. Registered under connector type `"postgres"`.

- [ ] **Step 1: Add the failing tests to `test_connectors_fetch_since.py`**

Append to `backend/tests/test_connectors_fetch_since.py`:

```python
from app.services.connectors.postgres_connector import PostgresConnector


def test_postgres_fetch_since_subsequent_run_filters_by_cursor():
    connector = PostgresConnector({
        "host": "h", "database": "d", "username": "u", "password": "p",
        "query": "SELECT * FROM ticks",
    })
    engine, conn = _fake_engine([{"ts": "2026-08-01T00:05:00", "units": 3}])
    with patch.object(connector, "_engine", return_value=engine), \
         patch.object(connector, "_check_deps"):
        rows, new_cursor = connector.fetch_since("ts", "2026-08-01T00:00:00", max_rows=100)

    sql_text = str(conn.execute.call_args[0][0])
    params = conn.execute.call_args[0][1]
    assert "WHERE ts > :cursor_value" in sql_text
    assert "LIMIT 100" in sql_text
    assert params == {"cursor_value": "2026-08-01T00:00:00"}
    assert new_cursor == "2026-08-01T00:05:00"


def test_postgres_connector_requires_database_and_query():
    import pytest
    with pytest.raises(ValueError):
        PostgresConnector({"host": "h"})
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && pytest tests/test_connectors_fetch_since.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.connectors.postgres_connector'`

- [ ] **Step 3: Create `postgres_connector.py`**

```python
"""Query Postgres (including Azure Database for PostgreSQL / AWS RDS or
Aurora Postgres) via SQLAlchemy + psycopg2. Only reachable if the customer's
instance is network-accessible from this server - same model as
sqlserver_connector.py."""
from __future__ import annotations
from typing import Any

try:
    from sqlalchemy import create_engine, text
    _HAS_SA = True
except ImportError:
    _HAS_SA = False

try:
    import psycopg2  # noqa: F401
    _HAS_PSYCOPG2 = True
except ImportError:
    _HAS_PSYCOPG2 = False


class PostgresConnector:
    def __init__(self, config: dict[str, Any]):
        self.host = str(config.get("host", "127.0.0.1")).strip()
        self.port = int(config.get("port", 5432))
        self.database = str(config.get("database", "")).strip()
        self.username = str(config.get("username", "")).strip()
        self.password = str(config.get("password", "")).strip()
        self.query = str(config.get("query", "")).strip()
        self.sslmode = str(config.get("sslmode", "require")).strip()

        if not self.database:
            raise ValueError("database is required for Postgres connector")
        if not self.query:
            raise ValueError("query is required for Postgres connector")

    def _check_deps(self):
        if not _HAS_SA:
            raise RuntimeError("sqlalchemy is required. Install with: pip install sqlalchemy")
        if not _HAS_PSYCOPG2:
            raise RuntimeError("psycopg2 is required. Install with: pip install psycopg2-binary")

    def _engine(self):
        self._check_deps()
        from urllib.parse import quote_plus
        user = quote_plus(self.username)
        pwd = quote_plus(self.password)
        url = (
            f"postgresql+psycopg2://{user}:{pwd}@{self.host}:{self.port}/{self.database}"
            f"?sslmode={self.sslmode}"
        )
        return create_engine(url, pool_pre_ping=True, pool_size=1, max_overflow=0)

    def test(self) -> dict[str, Any]:
        eng = self._engine()
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        eng.dispose()
        return {"ok": True, "message": f"Connected to {self.host}/{self.database}"}

    def preview(self, n: int = 10) -> list[dict[str, Any]]:
        eng = self._engine()
        limited = f"SELECT * FROM ({self.query}) AS _preview LIMIT {n}"  # nosec B608 - self.query is admin-configured connector config, not per-request user input
        with eng.connect() as conn:
            result = conn.execute(text(limited))
            cols = list(result.keys())
            rows = [dict(zip(cols, row)) for row in result.fetchall()]
        eng.dispose()
        return rows

    def iter_rows(self, max_rows: int = 500_000):
        eng = self._engine()
        try:
            with eng.connect() as conn:
                result = conn.execution_options(
                    stream_results=True, max_row_buffer=10_000
                ).execute(text(self.query))
                cols = list(result.keys())
                n = 0
                for row in result:
                    yield dict(zip(cols, row))
                    n += 1
                    if max_rows and n >= max_rows:
                        break
        finally:
            eng.dispose()

    def fetch_all(self, max_rows: int = 500_000) -> list[dict[str, Any]]:
        return list(self.iter_rows(max_rows=max_rows))

    def fetch_since(
        self, cursor_column: str, cursor_value: str | None, max_rows: int = 500_000
    ) -> tuple[list[dict[str, Any]], str | None]:
        eng = self._engine()
        try:
            base = f"SELECT * FROM ({self.query}) AS _src"  # nosec B608
            if cursor_value is not None:
                wrapped = (
                    f"SELECT * FROM ({base}) AS _cursor WHERE {cursor_column} > :cursor_value "
                    f"ORDER BY {cursor_column} LIMIT {max_rows}"
                )  # nosec B608 - cursor_column is admin-picked from preview() output, not request-time user input
                params: dict[str, Any] = {"cursor_value": cursor_value}
            else:
                wrapped = f"SELECT * FROM ({base}) AS _cursor ORDER BY {cursor_column} LIMIT {max_rows}"  # nosec B608
                params = {}
            with eng.connect() as conn:
                result = conn.execute(text(wrapped), params)
                cols = list(result.keys())
                rows = [dict(zip(cols, row)) for row in result.fetchall()]
        finally:
            eng.dispose()

        new_cursor = str(rows[-1][cursor_column]) if rows else cursor_value
        return rows, new_cursor
```

- [ ] **Step 4: Register the `postgres` connector type**

In `backend/app/services/connectors/__init__.py`, add `"postgres"` to `VALID_TYPES` and a branch in `get_connector`:

```python
VALID_TYPES = {
    "csv", "excel",
    "sqlserver", "oracle", "postgres",
    "sap_odata", "erp_rest", "mes_rest",
    "opcua", "mqtt",
    "opcda", "pi_webapi",
}
```

```python
    if t == "postgres":
        from app.services.connectors.postgres_connector import PostgresConnector
        return PostgresConnector(config)
```
(insert this branch right after the existing `if t == "oracle":` branch)

- [ ] **Step 5: Add the Postgres driver to requirements**

In `backend/requirements.txt`, in the "Data integration connectors" block (near `pyodbc`/`oracledb`), add:
```
psycopg2-binary>=2.9.9  # Postgres (Azure Database for PostgreSQL / AWS RDS/Aurora Postgres)
```

- [ ] **Step 6: Install and run tests to verify they pass**

Run: `cd backend && pip install psycopg2-binary && pytest tests/test_connectors_fetch_since.py -v`
Expected: PASS (all tests including the 2 new Postgres ones)

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/connectors/postgres_connector.py backend/app/services/connectors/__init__.py backend/tests/test_connectors_fetch_since.py backend/requirements.txt
git commit -m "feat: add Postgres connector with incremental fetch"
```

---

### Task 4: MySQL connector

**Files:**
- Create: `backend/app/services/connectors/mysql_connector.py`
- Modify: `backend/app/services/connectors/__init__.py`
- Modify: `backend/tests/test_connectors_fetch_since.py` (add MySQL cases)
- Modify: `backend/requirements.txt` (add `pymysql`)

**Interfaces:**
- Produces: `MysqlConnector`, same method shapes as `PostgresConnector`. Registered under connector type `"mysql"`.

- [ ] **Step 1: Add the failing tests**

Append to `backend/tests/test_connectors_fetch_since.py`:

```python
from app.services.connectors.mysql_connector import MysqlConnector


def test_mysql_fetch_since_subsequent_run_filters_by_cursor():
    connector = MysqlConnector({
        "host": "h", "database": "d", "username": "u", "password": "p",
        "query": "SELECT * FROM ticks",
    })
    engine, conn = _fake_engine([{"ts": "2026-08-01T00:05:00", "units": 3}])
    with patch.object(connector, "_engine", return_value=engine), \
         patch.object(connector, "_check_deps"):
        rows, new_cursor = connector.fetch_since("ts", "2026-08-01T00:00:00", max_rows=100)

    sql_text = str(conn.execute.call_args[0][0])
    params = conn.execute.call_args[0][1]
    assert "WHERE ts > :cursor_value" in sql_text
    assert "LIMIT 100" in sql_text
    assert params == {"cursor_value": "2026-08-01T00:00:00"}
    assert new_cursor == "2026-08-01T00:05:00"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && pytest tests/test_connectors_fetch_since.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.connectors.mysql_connector'`

- [ ] **Step 3: Create `mysql_connector.py`**

```python
"""Query MySQL (including Azure Database for MySQL / AWS RDS or Aurora
MySQL) via SQLAlchemy + PyMySQL. Only reachable if the customer's instance is
network-accessible from this server - same model as sqlserver_connector.py."""
from __future__ import annotations
from typing import Any

try:
    from sqlalchemy import create_engine, text
    _HAS_SA = True
except ImportError:
    _HAS_SA = False

try:
    import pymysql  # noqa: F401
    _HAS_PYMYSQL = True
except ImportError:
    _HAS_PYMYSQL = False


class MysqlConnector:
    def __init__(self, config: dict[str, Any]):
        self.host = str(config.get("host", "127.0.0.1")).strip()
        self.port = int(config.get("port", 3306))
        self.database = str(config.get("database", "")).strip()
        self.username = str(config.get("username", "")).strip()
        self.password = str(config.get("password", "")).strip()
        self.query = str(config.get("query", "")).strip()

        if not self.database:
            raise ValueError("database is required for MySQL connector")
        if not self.query:
            raise ValueError("query is required for MySQL connector")

    def _check_deps(self):
        if not _HAS_SA:
            raise RuntimeError("sqlalchemy is required. Install with: pip install sqlalchemy")
        if not _HAS_PYMYSQL:
            raise RuntimeError("pymysql is required. Install with: pip install pymysql")

    def _engine(self):
        self._check_deps()
        from urllib.parse import quote_plus
        user = quote_plus(self.username)
        pwd = quote_plus(self.password)
        url = f"mysql+pymysql://{user}:{pwd}@{self.host}:{self.port}/{self.database}"
        return create_engine(url, pool_pre_ping=True, pool_size=1, max_overflow=0)

    def test(self) -> dict[str, Any]:
        eng = self._engine()
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        eng.dispose()
        return {"ok": True, "message": f"Connected to {self.host}/{self.database}"}

    def preview(self, n: int = 10) -> list[dict[str, Any]]:
        eng = self._engine()
        limited = f"SELECT * FROM ({self.query}) AS _preview LIMIT {n}"  # nosec B608 - self.query is admin-configured connector config, not per-request user input
        with eng.connect() as conn:
            result = conn.execute(text(limited))
            cols = list(result.keys())
            rows = [dict(zip(cols, row)) for row in result.fetchall()]
        eng.dispose()
        return rows

    def iter_rows(self, max_rows: int = 500_000):
        eng = self._engine()
        try:
            with eng.connect() as conn:
                result = conn.execution_options(
                    stream_results=True, max_row_buffer=10_000
                ).execute(text(self.query))
                cols = list(result.keys())
                n = 0
                for row in result:
                    yield dict(zip(cols, row))
                    n += 1
                    if max_rows and n >= max_rows:
                        break
        finally:
            eng.dispose()

    def fetch_all(self, max_rows: int = 500_000) -> list[dict[str, Any]]:
        return list(self.iter_rows(max_rows=max_rows))

    def fetch_since(
        self, cursor_column: str, cursor_value: str | None, max_rows: int = 500_000
    ) -> tuple[list[dict[str, Any]], str | None]:
        eng = self._engine()
        try:
            base = f"SELECT * FROM ({self.query}) AS _src"  # nosec B608
            if cursor_value is not None:
                wrapped = (
                    f"SELECT * FROM ({base}) AS _cursor WHERE {cursor_column} > :cursor_value "
                    f"ORDER BY {cursor_column} LIMIT {max_rows}"
                )  # nosec B608 - cursor_column is admin-picked from preview() output, not request-time user input
                params: dict[str, Any] = {"cursor_value": cursor_value}
            else:
                wrapped = f"SELECT * FROM ({base}) AS _cursor ORDER BY {cursor_column} LIMIT {max_rows}"  # nosec B608
                params = {}
            with eng.connect() as conn:
                result = conn.execute(text(wrapped), params)
                cols = list(result.keys())
                rows = [dict(zip(cols, row)) for row in result.fetchall()]
        finally:
            eng.dispose()

        new_cursor = str(rows[-1][cursor_column]) if rows else cursor_value
        return rows, new_cursor
```

- [ ] **Step 4: Register the `mysql` connector type**

In `backend/app/services/connectors/__init__.py`:

```python
VALID_TYPES = {
    "csv", "excel",
    "sqlserver", "oracle", "postgres", "mysql",
    "sap_odata", "erp_rest", "mes_rest",
    "opcua", "mqtt",
    "opcda", "pi_webapi",
}
```

```python
    if t == "mysql":
        from app.services.connectors.mysql_connector import MysqlConnector
        return MysqlConnector(config)
```
(insert right after the `"postgres"` branch added in Task 3)

- [ ] **Step 5: Add the MySQL driver to requirements**

In `backend/requirements.txt`:
```
pymysql>=1.1.0          # MySQL (Azure Database for MySQL / AWS RDS/Aurora MySQL)
```

- [ ] **Step 6: Install and run tests to verify they pass**

Run: `cd backend && pip install pymysql && pytest tests/test_connectors_fetch_since.py -v`
Expected: PASS (all tests including the new MySQL one)

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/connectors/mysql_connector.py backend/app/services/connectors/__init__.py backend/tests/test_connectors_fetch_since.py backend/requirements.txt
git commit -m "feat: add MySQL connector with incremental fetch"
```

---

### Task 5: `SyncSchedule`/`SyncTick`/`SyncRow` models + migration

**Files:**
- Create: `backend/app/models/sync_schedule.py` (replaces `backend/app/models/realtime.py`, which stays in place until Task 12's deletion sweep — nothing imports the new module yet, so both can coexist safely for this task)
- Create: `backend/app/models/sync_tick.py`
- Create: `backend/alembic/versions/<new_revision>_sync_schedule_tables.py`
- Test: `backend/tests/test_sync_schedule_models.py` (new)

**Interfaces:**
- Produces: `SyncSchedule` (table `sync_schedules`: id, org_id, integration_id, name, column_map, machine, sensor_map, interval_seconds, cursor_column, cursor_value, desired_status, last_error, created_by, created_at, updated_at). `SyncTick` (table `sync_ticks`: id, schedule_id, org_id, ts, row_count, metrics). `SyncRow` (table `sync_rows`: id, schedule_id, ts, row).
- Consumes: nothing new.

- [ ] **Step 1: Write the failing model test**

```python
# backend/tests/test_sync_schedule_models.py
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.sync_schedule import SyncSchedule
from app.models.sync_tick import SyncRow, SyncTick


@pytest.mark.asyncio
async def test_sync_schedule_round_trips_cursor_fields(db_session):
    from app.models.integration import Integration
    from app.models.tenant import Org
    from app.models.user import User

    org = Org(name="Acme")
    db_session.add(org)
    await db_session.flush()
    user = User(org_id=org.id, email="a@example.com", hashed_password="x")
    db_session.add(user)
    await db_session.flush()
    integration = Integration(
        org_id=org.id, name="Cloud DB", type="postgres", config={}, created_by=user.id,
    )
    db_session.add(integration)
    await db_session.flush()

    schedule = SyncSchedule(
        org_id=org.id, integration_id=integration.id, name="Line 1",
        column_map={"units_col": "units"}, cursor_column="ts", cursor_value=None,
        interval_seconds=300, created_by=user.id,
    )
    db_session.add(schedule)
    await db_session.commit()

    fetched = (
        await db_session.execute(select(SyncSchedule).where(SyncSchedule.id == schedule.id))
    ).scalar_one()
    assert fetched.cursor_column == "ts"
    assert fetched.desired_status == "running"


@pytest.mark.asyncio
async def test_sync_tick_and_sync_row_link_to_schedule(db_session):
    from app.models.integration import Integration
    from app.models.tenant import Org
    from app.models.user import User

    org = Org(name="Acme")
    db_session.add(org)
    await db_session.flush()
    user = User(org_id=org.id, email="b@example.com", hashed_password="x")
    db_session.add(user)
    await db_session.flush()
    integration = Integration(org_id=org.id, name="Cloud DB", type="postgres", config={}, created_by=user.id)
    db_session.add(integration)
    await db_session.flush()
    schedule = SyncSchedule(
        org_id=org.id, integration_id=integration.id, name="Line 1",
        cursor_column="ts", interval_seconds=300, created_by=user.id,
    )
    db_session.add(schedule)
    await db_session.flush()

    tick = SyncTick(schedule_id=schedule.id, org_id=org.id, row_count=3, metrics={"throughput": 9.0})
    row = SyncRow(schedule_id=schedule.id, row={"ts": "2026-08-01", "units": 3})
    db_session.add_all([tick, row])
    await db_session.commit()

    fetched_tick = (
        await db_session.execute(select(SyncTick).where(SyncTick.schedule_id == schedule.id))
    ).scalar_one()
    assert fetched_tick.metrics["throughput"] == 9.0
    fetched_row = (
        await db_session.execute(select(SyncRow).where(SyncRow.schedule_id == schedule.id))
    ).scalar_one()
    assert fetched_row.row["units"] == 3
```

Also register the three new modules for `Base.metadata.create_all` in `backend/tests/conftest.py`: add `sync_schedule, sync_tick,` to the `from app.models import (...)` block (alphabetically, near `sales, sensor,`).

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && pytest tests/test_sync_schedule_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.models.sync_schedule'`

- [ ] **Step 3: Create `app/models/sync_schedule.py`**

```python
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SyncSchedule(Base):
    """A tenant-scoped scheduled pull-sync against an Integration's connector.
    Replaces the old RealtimeStream (app.services.realtime's thread-based
    TickProducer + Redpanda/Flink pipeline) - see
    docs/superpowers/specs/2026-08-04-scheduled-cloud-sync-design.md. A
    background APScheduler job (app.services.sync_scheduler) wakes on
    interval_seconds, pulls only rows newer than cursor_value via the
    connector's fetch_since(), and persists results as SyncTick/SyncRow.
    """

    __tablename__ = "sync_schedules"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    integration_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("integrations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # {"units_col": "...", "scrap_col": "...", ...} - see app.services.metrics_calc.compute_window_metrics.
    column_map: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # Which machine this schedule's rows belong to - None = OEE-only, unaffected by CBM.
    machine: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # {"vib_x": "vibration_x_mm_s", ...} - sensor channel name -> field name to read
    # from each synced row. See app.services.sensor_sync.
    sensor_map: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    # Column used for incremental fetch (SqlServerConnector.fetch_since and
    # friends) - admin-picked from the connector's preview() output.
    cursor_column: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Last-seen cursor value, stored as text (each connector casts per its
    # column's real type when building the WHERE clause).
    cursor_value: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # running | stopped - the user's intent, read by app.services.sync_scheduler
    # on startup to decide which schedules to (re)register a job for.
    desired_status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    last_error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
```

- [ ] **Step 4: Create `app/models/sync_tick.py`**

```python
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SyncTick(Base):
    """One successful scheduled-sync run's computed metrics - replaces the old
    JSONL tick-history file app.services.realtime used to write. Queried for
    GET /sync-schedules/{id}/history and for stream_metric alert evaluation
    (app.services.alerts.eval_stream_metric)."""

    __tablename__ = "sync_ticks"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    schedule_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("sync_schedules.id", ondelete="CASCADE"), nullable=False, index=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class SyncRow(Base):
    """Raw synced rows, capped at ~10,000 most recent per schedule (oldest
    trimmed each run by app.services.sync_scheduler) - the feature-column
    buffer app.services.automl_realtime's live scoring/drift-check needs,
    replacing the old in-memory realtime_cache.recent_rows()."""

    __tablename__ = "sync_rows"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    schedule_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("sync_schedules.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    row: Mapped[dict] = mapped_column(JSON, nullable=False)
```

- [ ] **Step 5: Register the new models in `conftest.py`**

In `backend/tests/conftest.py`, add `sync_schedule` and `sync_tick` to the model-registration import block:
```python
from app.models import (  # noqa: F401 - register models
    alert,
    audit,
    automl_live_binding,
    automl_run,
    bom,
    capture_event,
    dashboard,
    dataset,
    device_token,
    downtime_tracking,
    forecast,
    inventory,
    inventory_snapshot,
    integration,
    kpi,
    location,
    lot,
    machine,
    maintenance_schedule,
    operator,
    pace_tracking,
    procurement,
    rca_case,
    realtime,
    report_spec,
    routing,
    sales,
    sensor,
    shift_forecast,
    shift_schedule,
    simulation,
    staff,
    sync_schedule,
    sync_tick,
    telemetry,
    tenant,
    user,
    work_center,
    work_order,
    workflow,
)
```
(`realtime` stays imported here until Task 12 removes it — both tables can coexist in the SQLite test schema for now.)

- [ ] **Step 6: Run to verify the model test passes**

Run: `cd backend && pytest tests/test_sync_schedule_models.py -v`
Expected: PASS

- [ ] **Step 7: Find the current Alembic head(s)**

Run: `cd backend && python -m alembic heads`

If exactly one line is printed, that revision id is your `down_revision` in Step 8. If more than one is printed (this branch has unmerged migration heads from parallel feature work), set `down_revision` in Step 8 to a **tuple** of all printed revision ids — Alembic treats a migration with a tuple `down_revision` as a merge, landing this migration as the point where those branches reconverge. Do not pick just one and ignore the rest; that leaves a real, silent split in migration history.

- [ ] **Step 8: Write the migration**

Create `backend/alembic/versions/<new_revision>_sync_schedule_tables.py` (generate `<new_revision>` with `python -c "import uuid; print(uuid.uuid4().hex[:12])"` or let `alembic revision` assign one):

```python
"""sync_schedule_tables

Revision ID: <new_revision>
Revises: <down_revision from Step 7>
Create Date: 2026-08-04

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "<new_revision>"
down_revision = "<down_revision from Step 7>"  # single id, or a tuple if merging multiple heads
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- rename realtime_streams -> sync_schedules, drop/add columns ---
    op.rename_table("realtime_streams", "sync_schedules")
    op.drop_column("sync_schedules", "accumulate")
    op.add_column("sync_schedules", sa.Column("cursor_column", sa.String(length=255), nullable=True))
    op.add_column("sync_schedules", sa.Column("cursor_value", sa.String(length=255), nullable=True))

    # --- new sync_ticks / sync_rows tables ---
    op.create_table(
        "sync_ticks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("schedule_id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["schedule_id"], ["sync_schedules.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sync_ticks_schedule_id", "sync_ticks", ["schedule_id"])
    op.create_index("ix_sync_ticks_org_id", "sync_ticks", ["org_id"])

    op.create_table(
        "sync_rows",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("schedule_id", sa.Uuid(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("row", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["schedule_id"], ["sync_schedules.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sync_rows_schedule_id", "sync_rows", ["schedule_id"])

    # --- repoint automl_live_bindings.stream_id -> schedule_id (FK now targets sync_schedules) ---
    op.alter_column("automl_live_bindings", "stream_id", new_column_name="schedule_id")
    op.drop_constraint("automl_live_bindings_stream_id_fkey", "automl_live_bindings", type_="foreignkey")
    op.create_foreign_key(
        "fk_automl_live_bindings_schedule_id", "automl_live_bindings", "sync_schedules",
        ["schedule_id"], ["id"], ondelete="CASCADE",
    )
    op.drop_index("ix_automl_live_bindings_stream_id", table_name="automl_live_bindings")
    op.create_index("ix_automl_live_bindings_schedule_id", "automl_live_bindings", ["schedule_id"])

    # --- repoint automl_runs.source_stream_id's FK target (column name unchanged) ---
    op.drop_constraint("fk_automl_runs_source_stream_id", "automl_runs", type_="foreignkey")
    op.create_foreign_key(
        "fk_automl_runs_source_stream_id", "automl_runs", "sync_schedules",
        ["source_stream_id"], ["id"], ondelete="SET NULL",
    )

    # --- one-time data migration: alert_rules.config "stream_id" -> "schedule_id" ---
    op.execute(
        "UPDATE alert_rules SET config = (config - 'stream_id') || "
        "jsonb_build_object('schedule_id', config->'stream_id') "
        "WHERE type = 'stream_metric' AND config ? 'stream_id'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE alert_rules SET config = (config - 'schedule_id') || "
        "jsonb_build_object('stream_id', config->'schedule_id') "
        "WHERE type = 'stream_metric' AND config ? 'schedule_id'"
    )

    op.drop_constraint("fk_automl_runs_source_stream_id", "automl_runs", type_="foreignkey")
    op.create_foreign_key(
        "fk_automl_runs_source_stream_id", "automl_runs", "realtime_streams",
        ["source_stream_id"], ["id"], ondelete="SET NULL",
    )

    op.drop_index("ix_automl_live_bindings_schedule_id", table_name="automl_live_bindings")
    op.create_index("ix_automl_live_bindings_stream_id", "automl_live_bindings", ["stream_id"])
    op.drop_constraint("fk_automl_live_bindings_schedule_id", "automl_live_bindings", type_="foreignkey")
    op.create_foreign_key(
        "automl_live_bindings_stream_id_fkey", "automl_live_bindings", "realtime_streams",
        ["schedule_id"], ["id"], ondelete="CASCADE",
    )
    op.alter_column("automl_live_bindings", "schedule_id", new_column_name="stream_id")

    op.drop_index("ix_sync_rows_schedule_id", table_name="sync_rows")
    op.drop_table("sync_rows")
    op.drop_index("ix_sync_ticks_org_id", table_name="sync_ticks")
    op.drop_index("ix_sync_ticks_schedule_id", table_name="sync_ticks")
    op.drop_table("sync_ticks")

    op.drop_column("sync_schedules", "cursor_value")
    op.drop_column("sync_schedules", "cursor_column")
    op.add_column("sync_schedules", sa.Column("accumulate", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.rename_table("sync_schedules", "realtime_streams")
```

Note the auto-generated FK constraint name `automl_live_bindings_stream_id_fkey` in Step 8 is Postgres's default naming convention for an unnamed `ForeignKeyConstraint` — confirm the actual name in your target DB with `\d automl_live_bindings` (psql) before running this migration against anything but a throwaway dev DB; adjust the string if it differs.

- [ ] **Step 9: Dry-run the migration against a scratch Postgres**

Run: `cd backend && alembic upgrade head` against a disposable dev database (e.g. the `docker-compose.yml` `db` service pointed at a fresh volume), then `alembic downgrade -1` to confirm the downgrade path also runs cleanly.
Expected: both directions complete without error.

- [ ] **Step 10: Commit**

```bash
git add backend/app/models/sync_schedule.py backend/app/models/sync_tick.py backend/tests/test_sync_schedule_models.py backend/tests/conftest.py backend/alembic/versions/
git commit -m "feat: add SyncSchedule/SyncTick/SyncRow models and migration"
```

---

### Task 6: `sync_scheduler.py` — core scheduled-sync job

**Files:**
- Create: `backend/app/services/sync_scheduler.py`
- Test: `backend/tests/test_sync_scheduler.py` (new)
- Modify: `backend/requirements.txt` (add `apscheduler`)

**Interfaces:**
- Consumes: `app.services.metrics_calc.compute_window_metrics` (Task 1), `app.services.connectors.get_connector` (existing), `app.models.sync_schedule.SyncSchedule`, `app.models.sync_tick.SyncTick`/`SyncRow` (Task 5).
- Produces: `async def start_scheduler() -> None`, `async def stop_scheduler() -> None`, `def add_job(schedule_id: uuid.UUID, interval_seconds: int) -> None`, `def remove_job(schedule_id: uuid.UUID) -> None`, `def reschedule_job(schedule_id: uuid.UUID, interval_seconds: int) -> None`, `async def run_sync(schedule_id: uuid.UUID) -> None` — Task 7 (sensor hook) and Task 8 (AutoML hook) both extend `_run_sync`'s body; Task 10 (routes) calls `add_job`/`remove_job`/`reschedule_job`; Task 11 (main.py) calls `start_scheduler`/`stop_scheduler`.

This task builds `_run_sync` far enough to fetch, compute metrics, persist `SyncTick`/`SyncRow`, trim the row buffer, and advance the cursor — **without** the sensor/AutoML hooks yet (those are separate, independently-testable tasks that extend the same function).

- [ ] **Step 1: Add `apscheduler` to requirements**

In `backend/requirements.txt`, near the top (alongside `sqlalchemy`/`alembic`):
```
apscheduler>=3.10.4
```
Run: `cd backend && pip install apscheduler`

- [ ] **Step 2: Write the failing tests**

```python
# backend/tests/test_sync_scheduler.py
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models.integration import Integration
from app.models.sync_schedule import SyncSchedule
from app.models.sync_tick import SyncRow, SyncTick
from app.models.tenant import Org
from app.models.user import User
from app.services import sync_scheduler


class _FakeConnector:
    """Stands in for a real DB connector's fetch_since - returns fixed rows
    once, then nothing, so tests can assert both the "new data" and
    "no new data" paths without a live database."""

    def __init__(self, rows_by_cursor: dict):
        self.rows_by_cursor = rows_by_cursor
        self.calls: list[tuple] = []

    def fetch_since(self, cursor_column, cursor_value, max_rows=500_000):
        self.calls.append((cursor_column, cursor_value, max_rows))
        rows = self.rows_by_cursor.get(cursor_value, [])
        new_cursor = str(rows[-1][cursor_column]) if rows else cursor_value
        return rows, new_cursor


async def _make_schedule(db_session, *, cursor_value=None) -> SyncSchedule:
    org = Org(name="Acme")
    db_session.add(org)
    await db_session.flush()
    user = User(org_id=org.id, email=f"{uuid.uuid4()}@example.com", hashed_password="x")
    db_session.add(user)
    await db_session.flush()
    integration = Integration(org_id=org.id, name="Cloud DB", type="postgres", config={}, created_by=user.id)
    db_session.add(integration)
    await db_session.flush()
    schedule = SyncSchedule(
        org_id=org.id, integration_id=integration.id, name="Line 1",
        column_map={"units_col": "units"}, cursor_column="ts", cursor_value=cursor_value,
        interval_seconds=300, created_by=user.id, desired_status="running",
    )
    db_session.add(schedule)
    await db_session.commit()
    await db_session.refresh(schedule)
    return schedule


@pytest.mark.asyncio
async def test_run_sync_persists_tick_and_advances_cursor(db_session, monkeypatch):
    schedule = await _make_schedule(db_session)
    fake = _FakeConnector({None: [{"ts": "2026-08-01T00:00:00", "units": 5}, {"ts": "2026-08-01T00:01:00", "units": 3}]})
    monkeypatch.setattr(sync_scheduler, "get_connector", lambda *_a, **_kw: fake)
    monkeypatch.setattr("app.db.session.async_session_factory", lambda: db_session)

    await sync_scheduler.run_sync(schedule.id)

    tick = (
        await db_session.execute(select(SyncTick).where(SyncTick.schedule_id == schedule.id))
    ).scalar_one()
    assert tick.row_count == 2
    assert tick.metrics["throughput"] == 8.0

    rows = (
        await db_session.execute(select(SyncRow).where(SyncRow.schedule_id == schedule.id))
    ).scalars().all()
    assert len(rows) == 2

    refreshed = (
        await db_session.execute(select(SyncSchedule).where(SyncSchedule.id == schedule.id))
    ).scalar_one()
    assert refreshed.cursor_value == "2026-08-01T00:01:00"
    assert refreshed.last_error is None


@pytest.mark.asyncio
async def test_run_sync_no_new_rows_is_a_noop(db_session, monkeypatch):
    schedule = await _make_schedule(db_session, cursor_value="2026-08-01T00:01:00")
    fake = _FakeConnector({})  # no rows for any cursor
    monkeypatch.setattr(sync_scheduler, "get_connector", lambda *_a, **_kw: fake)
    monkeypatch.setattr("app.db.session.async_session_factory", lambda: db_session)

    await sync_scheduler.run_sync(schedule.id)

    tick_count = len((
        await db_session.execute(select(SyncTick).where(SyncTick.schedule_id == schedule.id))
    ).scalars().all())
    assert tick_count == 0
    refreshed = (
        await db_session.execute(select(SyncSchedule).where(SyncSchedule.id == schedule.id))
    ).scalar_one()
    assert refreshed.cursor_value == "2026-08-01T00:01:00"  # unchanged


@pytest.mark.asyncio
async def test_run_sync_failure_sets_last_error_and_keeps_cursor(db_session, monkeypatch):
    schedule = await _make_schedule(db_session, cursor_value="2026-08-01T00:01:00")

    class _BrokenConnector:
        def fetch_since(self, *a, **kw):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(sync_scheduler, "get_connector", lambda *_a, **_kw: _BrokenConnector())
    monkeypatch.setattr("app.db.session.async_session_factory", lambda: db_session)

    await sync_scheduler.run_sync(schedule.id)  # must not raise

    refreshed = (
        await db_session.execute(select(SyncSchedule).where(SyncSchedule.id == schedule.id))
    ).scalar_one()
    assert "connection refused" in refreshed.last_error
    assert refreshed.cursor_value == "2026-08-01T00:01:00"


@pytest.mark.asyncio
async def test_row_buffer_is_trimmed_to_cap(db_session, monkeypatch):
    schedule = await _make_schedule(db_session)
    monkeypatch.setattr(sync_scheduler, "_ROW_BUFFER_CAP", 3)
    rows = [{"ts": f"2026-08-01T00:0{i}:00", "units": 1} for i in range(5)]
    fake = _FakeConnector({None: rows})
    monkeypatch.setattr(sync_scheduler, "get_connector", lambda *_a, **_kw: fake)
    monkeypatch.setattr("app.db.session.async_session_factory", lambda: db_session)

    await sync_scheduler.run_sync(schedule.id)

    remaining = (
        await db_session.execute(select(SyncRow).where(SyncRow.schedule_id == schedule.id))
    ).scalars().all()
    assert len(remaining) == 3
    assert {r.row["ts"] for r in remaining} == {"2026-08-01T00:02:00", "2026-08-01T00:03:00", "2026-08-01T00:04:00"}


def test_add_and_remove_job_are_idempotent():
    sync_scheduler._scheduler = None  # ensure a clean module-level state for this test
    sync_scheduler.add_job(uuid.uuid4(), 300)  # scheduler not started - no-op, must not raise
    sync_scheduler.remove_job(uuid.uuid4())  # removing a job that was never added - must not raise
```

Note: `monkeypatch.setattr("app.db.session.async_session_factory", lambda: db_session)` replaces the factory with something that returns the *same* `db_session` fixture every call (not a real session factory) so `_run_sync`'s internal `async with db_session.async_session_factory() as db:` pattern reuses the test's transactional session — mirror however `backend/tests/test_realtime.py`'s existing tests patch `db_session.async_session_factory` for its own `_sse_generator` test (check that file for the exact pattern in use before assuming this one compiles verbatim; adjust to match).

- [ ] **Step 3: Run to verify failure**

Run: `cd backend && pytest tests/test_sync_scheduler.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.sync_scheduler'`

- [ ] **Step 4: Create `app/services/sync_scheduler.py`**

```python
"""APScheduler-driven periodic pull-sync against a SyncSchedule's Integration
connector - replaces app.services.realtime's thread-based TickProducer +
Redpanda/Flink pipeline. See
docs/superpowers/specs/2026-08-04-scheduled-cloud-sync-design.md.

One APScheduler job per SyncSchedule. Each tick: fetch only new rows via the
connector's fetch_since(), compute OEE metrics over that batch, persist a
SyncTick (metrics history) + SyncRow entries (raw-row buffer, capped), and
advance the schedule's cursor. Never raises out of run_sync - a bad
connector/config shouldn't kill the scheduler, same contract the old
TickProducer._run's fetch loop had."""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import delete, select

from app.db import session as db_session
from app.models.integration import Integration
from app.models.sync_schedule import SyncSchedule
from app.models.sync_tick import SyncRow, SyncTick
from app.services import metrics_calc
from app.services.connectors import get_connector
from app.services.integrations import decrypt_config

logger = logging.getLogger(__name__)

# Raw rows kept per schedule for AutoML live scoring/drift-check - mirrors the
# old realtime_cache._RAW_ROW_BUFFER size.
_ROW_BUFFER_CAP = 10_000

_scheduler: AsyncIOScheduler | None = None


def _job_id(schedule_id: uuid.UUID) -> str:
    return f"sync-schedule-{schedule_id}"


async def start_scheduler() -> None:
    """Called from app.main's startup lifespan hook. Starts the process-wide
    scheduler and registers a job for every schedule still marked
    desired_status='running' - the scheduled-sync equivalent of the old
    realtime.resume_all_streams()."""
    global _scheduler
    _scheduler = AsyncIOScheduler()
    _scheduler.start()

    async with db_session.async_session_factory() as db:
        rows = (
            await db.execute(select(SyncSchedule).where(SyncSchedule.desired_status == "running"))
        ).scalars().all()
    for schedule in rows:
        add_job(schedule.id, schedule.interval_seconds)


async def stop_scheduler() -> None:
    """Called from app.main's shutdown lifespan hook."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def add_job(schedule_id: uuid.UUID, interval_seconds: int) -> None:
    """(Re)register a schedule's job. Also used as "reschedule" (interval
    change) via replace_existing=True - no separate reschedule primitive
    needed. A no-op if the scheduler hasn't started yet (e.g. called from a
    route before app startup finishes, which shouldn't happen in practice but
    shouldn't crash the request if it does)."""
    if _scheduler is None:
        return
    _scheduler.add_job(
        run_sync, trigger=IntervalTrigger(seconds=max(60, interval_seconds)),
        id=_job_id(schedule_id), args=[schedule_id], replace_existing=True,
        next_run_time=datetime.now(timezone.utc),  # run once immediately, not just after the first interval
    )


def remove_job(schedule_id: uuid.UUID) -> None:
    if _scheduler is None:
        return
    try:
        _scheduler.remove_job(_job_id(schedule_id))
    except Exception:
        pass  # job may already be gone (never started, or already removed) - stop/delete are idempotent


def reschedule_job(schedule_id: uuid.UUID, interval_seconds: int) -> None:
    add_job(schedule_id, interval_seconds)


async def run_sync(schedule_id: uuid.UUID) -> None:
    async with db_session.async_session_factory() as db:
        try:
            await _run_sync(db, schedule_id)
        except Exception as exc:
            logger.exception("sync_scheduler run failed for schedule %s", schedule_id)
            schedule = (
                await db.execute(select(SyncSchedule).where(SyncSchedule.id == schedule_id))
            ).scalar_one_or_none()
            if schedule is not None:
                schedule.last_error = str(exc)
                await db.commit()


async def _run_sync(db, schedule_id: uuid.UUID) -> None:
    schedule = (
        await db.execute(select(SyncSchedule).where(SyncSchedule.id == schedule_id))
    ).scalar_one_or_none()
    if schedule is None or schedule.desired_status != "running":
        return

    integration = (
        await db.execute(select(Integration).where(Integration.id == schedule.integration_id))
    ).scalar_one_or_none()
    if integration is None:
        schedule.last_error = "Integration not found"
        await db.commit()
        return

    connector = get_connector(integration.type, decrypt_config(integration.config))
    if not hasattr(connector, "fetch_since"):
        schedule.last_error = f"Connector type {integration.type!r} does not support incremental sync"
        await db.commit()
        return

    rows, new_cursor = await asyncio.to_thread(
        connector.fetch_since, schedule.cursor_column, schedule.cursor_value, 500_000
    )
    schedule.last_error = None

    if not rows:
        await db.commit()
        return

    metrics = metrics_calc.compute_window_metrics(rows, schedule.column_map)
    tick = SyncTick(schedule_id=schedule.id, org_id=schedule.org_id, row_count=len(rows), metrics=metrics)
    db.add(tick)

    now = datetime.now(timezone.utc)
    for row in rows:
        db.add(SyncRow(schedule_id=schedule.id, ts=now, row=row))
    await db.flush()
    await _trim_row_buffer(db, schedule.id)

    schedule.cursor_value = new_cursor
    await db.commit()


async def _trim_row_buffer(db, schedule_id: uuid.UUID) -> None:
    keep_ids = (
        await db.execute(
            select(SyncRow.id).where(SyncRow.schedule_id == schedule_id)
            .order_by(SyncRow.ts.desc()).limit(_ROW_BUFFER_CAP)
        )
    ).scalars().all()
    await db.execute(
        delete(SyncRow).where(SyncRow.schedule_id == schedule_id, SyncRow.id.notin_(keep_ids))
    )
```

- [ ] **Step 5: Run to verify tests pass**

Run: `cd backend && pytest tests/test_sync_scheduler.py -v`
Expected: PASS. If the `async_session_factory` monkeypatch doesn't behave as assumed, check `backend/tests/test_realtime.py`'s existing patch of the same factory (its `test_sse_generator_yields_first_snapshot_frame` predecessor) and adjust the fixture/patch shape in this test file to match what actually works against this codebase's session-management setup, then re-run.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/sync_scheduler.py backend/tests/test_sync_scheduler.py backend/requirements.txt
git commit -m "feat: add APScheduler-driven scheduled sync job"
```

---

### Task 7: CBM sensor extraction, ported to batch cadence

**Files:**
- Create: `backend/app/services/sensor_sync.py`
- Modify: `backend/app/services/sync_scheduler.py` (wire in the hook)
- Test: `backend/tests/test_sensor_sync.py` (new)

**Interfaces:**
- Consumes: `app.models.sensor.{SensorReading, SensorBaseline, SensorAnomaly}` (existing), `app.services.anomaly.{WINDOW, score_reading}` (existing, unchanged).
- Produces: `async def process_rows(db: AsyncSession, schedule: SyncSchedule, rows: list[dict[str, Any]]) -> None`, called from `sync_scheduler._run_sync`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_sensor_sync.py
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.integration import Integration
from app.models.sensor import SensorAnomaly, SensorBaseline, SensorReading
from app.models.sync_schedule import SyncSchedule
from app.models.tenant import Org
from app.models.user import User
from app.services import sensor_sync


async def _make_schedule(db_session, *, sensor_map: dict, machine: str = "M1") -> SyncSchedule:
    org = Org(name="Acme")
    db_session.add(org)
    await db_session.flush()
    user = User(org_id=org.id, email=f"{uuid.uuid4()}@example.com", hashed_password="x")
    db_session.add(user)
    await db_session.flush()
    integration = Integration(org_id=org.id, name="Cloud DB", type="postgres", config={}, created_by=user.id)
    db_session.add(integration)
    await db_session.flush()
    schedule = SyncSchedule(
        org_id=org.id, integration_id=integration.id, name="Line 1",
        machine=machine, sensor_map=sensor_map, cursor_column="ts",
        interval_seconds=300, created_by=user.id,
    )
    db_session.add(schedule)
    await db_session.commit()
    await db_session.refresh(schedule)
    return schedule


@pytest.mark.asyncio
async def test_process_rows_writes_sensor_readings(db_session):
    schedule = await _make_schedule(db_session, sensor_map={"vib_x": "vibration_x"})
    rows = [{"vibration_x": 1.2}, {"vibration_x": 1.4}]

    await sensor_sync.process_rows(db_session, schedule, rows)
    await db_session.commit()

    readings = (
        await db_session.execute(
            select(SensorReading).where(SensorReading.org_id == schedule.org_id, SensorReading.sensor == "vib_x")
        )
    ).scalars().all()
    assert len(readings) == 2
    assert {r.value for r in readings} == {1.2, 1.4}

    baseline = (
        await db_session.execute(
            select(SensorBaseline).where(SensorBaseline.org_id == schedule.org_id, SensorBaseline.sensor == "vib_x")
        )
    ).scalar_one()
    assert baseline.n == 2


@pytest.mark.asyncio
async def test_process_rows_skips_missing_or_non_numeric_values(db_session):
    schedule = await _make_schedule(db_session, sensor_map={"vib_x": "vibration_x"})
    rows = [{"vibration_x": None}, {"vibration_x": "not-a-number"}, {}]

    await sensor_sync.process_rows(db_session, schedule, rows)
    await db_session.commit()

    readings = (
        await db_session.execute(select(SensorReading).where(SensorReading.org_id == schedule.org_id))
    ).scalars().all()
    assert readings == []


@pytest.mark.asyncio
async def test_process_rows_flags_out_of_control_reading(db_session):
    from app.analytics.spc_calc import MIN_BASELINE

    schedule = await _make_schedule(db_session, sensor_map={"vib_x": "vibration_x"})
    stable_rows = [{"vibration_x": 1.0} for _ in range(MIN_BASELINE)]
    await sensor_sync.process_rows(db_session, schedule, stable_rows)
    await db_session.commit()

    spike_rows = [{"vibration_x": 500.0}]
    await sensor_sync.process_rows(db_session, schedule, spike_rows)
    await db_session.commit()

    anomalies = (
        await db_session.execute(select(SensorAnomaly).where(SensorAnomaly.org_id == schedule.org_id))
    ).scalars().all()
    assert len(anomalies) >= 1
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && pytest tests/test_sensor_sync.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.sensor_sync'`

- [ ] **Step 3: Create `app/services/sensor_sync.py`**

```python
"""CBM sensor extraction + anomaly scoring for a batch of freshly-synced
rows. Ported from app.services.sensor_consumer's per-tick Kafka handler (now
called synchronously from app.services.sync_scheduler after each scheduled
sync instead of via a Redpanda consumer) - same extraction/scoring logic,
unchanged."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sensor import SensorAnomaly, SensorBaseline, SensorReading
from app.models.sync_schedule import SyncSchedule
from app.services.anomaly import WINDOW, score_reading


async def process_rows(db: AsyncSession, schedule: SyncSchedule, rows: list[dict[str, Any]]) -> None:
    now = datetime.now(timezone.utc)
    for row in rows:
        for sensor, field in schedule.sensor_map.items():
            raw = row.get(field)
            if raw is None:
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue

            reading = SensorReading(org_id=schedule.org_id, machine=schedule.machine, sensor=sensor, value=value, ts=now)
            db.add(reading)
            await db.flush()

            window_rows = (
                await db.execute(
                    select(SensorReading)
                    .where(
                        SensorReading.org_id == schedule.org_id,
                        SensorReading.machine == schedule.machine,
                        SensorReading.sensor == sensor,
                    )
                    .order_by(SensorReading.ts.desc())
                    .limit(WINDOW)
                )
            ).scalars().all()
            window_rows = list(reversed(window_rows))  # oldest first

            result = score_reading([r.value for r in window_rows], [r.ts for r in window_rows])

            baseline = (
                await db.execute(
                    select(SensorBaseline).where(
                        SensorBaseline.org_id == schedule.org_id,
                        SensorBaseline.machine == schedule.machine,
                        SensorBaseline.sensor == sensor,
                    )
                )
            ).scalar_one_or_none()
            if baseline is None:
                baseline = SensorBaseline(org_id=schedule.org_id, machine=schedule.machine, sensor=sensor)
                db.add(baseline)
            baseline.n = result.n
            baseline.centerline = result.centerline
            baseline.ucl = result.ucl
            baseline.lcl = result.lcl
            baseline.mr_bar = result.mr_bar

            if not result.learning and not result.in_control:
                db.add(SensorAnomaly(
                    org_id=schedule.org_id, machine=schedule.machine, sensor=sensor,
                    reading_id=reading.id, violations=result.violations,
                ))
```

- [ ] **Step 4: Run to verify tests pass**

Run: `cd backend && pytest tests/test_sensor_sync.py -v`
Expected: PASS

- [ ] **Step 5: Wire the hook into `sync_scheduler._run_sync`**

In `backend/app/services/sync_scheduler.py`, add the import at the top:
```python
from app.services import sensor_sync
```
and in `_run_sync`, right after `await _trim_row_buffer(db, schedule.id)` and before `schedule.cursor_value = new_cursor`:
```python
    if schedule.sensor_map and schedule.machine:
        await sensor_sync.process_rows(db, schedule, rows)
```

- [ ] **Step 6: Run the full scheduler test file to confirm nothing broke**

Run: `cd backend && pytest tests/test_sync_scheduler.py tests/test_sensor_sync.py -v`
Expected: PASS (all tests from both files)

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/sensor_sync.py backend/app/services/sync_scheduler.py backend/tests/test_sensor_sync.py
git commit -m "feat: port CBM sensor extraction to scheduled-sync batch cadence"
```

---

### Task 8: AutoML live scoring/drift, ported to batch cadence

**Files:**
- Modify: `backend/app/services/automl_realtime.py` (full rewrite of the scoring/drift/retrain path; the file keeps its name)
- Modify: `backend/app/models/automl_live_binding.py` (`stream_id` → `schedule_id`, FK target)
- Modify: `backend/app/services/sync_scheduler.py` (wire in the hook)
- Test: `backend/tests/test_automl_realtime.py` (rewritten in place — read the existing file first to see what it currently asserts, then replace it)

**Interfaces:**
- Consumes: `app.models.sync_tick.{SyncTick, SyncRow}` (Task 5), `app.services.automl_engine.{predict_with_model, check_data_drift}` (existing, unchanged), `app.services.automl_storage.run_dir` (existing, unchanged).
- Produces: `async def score_and_check_binding(db: AsyncSession, binding_id: uuid.UUID, tick: SyncTick) -> None`, called from `sync_scheduler._run_sync` with the tick it just created.

- [ ] **Step 1: Update `AutoMLLiveBinding.stream_id` → `schedule_id`**

In `backend/app/models/automl_live_binding.py`, change:
```python
    stream_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("realtime_streams.id", ondelete="CASCADE"), nullable=False, index=True
    )
```
to:
```python
    schedule_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("sync_schedules.id", ondelete="CASCADE"), nullable=False, index=True
    )
```
and update the class docstring's reference to "RealtimeStream" → "SyncSchedule". (The migration for this column rename already landed in Task 5, Step 8 — this is the ORM side catching up to a DB schema that's already correct.)

- [ ] **Step 2: Write the failing tests (read the current file first)**

Read `backend/tests/test_automl_realtime.py` in full to see its existing fixtures/assertions (it currently drives scoring via a fake Kafka tick / `realtime_cache`). Replace its contents with tests that drive the new signature directly:

```python
# backend/tests/test_automl_realtime.py
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.automl_live_binding import AutoMLLiveBinding
from app.models.sync_tick import SyncRow, SyncTick
from app.services import automl_realtime

# Reuse whatever helper this test file already has for constructing an
# org/user/dataset/AutoMLRun/SyncSchedule fixture set (check the file's
# existing _make_run / _make_binding style helpers before writing new ones -
# match the established pattern rather than duplicating it).


@pytest.mark.asyncio
async def test_score_and_check_binding_merges_score_into_tick_metrics(db_session, monkeypatch):
    run, binding, schedule = await _make_run_and_binding(db_session)  # existing helper, adapted to schedule_id
    for i in range(5):
        db_session.add(SyncRow(schedule_id=schedule.id, row={"feature": i}))
    await db_session.flush()
    tick = SyncTick(schedule_id=schedule.id, org_id=schedule.org_id, row_count=5, metrics={"throughput": 1.0})
    db_session.add(tick)
    await db_session.flush()

    monkeypatch.setattr(automl_realtime, "_score_latest", lambda pipe, meta, df: 0.87)
    monkeypatch.setattr("joblib.load", lambda path: object())

    await automl_realtime.score_and_check_binding(db_session, binding.id, tick)
    await db_session.commit()

    refreshed_tick = (
        await db_session.execute(select(SyncTick).where(SyncTick.id == tick.id))
    ).scalar_one()
    assert refreshed_tick.metrics["throughput"] == 1.0  # existing metrics preserved
    assert refreshed_tick.metrics[binding.metric_name] == 0.87

    refreshed_binding = (
        await db_session.execute(select(AutoMLLiveBinding).where(AutoMLLiveBinding.id == binding.id))
    ).scalar_one()
    assert refreshed_binding.last_scored_at is not None


@pytest.mark.asyncio
async def test_score_and_check_binding_disabled_binding_is_noop(db_session):
    run, binding, schedule = await _make_run_and_binding(db_session)
    binding.enabled = False
    await db_session.commit()
    tick = SyncTick(schedule_id=schedule.id, org_id=schedule.org_id, row_count=0, metrics={})
    db_session.add(tick)
    await db_session.flush()

    await automl_realtime.score_and_check_binding(db_session, binding.id, tick)  # must not raise

    assert tick.metrics == {}
```

Adapt `_make_run_and_binding` from whatever fixture helper the existing `test_automl_realtime.py` already has, changing its `AutoMLLiveBinding(..., stream_id=...)` construction to `schedule_id=schedule.id` against a `SyncSchedule` instead of a `RealtimeStream`.

- [ ] **Step 3: Run to verify failure**

Run: `cd backend && pytest tests/test_automl_realtime.py -v`
Expected: FAIL (old signature/fixtures don't match)

- [ ] **Step 4: Rewrite `app/services/automl_realtime.py`**

```python
"""Live scoring, drift monitoring, and drift-triggered retraining for AutoML
runs bound to a SyncSchedule (app.models.automl_live_binding.AutoMLLiveBinding).

Runs as one step of app.services.sync_scheduler's post-sync pipeline instead
of its own polling thread - the old Redpanda-tick-driven version scored on a
fixed 30s timer against an in-memory raw-row buffer (realtime_cache); this
version scores once per scheduled sync, against the SyncRow rows that sync
just landed, and merges the score directly into that sync's SyncTick.metrics
so the existing stream_metric alerting (app.services.alerts.eval_stream_metric)
still just reads a schedule's latest tick - no new alerting subsystem needed.
See docs/superpowers/specs/2026-08-04-scheduled-cloud-sync-design.md.

Drift-checking and drift-triggered retraining are unchanged in substance:
compares the recent SyncRow window against the run's persisted training
baseline (explain_X/explain_y) on drift_check_interval_minutes, and - if
auto_retrain_on_drift and drift reaches drift_severity_trigger - materializes
the buffer into a Dataset and re-runs execute_automl_run, producing a new
versioned AutoMLRun + AutoMLLiveBinding rather than overwriting the old ones.
The new binding is picked up automatically on the schedule's next sync tick
(sync_scheduler looks up the enabled binding by schedule_id each run) - no
separate scheduler registry to re-adopt it into, unlike the old thread-based
version.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import joblib
import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.automl_live_binding import AutoMLLiveBinding
from app.models.automl_run import AutoMLRun
from app.models.sync_tick import SyncRow, SyncTick
from app.services import automl_engine, automl_storage
from app.services import datasets as ds_service

logger = logging.getLogger(__name__)

_WINDOW_ROWS = 50
_SEVERITY_RANK = {"low": 0, "moderate": 1, "high": 2}


def _severity_at_least(severity: str, trigger: str) -> bool:
    return _SEVERITY_RANK.get(severity, 0) >= _SEVERITY_RANK.get(trigger, 1)


async def score_and_check_binding(db: AsyncSession, binding_id: uuid.UUID, tick: SyncTick) -> None:
    """One post-sync step for a single binding: score the batch that just
    landed in `tick`, merge the score into tick.metrics, and drift-check/
    retrain if due. Never raises - a bad binding/run shouldn't fail the sync
    it's riding on."""
    try:
        await _tick(db, binding_id, tick)
    except Exception:
        logger.exception("automl_realtime scoring failed for binding %s", binding_id)


async def _tick(db: AsyncSession, binding_id: uuid.UUID, tick: SyncTick) -> None:
    binding = (
        await db.execute(select(AutoMLLiveBinding).where(AutoMLLiveBinding.id == binding_id))
    ).scalar_one_or_none()
    if binding is None or not binding.enabled:
        return

    run = (await db.execute(select(AutoMLRun).where(AutoMLRun.id == binding.run_id))).scalar_one_or_none()
    if run is None or run.status != "success" or not run.model_path:
        return

    rows = await _recent_rows(db, binding.schedule_id, _WINDOW_ROWS)
    if not rows:
        return
    df = pd.DataFrame(rows)

    try:
        pipe = joblib.load(run.model_path)
    except Exception:
        logger.exception("Could not load model for run %s", run.id)
        return

    meta = run.meta or {}
    score = _score_latest(pipe, meta, df)
    if score is not None:
        tick.metrics = {**tick.metrics, binding.metric_name: score}
        binding.last_scored_at = datetime.now(timezone.utc)

    due = binding.last_drift_check_at is None or (
        datetime.now(timezone.utc) - binding.last_drift_check_at
        >= timedelta(minutes=binding.drift_check_interval_minutes)
    )
    if due:
        await _check_drift(db, binding, run, df)


async def _recent_rows(db: AsyncSession, schedule_id: uuid.UUID, n: int) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(SyncRow.row).where(SyncRow.schedule_id == schedule_id)
            .order_by(SyncRow.ts.desc()).limit(n)
        )
    ).scalars().all()
    return list(reversed(rows))  # oldest first


def _score_latest(pipe: Any, meta: dict, df: pd.DataFrame) -> float | None:
    try:
        result = automl_engine.predict_with_model(pipe, meta, df, max_preview_rows=len(df))
    except Exception:
        logger.exception("Live scoring failed")
        return None
    preview = result.get("preview") or []
    if not preview:
        return None
    value = preview[-1].get("prediction")  # buffer order is chronological - last = most recent
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def _check_drift(db: AsyncSession, binding: AutoMLLiveBinding, run: AutoMLRun, df: pd.DataFrame) -> None:
    run_dir = automl_storage.run_dir(run.org_id, run.id)
    x_path = run_dir / "explain_X.csv"
    if not x_path.exists():
        return
    explain_X = pd.read_csv(x_path)

    try:
        drift = automl_engine.check_data_drift(run.meta or {}, explain_X, df)
    except ValueError:
        return  # explain sample too small / unavailable - nothing to compare against yet

    binding.last_drift_check_at = datetime.now(timezone.utc)
    severity = drift["overall_severity"]
    binding.last_drift_status = "ok" if severity == "low" else "drifted"

    if binding.auto_retrain_on_drift and _severity_at_least(severity, binding.drift_severity_trigger):
        await _retrain_on_drift(db, binding, run)


async def _retrain_on_drift(db: AsyncSession, binding: AutoMLLiveBinding, run: AutoMLRun) -> None:
    from app.services.automl_runner import execute_automl_run

    rows = await _recent_rows(db, binding.schedule_id, 10_000)
    if len(rows) < 20:
        return  # not enough accumulated data yet to retrain on

    csv_bytes = pd.DataFrame(rows).to_csv(index=False).encode("utf-8")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    dataset = await ds_service.create_dataset(
        db, org_id=binding.org_id, created_by=run.created_by,
        name=f"{run.name} (live retrain {stamp})", filename="live_retrain.csv", data=csv_bytes,
    )
    await db.flush()

    new_run = AutoMLRun(
        org_id=binding.org_id, dataset_id=dataset.id, created_by=run.created_by,
        name=f"{run.name} (retrained {stamp})",
        task=run.task, target_column=run.target_column, feature_columns=run.feature_columns,
        template_id=run.template_id, config=run.config or {}, source_stream_id=binding.schedule_id,
        status="queued",
    )
    db.add(new_run)
    await db.flush()

    new_binding = AutoMLLiveBinding(
        org_id=binding.org_id, run_id=new_run.id, schedule_id=binding.schedule_id,
        metric_name=binding.metric_name, drift_severity_trigger=binding.drift_severity_trigger,
        drift_check_interval_minutes=binding.drift_check_interval_minutes,
        auto_retrain_on_drift=binding.auto_retrain_on_drift, enabled=True,
    )
    db.add(new_binding)
    # The old binding stops scoring once the new run takes over - a versioned
    # successor, not an in-place overwrite (per spec). The new binding is
    # picked up automatically on this schedule's next sync tick.
    binding.enabled = False
    await db.flush()

    # execute_automl_run opens its own DB session to look up new_run by id,
    # so it needs this transaction committed first - this finalizes the
    # whole sync transaction (tick/rows/cursor included), which is fine:
    # everything up to this point is legitimately ready to persist.
    await db.commit()
    await execute_automl_run(str(new_run.id), str(binding.org_id))
```

- [ ] **Step 5: Run to verify tests pass**

Run: `cd backend && pytest tests/test_automl_realtime.py -v`
Expected: PASS

- [ ] **Step 6: Wire the hook into `sync_scheduler._run_sync`**

In `backend/app/services/sync_scheduler.py`, add the import:
```python
from app.models.automl_live_binding import AutoMLLiveBinding
from app.services import automl_realtime
```
and after `db.add(tick)` / `await db.flush()` for the tick (before the `SyncRow` loop is fine too — just needs `tick` to exist and have an id, and rows to already be in `db` if `_recent_rows` should see this batch; since `_recent_rows` queries `SyncRow` and this batch's rows are added right after, call the hook **after** the `SyncRow` loop and `await db.flush()`, right before `_trim_row_buffer`):

```python
    binding = (
        await db.execute(
            select(AutoMLLiveBinding).where(
                AutoMLLiveBinding.schedule_id == schedule.id, AutoMLLiveBinding.enabled.is_(True)
            )
        )
    ).scalar_one_or_none()
    if binding is not None:
        await automl_realtime.score_and_check_binding(db, binding.id, tick)
```

Full updated ordering inside `_run_sync` after this task and Task 7 should read: create `tick` → add all `SyncRow`s → flush → trim buffer → sensor hook → AutoML hook → advance cursor → commit. Apply that exact order.

- [ ] **Step 7: Run the full scheduler + automl + sensor test files together**

Run: `cd backend && pytest tests/test_sync_scheduler.py tests/test_sensor_sync.py tests/test_automl_realtime.py -v`
Expected: PASS (all)

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/automl_realtime.py backend/app/models/automl_live_binding.py backend/app/services/sync_scheduler.py backend/tests/test_automl_realtime.py
git commit -m "feat: port AutoML live scoring/drift-retrain to scheduled-sync batch cadence"
```

---

### Task 9: Alerts read from `SyncTick` instead of `realtime_cache`

**Files:**
- Modify: `backend/app/services/alerts.py`
- Test: `backend/tests/test_alerts.py` (locate and update the existing `stream_metric` test cases — grep first)

**Interfaces:**
- Produces: `async def eval_stream_metric(db: AsyncSession, rule: AlertRule) -> AlertTrigger` (was sync, took only `rule`). `stream_alerts`/`stream_alerts_bulk` keep their existing signatures but their `AlertRule.config["stream_id"]` filter becomes `AlertRule.config["schedule_id"]`.

- [ ] **Step 1: Locate existing stream_metric tests**

Run: `cd backend && grep -rln "stream_metric\|eval_stream_metric" tests/`
Read whatever file(s) come back in full before editing — likely `tests/test_alerts.py` and/or `tests/test_realtime.py` (the latter's `test_list_streams_attaches_alerts_to_the_right_stream` test, which Task 10 rewrites anyway as part of replacing that whole file).

- [ ] **Step 2: Update the failing test(s)**

In whichever file has `stream_metric` alert-rule tests, change every `"stream_id": <id>` in a `stream_metric` rule's `config` to `"schedule_id": <id>`, and change the fixture setup from creating a `RealtimeStream`/live snapshot to creating a `SyncSchedule` + a `SyncTick` row directly (no live process needed to test this anymore — a real improvement over the old `REDPANDA_BROKERS`-gated version). Example shape:

```python
@pytest.mark.asyncio
async def test_eval_stream_metric_reads_latest_sync_tick(db_session):
    from app.models.integration import Integration
    from app.models.sync_schedule import SyncSchedule
    from app.models.sync_tick import SyncTick
    from app.models.tenant import Org
    from app.models.user import User
    from app.models.alert import AlertRule
    from app.services.alerts import eval_stream_metric

    org = Org(name="Acme")
    db_session.add(org)
    await db_session.flush()
    user = User(org_id=org.id, email="c@example.com", hashed_password="x")
    db_session.add(user)
    await db_session.flush()
    integration = Integration(org_id=org.id, name="Cloud DB", type="postgres", config={}, created_by=user.id)
    db_session.add(integration)
    await db_session.flush()
    schedule = SyncSchedule(
        org_id=org.id, integration_id=integration.id, name="Line 1",
        cursor_column="ts", interval_seconds=300, created_by=user.id,
    )
    db_session.add(schedule)
    await db_session.flush()
    db_session.add(SyncTick(schedule_id=schedule.id, org_id=org.id, row_count=5, metrics={"throughput": 12.0}))
    await db_session.commit()

    rule = AlertRule(
        org_id=org.id, name="Throughput watch", type="stream_metric", severity="warning", enabled=True,
        config={"schedule_id": str(schedule.id), "metric_name": "throughput", "comparison": "gt", "threshold": 10},
    )

    trigger = await eval_stream_metric(db_session, rule)
    assert trigger.triggered is True
    assert trigger.value == 12.0


@pytest.mark.asyncio
async def test_eval_stream_metric_no_tick_yet(db_session):
    from app.models.integration import Integration
    from app.models.sync_schedule import SyncSchedule
    from app.models.tenant import Org
    from app.models.user import User
    from app.models.alert import AlertRule
    from app.services.alerts import eval_stream_metric

    org = Org(name="Acme")
    db_session.add(org)
    await db_session.flush()
    user = User(org_id=org.id, email="d@example.com", hashed_password="x")
    db_session.add(user)
    await db_session.flush()
    integration = Integration(org_id=org.id, name="Cloud DB", type="postgres", config={}, created_by=user.id)
    db_session.add(integration)
    await db_session.flush()
    schedule = SyncSchedule(
        org_id=org.id, integration_id=integration.id, name="Line 1",
        cursor_column="ts", interval_seconds=300, created_by=user.id,
    )
    db_session.add(schedule)
    await db_session.commit()
    # deliberately no SyncTick added - schedule exists but has never synced

    rule = AlertRule(
        org_id=org.id, name="Throughput watch", type="stream_metric", severity="warning", enabled=True,
        config={"schedule_id": str(schedule.id), "metric_name": "throughput", "comparison": "gt", "threshold": 10},
    )

    trigger = await eval_stream_metric(db_session, rule)
    assert trigger.triggered is False
    assert "not synced yet" in trigger.message.lower()
```

- [ ] **Step 3: Run to verify failure**

Run: `cd backend && pytest tests/test_alerts.py -k stream_metric -v` (adjust path to wherever Step 1 found the tests)
Expected: FAIL (old sync signature / old `stream_id` key)

- [ ] **Step 4: Rewrite `eval_stream_metric` in `app/services/alerts.py`**

Replace the existing function (currently at line 299) with:

```python
async def eval_stream_metric(db: AsyncSession, rule: AlertRule) -> AlertTrigger:
    """Targets a scheduled sync's latest computed metrics instead of a
    dataset - same threshold/offender shape as the dataset evaluators above.
    Reads the most recent SyncTick for the schedule (app.services.sync_scheduler
    writes one per successful sync) instead of the old realtime_cache snapshot.

    Metric values here reflect only the batch synced in that one tick, not a
    cumulative session total - same caveat app.services.metrics_calc carries
    forward from the pre-Redpanda StreamRunner days: thresholds on
    absolute-sum metrics like throughput may need re-tuning per your sync
    interval."""
    from app.models.sync_tick import SyncTick

    cfg = rule.config
    schedule_id = cfg.get("schedule_id", "")
    metric_name = cfg.get("metric_name", "")
    comparison = cfg.get("comparison", "gt")  # "gt" | "lt"
    threshold = float(cfg.get("threshold", 0))

    tick = None
    if schedule_id:
        tick = (
            await db.execute(
                select(SyncTick).where(SyncTick.schedule_id == uuid.UUID(schedule_id))
                .order_by(SyncTick.ts.desc()).limit(1)
            )
        ).scalar_one_or_none()
    if tick is None:
        return AlertTrigger(
            str(rule.id), rule.name, rule.type, rule.severity, False, 0.0, threshold,
            "Schedule has not synced yet — nothing to evaluate.",
        )

    metrics = tick.metrics or {}
    if metric_name not in metrics:
        return AlertTrigger(
            str(rule.id), rule.name, rule.type, rule.severity, False, 0.0, threshold,
            f"Metric '{metric_name}' not available on this schedule yet.",
        )

    value = float(metrics.get(metric_name) or 0.0)
    triggered = (value > threshold) if comparison == "gt" else (value < threshold)
    op_word = "above" if comparison == "gt" else "below"
    message = (
        f"{metric_name} = {value:g} ({op_word} threshold {threshold:g})" if triggered
        else f"{metric_name} = {value:g} (within threshold)"
    )
    return AlertTrigger(
        str(rule.id), rule.name, rule.type, rule.severity, triggered, round(value, 4), threshold,
        message, [schedule_id] if triggered else [],
    )
```

- [ ] **Step 5: Update the three call sites**

In `evaluate_rule` (around line 578):
```python
    if rule.type == "stream_metric":
        return await eval_stream_metric(db, rule)
```

In `stream_alerts` (around line 672-683), rename the parameter and query key:
```python
async def stream_alerts(db: AsyncSession, org_id: uuid.UUID, schedule_id: uuid.UUID) -> list[AlertTrigger]:
    """Enabled stream_metric rules targeting this schedule, evaluated live -
    used by sync_schedules.py's list/snapshot endpoints to attach alarm state."""
    result = await db.execute(
        select(AlertRule).where(
            AlertRule.org_id == org_id,
            AlertRule.enabled.is_(True),
            AlertRule.type == "stream_metric",
            AlertRule.config["schedule_id"].as_string() == str(schedule_id),
        )
    )
    return [await eval_stream_metric(db, r) for r in result.scalars().all()]
```

In `stream_alerts_bulk` (around line 686-705):
```python
async def stream_alerts_bulk(
    db: AsyncSession, org_id: uuid.UUID, schedule_ids: list[uuid.UUID],
) -> dict[str, list[AlertTrigger]]:
    """stream_alerts for N schedules in one query - used by sync_schedules.py's
    list_schedules so listing schedules isn't a query per schedule."""
    if not schedule_ids:
        return {}
    sids = [str(s) for s in schedule_ids]
    result = await db.execute(
        select(AlertRule).where(
            AlertRule.org_id == org_id,
            AlertRule.enabled.is_(True),
            AlertRule.type == "stream_metric",
            AlertRule.config["schedule_id"].as_string().in_(sids),
        )
    )
    by_schedule: dict[str, list[AlertTrigger]] = {}
    for rule in result.scalars().all():
        by_schedule.setdefault(rule.config.get("schedule_id"), []).append(await eval_stream_metric(db, rule))
    return by_schedule
```

- [ ] **Step 6: Run the updated tests to verify they pass**

Run: `cd backend && pytest tests/test_alerts.py -k stream_metric -v`
Expected: PASS

- [ ] **Step 7: Run the full alerts test file to check for regressions from the signature change**

Run: `cd backend && pytest tests/test_alerts.py -v`
Expected: PASS (all tests, not just the stream_metric ones — `evaluate_rule`'s other branches are untouched but confirm nothing else called `eval_stream_metric` synchronously)

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/alerts.py backend/tests/test_alerts.py
git commit -m "feat: evaluate stream_metric alerts from SyncTick instead of realtime_cache"
```

---

### Task 10: `sync_schedules` API routes

**Files:**
- Create: `backend/app/api/routes/sync_schedules.py`
- Test: `backend/tests/test_sync_schedules.py` (new — this is the rewrite of `backend/tests/test_realtime.py`; read that file first for the test patterns/helpers to carry forward, e.g. `_register_and_login`, `_h`, `fake_rest_server` — the old file itself is deleted in Task 12, not here, so both can coexist during this task)

**Interfaces:**
- Produces: FastAPI router at prefix `/sync-schedules` with routes: `POST /sync-schedules`, `GET /sync-schedules`, `GET /sync-schedules/{id}/snapshot`, `GET /sync-schedules/{id}/history`, `POST /sync-schedules/{id}/start`, `POST /sync-schedules/{id}/stop`, `DELETE /sync-schedules/{id}`. No SSE/`/events` route.
- Consumes: `app.services.sync_scheduler.{add_job, remove_job, reschedule_job}` (Task 6), `app.services.alerts.{stream_alerts, stream_alerts_bulk}` (Task 9, now schedule-keyed), `app.models.sync_schedule.SyncSchedule` / `app.models.sync_tick.SyncTick` (Task 5).

- [ ] **Step 1: Write the failing tests**

Read `backend/tests/test_realtime.py` in full first (for `_register_and_login`, `_h`, `fake_rest_server`, `_create_rest_integration` helper shapes), then write:

```python
# backend/tests/test_sync_schedules.py
from __future__ import annotations

import uuid

import pytest

PASSWORD = "correct-horse-battery-staple1"  # pragma: allowlist secret


async def _register_and_login(client, email="owner@example.com", org_name="Acme"):
    reg = await client.post("/auth/register", json={"org_name": org_name, "email": email, "password": PASSWORD})
    assert reg.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["access_token"]


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def _create_postgres_integration(client, headers):
    res = await client.post(
        "/integrations", headers=headers,
        json={
            "name": "Cloud DB", "type": "postgres",
            "config": {"host": "db.example.com", "database": "prod", "username": "u", "password": "p", "query": "SELECT * FROM ticks"},
        },
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


@pytest.mark.asyncio
async def test_create_schedule_registers_it_and_returns_snapshot(client, monkeypatch):
    from app.services import sync_scheduler
    added = []
    monkeypatch.setattr(sync_scheduler, "add_job", lambda sid, interval: added.append((sid, interval)))

    token = await _register_and_login(client)
    headers = _h(token)
    integration_id = await _create_postgres_integration(client, headers)

    res = await client.post(
        "/sync-schedules", headers=headers,
        json={
            "integration_id": integration_id, "name": "Line 1", "interval_seconds": 300,
            "cursor_column": "ts", "column_map": {"units_col": "units"},
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] in ("stopped", "not_synced_yet")
    assert len(added) == 1


@pytest.mark.asyncio
async def test_create_schedule_rejects_connector_without_incremental_support(client):
    token = await _register_and_login(client)
    headers = _h(token)
    integ = await client.post(
        "/integrations", headers=headers,
        json={"name": "MQTT feed", "type": "mqtt", "config": {"broker": "test", "topic": "sensors/#"}},
    )
    assert integ.status_code == 201
    integration_id = integ.json()["id"]

    res = await client.post(
        "/sync-schedules", headers=headers,
        json={"integration_id": integration_id, "name": "Bad", "interval_seconds": 300, "cursor_column": "ts"},
    )
    assert res.status_code == 400
    assert "does not support" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_list_schedules_attaches_alerts_to_the_right_schedule(client, monkeypatch):
    from app.services import sync_scheduler
    monkeypatch.setattr(sync_scheduler, "add_job", lambda *a, **kw: None)

    token = await _register_and_login(client)
    headers = _h(token)
    integration_id = await _create_postgres_integration(client, headers)

    a = (await client.post(
        "/sync-schedules", headers=headers,
        json={"integration_id": integration_id, "name": "A", "interval_seconds": 300, "cursor_column": "ts"},
    )).json()["schedule_id"]
    b = (await client.post(
        "/sync-schedules", headers=headers,
        json={"integration_id": integration_id, "name": "B", "interval_seconds": 300, "cursor_column": "ts"},
    )).json()["schedule_id"]

    rule = await client.post(
        "/alerts/rules", headers=headers,
        json={
            "name": "A watch", "type": "stream_metric",
            "config": {"schedule_id": a, "metric_name": "throughput", "comparison": "gt", "threshold": -1},
        },
    )
    assert rule.status_code == 201, rule.text

    listed = (await client.get("/sync-schedules", headers=headers)).json()["schedules"]
    by_id = {s["schedule_id"]: s for s in listed}
    assert len(by_id[a]["alerts"]) == 1
    assert by_id[b]["alerts"] == []


@pytest.mark.asyncio
async def test_stop_then_start_reregisters_job(client, monkeypatch):
    from app.services import sync_scheduler
    calls = []
    monkeypatch.setattr(sync_scheduler, "add_job", lambda sid, interval: calls.append(("add", sid)))
    monkeypatch.setattr(sync_scheduler, "remove_job", lambda sid: calls.append(("remove", sid)))

    token = await _register_and_login(client)
    headers = _h(token)
    integration_id = await _create_postgres_integration(client, headers)
    schedule_id = (await client.post(
        "/sync-schedules", headers=headers,
        json={"integration_id": integration_id, "name": "Line 1", "interval_seconds": 300, "cursor_column": "ts"},
    )).json()["schedule_id"]

    stop = await client.post(f"/sync-schedules/{schedule_id}/stop", headers=headers)
    assert stop.json()["status"] == "stopped"
    start = await client.post(f"/sync-schedules/{schedule_id}/start", headers=headers)
    assert start.status_code == 200

    kinds = [c[0] for c in calls]
    assert kinds.count("add") == 2  # once on create, once on start
    assert kinds.count("remove") == 1  # once on stop


@pytest.mark.asyncio
async def test_delete_schedule_removes_job_and_definition(client, monkeypatch):
    from app.services import sync_scheduler
    monkeypatch.setattr(sync_scheduler, "add_job", lambda *a, **kw: None)
    removed = []
    monkeypatch.setattr(sync_scheduler, "remove_job", lambda sid: removed.append(sid))

    token = await _register_and_login(client)
    headers = _h(token)
    integration_id = await _create_postgres_integration(client, headers)
    schedule_id = (await client.post(
        "/sync-schedules", headers=headers,
        json={"integration_id": integration_id, "name": "Line 1", "interval_seconds": 300, "cursor_column": "ts"},
    )).json()["schedule_id"]

    delete = await client.delete(f"/sync-schedules/{schedule_id}", headers=headers)
    assert delete.status_code == 204
    assert len(removed) == 1

    missing = await client.get(f"/sync-schedules/{schedule_id}/snapshot", headers=headers)
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_cannot_access_another_orgs_schedule(client, monkeypatch):
    from app.services import sync_scheduler
    monkeypatch.setattr(sync_scheduler, "add_job", lambda *a, **kw: None)

    token_a = await _register_and_login(client, email="a@example.com", org_name="OrgA")
    headers_a = _h(token_a)
    integration_id = await _create_postgres_integration(client, headers_a)
    schedule_id = (await client.post(
        "/sync-schedules", headers=headers_a,
        json={"integration_id": integration_id, "name": "Line 1", "interval_seconds": 300, "cursor_column": "ts"},
    )).json()["schedule_id"]

    token_b = await _register_and_login(client, email="b@example.com", org_name="OrgB")
    headers_b = _h(token_b)
    res = await client.get(f"/sync-schedules/{schedule_id}/snapshot", headers=headers_b)
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_history_returns_persisted_ticks(client, monkeypatch, db_session):
    from app.services import sync_scheduler
    monkeypatch.setattr(sync_scheduler, "add_job", lambda *a, **kw: None)

    token = await _register_and_login(client)
    headers = _h(token)
    integration_id = await _create_postgres_integration(client, headers)
    schedule_id = (await client.post(
        "/sync-schedules", headers=headers,
        json={"integration_id": integration_id, "name": "Line 1", "interval_seconds": 300, "cursor_column": "ts"},
    )).json()["schedule_id"]

    from app.models.sync_tick import SyncTick
    from app.models.tenant import Org
    org_id = (await client.get("/orgs/me", headers=headers)).json()["id"]
    db_session.add(SyncTick(schedule_id=uuid.UUID(schedule_id), org_id=uuid.UUID(org_id), row_count=2, metrics={"throughput": 4.0}))
    await db_session.commit()

    history = await client.get(f"/sync-schedules/{schedule_id}/history", headers=headers)
    assert history.status_code == 200
    assert len(history.json()["points"]) == 1
    assert history.json()["points"][0]["metrics"]["throughput"] == 4.0
```

If `db_session` and `client` in the same test don't share a transaction/connection cleanly (check how other existing tests that mix both, if any, handle this — search `grep -rn "db_session, client\|client, db_session" backend/tests/` — if none exist, insert the `SyncTick` via a second API call path instead, e.g. by monkeypatching `sync_scheduler.run_sync` to run once synchronously against a fake connector rather than reaching into the DB directly).

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && pytest tests/test_sync_schedules.py -v`
Expected: FAIL — route `/sync-schedules` doesn't exist (404s), or import errors once the router is referenced in Step 5's `main.py` change (not yet made) — for now expect plain 404s since nothing is registered.

- [ ] **Step 3: Create `app/api/routes/sync_schedules.py`**

```python
"""Scheduled cloud-DB sync API — periodically pulls new rows from an
Integration's connector on an interval, computing OEE metrics (and, where
configured, CBM sensor scoring / AutoML live scoring) over each batch.
Replaces app.api.routes.realtime's SSE-based live-stream API — see
docs/superpowers/specs/2026-08-04-scheduled-cloud-sync-design.md.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_or_404
from app.db.session import get_db
from app.middleware.audit import log_event
from app.models.integration import Integration
from app.models.sync_schedule import SyncSchedule
from app.models.sync_tick import SyncTick
from app.services import alerts as alerts_service
from app.services import sync_scheduler

router = APIRouter(prefix="/sync-schedules", tags=["sync-schedules"])

# Connector types whose fetch_since() supports incremental cursor-based sync.
# Keep in sync with app.services.connectors.__init__.VALID_TYPES additions.
_INCREMENTAL_TYPES = {"sqlserver", "oracle", "postgres", "mysql"}


class ScheduleCreate(BaseModel):
    integration_id: uuid.UUID
    name: str = ""
    interval_seconds: int = Field(300, ge=60, le=86400)
    cursor_column: str
    column_map: dict[str, str] = Field(default_factory=dict)
    machine: str | None = None
    sensor_map: dict[str, str] = Field(default_factory=dict)


def _snapshot_dict(schedule: SyncSchedule, latest_tick: SyncTick | None, alerts: list) -> dict:
    if latest_tick is None:
        snap = {
            "ts": None, "schedule_id": str(schedule.id), "name": schedule.name,
            "status": "stopped" if schedule.desired_status == "stopped" else "not_synced_yet",
            "error": schedule.last_error,
            "row_count": 0, "metrics": {},
        }
    else:
        snap = {
            "ts": latest_tick.ts.isoformat(), "schedule_id": str(schedule.id), "name": schedule.name,
            "status": "stopped" if schedule.desired_status == "stopped" else "running",
            "error": schedule.last_error,
            "row_count": latest_tick.row_count, "metrics": latest_tick.metrics,
        }
    snap["alerts"] = [vars(a) for a in alerts]
    return snap


async def _latest_tick(db: AsyncSession, schedule_id: uuid.UUID) -> SyncTick | None:
    return (
        await db.execute(
            select(SyncTick).where(SyncTick.schedule_id == schedule_id).order_by(SyncTick.ts.desc()).limit(1)
        )
    ).scalar_one_or_none()


async def _snapshot_for(db: AsyncSession, org_id: uuid.UUID, schedule: SyncSchedule) -> dict:
    tick = await _latest_tick(db, schedule.id)
    alerts = await alerts_service.stream_alerts(db, org_id, schedule.id)
    return _snapshot_dict(schedule, tick, alerts)


@router.post("")
async def create_schedule(
    req: ScheduleCreate, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    integration = await get_or_404(db, Integration, current.org_id, req.integration_id, "Integration not found")
    if integration.type not in _INCREMENTAL_TYPES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Connector type {integration.type!r} does not support incremental sync "
            f"(supported: {sorted(_INCREMENTAL_TYPES)})",
        )

    schedule = SyncSchedule(
        org_id=current.org_id,
        integration_id=integration.id,
        name=req.name or integration.name,
        column_map=req.column_map,
        machine=req.machine,
        sensor_map=req.sensor_map,
        interval_seconds=req.interval_seconds,
        cursor_column=req.cursor_column,
        desired_status="running",
        created_by=current.id,
    )
    db.add(schedule)
    await log_event(
        db, action="sync_schedule.created", org_id=current.org_id, actor_user_id=current.id,
        metadata={"name": schedule.name, "integration_id": str(integration.id)},
    )
    await db.commit()
    await db.refresh(schedule)

    sync_scheduler.add_job(schedule.id, schedule.interval_seconds)
    return await _snapshot_for(db, current.org_id, schedule)


@router.get("")
async def list_schedules(current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(select(SyncSchedule).where(SyncSchedule.org_id == current.org_id).order_by(SyncSchedule.name))
    ).scalars().all()
    alerts_by_schedule = await alerts_service.stream_alerts_bulk(db, current.org_id, [s.id for s in rows])
    snapshots = []
    for s in rows:
        tick = await _latest_tick(db, s.id)
        snapshots.append(_snapshot_dict(s, tick, alerts_by_schedule.get(str(s.id), [])))
    return {"schedules": snapshots}


@router.get("/{schedule_id}/snapshot")
async def schedule_snapshot(
    schedule_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    schedule = await get_or_404(db, SyncSchedule, current.org_id, schedule_id, "Schedule not found")
    return await _snapshot_for(db, current.org_id, schedule)


@router.get("/{schedule_id}/history")
async def schedule_history(
    schedule_id: uuid.UUID,
    limit: int = 2000,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await get_or_404(db, SyncSchedule, current.org_id, schedule_id, "Schedule not found")
    limit = max(1, min(20_000, limit))
    ticks = (
        await db.execute(
            select(SyncTick).where(SyncTick.schedule_id == schedule_id)
            .order_by(SyncTick.ts.desc()).limit(limit)
        )
    ).scalars().all()
    points = [{"ts": t.ts.isoformat(), "row_count": t.row_count, "metrics": t.metrics} for t in reversed(ticks)]
    return {"schedule_id": str(schedule_id), "points": points}


@router.post("/{schedule_id}/start")
async def start_schedule(
    schedule_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    schedule = await get_or_404(db, SyncSchedule, current.org_id, schedule_id, "Schedule not found")
    schedule.desired_status = "running"
    schedule.last_error = None
    await db.commit()

    sync_scheduler.add_job(schedule.id, schedule.interval_seconds)
    return await _snapshot_for(db, current.org_id, schedule)


@router.post("/{schedule_id}/stop")
async def stop_schedule(
    schedule_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    schedule = await get_or_404(db, SyncSchedule, current.org_id, schedule_id, "Schedule not found")
    sync_scheduler.remove_job(schedule.id)
    schedule.desired_status = "stopped"
    await db.commit()
    return await _snapshot_for(db, current.org_id, schedule)


@router.delete("/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(
    schedule_id: uuid.UUID, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    schedule = await get_or_404(db, SyncSchedule, current.org_id, schedule_id, "Schedule not found")
    sync_scheduler.remove_job(schedule.id)
    await db.delete(schedule)
    await log_event(db, action="sync_schedule.deleted", org_id=current.org_id, actor_user_id=current.id, target=str(schedule_id))
    await db.commit()
```

Route paths above already match this codebase's convention for bare list/create routes — confirmed against `app/api/routes/integrations.py`, which uses `@router.get("")`/`@router.post("")` (not `"/"`) under its own prefix, avoiding a trailing-slash redirect.

- [ ] **Step 4: Run to verify tests still fail (router not registered yet)**

Run: `cd backend && pytest tests/test_sync_schedules.py -v`
Expected: FAIL with 404s (router exists as a module but isn't included in `app.main` yet — that's Task 11).

Skip ahead to Task 11 now, then return here.

- [ ] **Step 5: (after Task 11) Run to verify tests pass**

Run: `cd backend && pytest tests/test_sync_schedules.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/routes/sync_schedules.py backend/tests/test_sync_schedules.py
git commit -m "feat: add sync-schedules API routes (poll-based, no SSE)"
```

---

### Task 11: Wire `app.main` — scheduler lifespan, router swap

**Files:**
- Modify: `backend/app/main.py`

**Interfaces:**
- Consumes: `app.services.sync_scheduler.{start_scheduler, stop_scheduler}` (Task 6), `app.api.routes.sync_schedules` (Task 10).

- [ ] **Step 1: Update imports**

In `backend/app/main.py`, replace:
```python
from app.api.routes import (
    ai_insights,
    admin,
    alerts,
    automl,
    auth,
    bi,
    boms,
    cbm,
    computer_vision,
    customers,
    data_chat,
    datasets,
    device_tokens,
    downtime,
    downtime_tracking,
    forecasts,
    integrations,
    inventory,
    inventory_analytics,
    items,
    kpi_dashboards,
    kpis,
    locations,
    lots,
    machines,
    mfg_forecast,
    oauth,
    oee,
    operator_capture,
    operators,
    orgs,
    pace_tracking,
    planning_grid,
    plant_summary,
    predictive,
    process_mining,
    purchase_orders,
    quality,
    rca_cases,
    realtime,
    reports,
    routings,
    sales_orders,
    scrap,
    shifts,
    simulation,
    spc,
    staff_auth,
    suppliers,
    throughput,
    work_centers,
    work_orders,
    workflows,
)
```
with (`realtime` → `sync_schedules`, keeping alphabetical order):
```python
from app.api.routes import (
    ai_insights,
    admin,
    alerts,
    automl,
    auth,
    bi,
    boms,
    cbm,
    computer_vision,
    customers,
    data_chat,
    datasets,
    device_tokens,
    downtime,
    downtime_tracking,
    forecasts,
    integrations,
    inventory,
    inventory_analytics,
    items,
    kpi_dashboards,
    kpis,
    locations,
    lots,
    machines,
    mfg_forecast,
    oauth,
    oee,
    operator_capture,
    operators,
    orgs,
    pace_tracking,
    planning_grid,
    plant_summary,
    predictive,
    process_mining,
    purchase_orders,
    quality,
    rca_cases,
    reports,
    routings,
    sales_orders,
    scrap,
    shifts,
    simulation,
    spc,
    staff_auth,
    suppliers,
    sync_schedules,
    throughput,
    work_centers,
    work_orders,
    workflows,
)
```

Replace:
```python
from app.services import automl_realtime as automl_realtime_service
from app.services import realtime as realtime_service
from app.services import realtime_cache
from app.services import sensor_consumer
```
with:
```python
from app.services import sync_scheduler
```
(`automl_realtime` is no longer imported here — it has no more standalone startup/shutdown lifecycle of its own, since Task 8 removed its background-thread registry entirely; it's called inline from `sync_scheduler` now.)

- [ ] **Step 2: Update the lifespan hook**

Replace:
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Re-adopt any realtime stream that was running before this process
    # started (redeploy/restart/crash) - see app.services.realtime.
    await realtime_service.resume_all_streams()
    # Start this replica's realtime_cache consumer - see its module
    # docstring for why every replica needs its own consumer group.
    await realtime_cache.start_cache_consumer()
    # Same re-adoption for live AutoML scoring/drift bindings - see
    # app.services.automl_realtime.
    await automl_realtime_service.resume_all_bindings()
    await sensor_consumer.start_sensor_consumer()
    yield
    automl_realtime_service.stop_all_binding_schedulers()
    await sensor_consumer.stop_sensor_consumer()
    await realtime_cache.stop_cache_consumer()
    realtime_service.stop_all_runners()
```
with:
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Re-adopt any sync schedule that was running before this process
    # started (redeploy/restart/crash), and start the process-wide
    # scheduler - see app.services.sync_scheduler. CBM sensor scoring and
    # AutoML live scoring/drift-check both ride along inside each scheduled
    # sync's own job body now, so they have no separate startup/shutdown
    # step of their own.
    await sync_scheduler.start_scheduler()
    yield
    await sync_scheduler.stop_scheduler()
```

- [ ] **Step 3: Swap the router registration**

Replace:
```python
app.include_router(integrations.router)
app.include_router(realtime.router)
```
with:
```python
app.include_router(integrations.router)
app.include_router(sync_schedules.router)
```

- [ ] **Step 4: Sanity-check the app imports cleanly**

Run: `cd backend && python -c "from app.main import app; print('ok')"`
Expected: prints `ok` with no import errors. (This will still fail until Task 12 deletes `app/services/realtime.py` etc. only if something else still imports them at module load time — at this point in the plan those modules still exist untouched and unimported by `main.py`, so this should already pass. If it doesn't, something still references the old modules; grep for `from app.services import realtime` / `from app.services import realtime_cache` / `from app.services import sensor_consumer` across `backend/app` and fix the stray import before continuing.)

- [ ] **Step 5: Go back and finish Task 10's Steps 5–6 now (the sync-schedules route tests)**

Run: `cd backend && pytest tests/test_sync_schedules.py -v`
Expected: PASS

- [ ] **Step 6: Run the full backend test suite once, expecting known-broken old-pipeline tests**

Run: `cd backend && pytest -q`
Expected: the suite runs; `tests/test_realtime.py`, `tests/test_realtime_cache.py`, `tests/test_realtime_cache_external.py`, `tests/test_realtime_producer.py`, and `tests/test_sensor_consumer.py` (old version) are now broken/failing because their imports (`app.services.realtime_cache`, `streaming.schemas`, etc.) still exist as modules but the routes/lifespan they exercised are gone — that's expected and gets resolved in Task 12's deletion sweep, not before. Every other test file should still be green; if something unrelated broke, stop and fix it before Task 12.

- [ ] **Step 7: Commit**

```bash
git add backend/app/main.py
git commit -m "feat: wire sync_scheduler into app lifespan, replace realtime router"
```

---

### Task 12: Delete the old pipeline

**Files:**
- Delete: `backend/app/services/realtime.py`, `backend/app/services/realtime_cache.py`, `backend/app/services/sensor_consumer.py`, `backend/app/models/realtime.py`, `backend/app/api/routes/realtime.py`
- Delete: `backend/streaming/` (everything remaining — `realtime_metrics_job.py`, `schemas.py`, `flink.Dockerfile`, `requirements.txt`, `test_infra_smoke.py`, `test_pipeline_e2e.py`, `test_schemas.py`; `metrics.py`/`test_metrics.py` already left in Task 1)
- Delete: `backend/tests/test_realtime.py`, `backend/tests/test_realtime_cache.py`, `backend/tests/test_realtime_cache_external.py`, `backend/tests/test_realtime_producer.py`, `backend/tests/test_sensor_consumer.py` (the old Kafka-tick-driven version — its replacement landed in Task 7)
- Modify: `backend/tests/conftest.py` (drop the `realtime` model import)
- Modify: `backend/requirements.txt` (remove `kafka-python`, `aiokafka`)
- Modify: `docker-compose.yml` (remove `redpanda`, `flink-jobmanager`, `flink-taskmanager` services and `REDPANDA_BROKERS`)
- Modify: `backend/app/core/config.py` (remove `redpanda_brokers` setting)

- [ ] **Step 1: Confirm nothing still imports the modules being deleted**

Run: `cd backend && grep -rln "app.services.realtime\b\|app\.services\.realtime_cache\|app\.services\.sensor_consumer\|app\.models\.realtime\|from streaming\." app backend/streaming tests | grep -v -E "realtime_cache_external|test_realtime_cache\.py|test_realtime_producer\.py|test_realtime\.py|test_sensor_consumer\.py|/realtime\.py$|/realtime_cache\.py$|/sensor_consumer\.py$"`

Expected: empty output (everything left referencing these is one of the files this task is about to delete anyway). If anything else shows up, fix that reference before deleting.

- [ ] **Step 2: Delete the backend service/model/route files**

```bash
rm backend/app/services/realtime.py backend/app/services/realtime_cache.py backend/app/services/sensor_consumer.py backend/app/models/realtime.py backend/app/api/routes/realtime.py
```

- [ ] **Step 3: Delete the rest of `backend/streaming/`**

```bash
rm -rf backend/streaming
```

- [ ] **Step 4: Delete the obsolete test files**

```bash
rm backend/tests/test_realtime.py backend/tests/test_realtime_cache.py backend/tests/test_realtime_cache_external.py backend/tests/test_realtime_producer.py backend/tests/test_sensor_consumer.py
```

- [ ] **Step 5: Drop the `realtime` model import from `conftest.py`**

In `backend/tests/conftest.py`, remove `realtime,` from the `from app.models import (...)` block (it was left in place through Task 5 deliberately; now the module it points to no longer exists).

- [ ] **Step 6: Remove the Kafka dependencies**

In `backend/requirements.txt`, delete:
```
# Realtime streaming: Redpanda (Kafka-protocol broker) + Flink pipeline
kafka-python==3.0.8
aiokafka==0.11.0
```

- [ ] **Step 7: Remove Redpanda/Flink services from `docker-compose.yml`**

Delete the `redpanda`, `flink-jobmanager`, and `flink-taskmanager` service blocks entirely. In the `backend` service's `environment`, delete the `REDPANDA_BROKERS: redpanda:9092` line. In the `backend` service's `depends_on`, if `redpanda` appears there, remove it too (check — the block read in this plan's research didn't show `depends_on` for `backend` extending past `db`/`redis`, but re-read the current file before editing since Tasks 1–11 didn't touch it).

- [ ] **Step 8: Remove `redpanda_brokers` from settings**

In `backend/app/core/config.py`, delete:
```python
    # docker-compose's redpanda service only publishes its external listener
    # on 19092 to the host (9092 is internal-only, for other containers) -
    # this default matches running the backend on the host against compose.
    redpanda_brokers: str = "localhost:19092"
```

- [ ] **Step 9: Run the full backend test suite**

Run: `cd backend && pytest -q`
Expected: PASS, no failures, no collection errors (in particular, no leftover `ModuleNotFoundError: No module named 'streaming'` — grep once more per Step 1's pattern if anything surfaces).

- [ ] **Step 10: Sanity-check `docker-compose config` parses cleanly**

Run: `docker compose config -q`
Expected: no error output.

- [ ] **Step 11: Commit**

```bash
git add -A backend/app/services backend/app/models backend/app/api/routes backend/streaming backend/tests backend/requirements.txt backend/app/core/config.py docker-compose.yml
git commit -m "chore: remove Redpanda/Flink live-streaming pipeline"
```

---

### Task 13: Frontend — drop SSE, poll the new API

**Files:**
- Delete: `frontend/src/lib/sse.ts`
- Modify: `frontend/src/pages/Realtime.tsx`

**Interfaces:**
- Consumes: `GET /sync-schedules`, `GET /sync-schedules/{id}/snapshot`, `GET /sync-schedules/{id}/history`, `POST /sync-schedules`, `POST /sync-schedules/{id}/start`, `POST /sync-schedules/{id}/stop`, `DELETE /sync-schedules/{id}` (Task 10).

- [ ] **Step 1: Delete the SSE helper**

```bash
rm frontend/src/lib/sse.ts
```

- [ ] **Step 2: Rewrite `Realtime.tsx`**

Replace the full file contents:

```tsx
import { useState } from "react";
import { Radio } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import { PageHeader } from "@/components/PageHeader";
import { EmptyState } from "@/components/EmptyState";
import { Field } from "@/components/Field";
import { STAT_TONES, StatCard } from "@/components/StatCard";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { Dialog } from "@/components/ui/dialog";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";

interface IntegrationOption {
  id: string;
  name: string;
  type: string;
}

interface ScheduleSnapshot {
  ts: string | null;
  schedule_id: string;
  name: string;
  status: "running" | "error" | "stopped" | "not_synced_yet";
  error: string | null;
  row_count: number;
  metrics: Record<string, number>;
}

const STATUS_VARIANT: Record<string, "success" | "warning" | "danger" | "neutral"> = {
  running: "success",
  error: "danger",
  stopped: "neutral",
  not_synced_yet: "warning",
};

// The subset of Integration types whose connector implements fetch_since() -
// keep this in sync with app/api/routes/sync_schedules.py's _INCREMENTAL_TYPES.
const INCREMENTAL_TYPES = new Set(["sqlserver", "oracle", "postgres", "mysql"]);

// column_map keys app/services/metrics_calc.py's compute_window_metrics reads.
const COLUMN_MAP_FIELDS: { key: string; label: string }[] = [
  { key: "units_col", label: "Units column" },
  { key: "total_units_col", label: "Total units column" },
  { key: "scrap_col", label: "Scrap column" },
  { key: "downtime_col", label: "Downtime (min) column" },
  { key: "planned_time_col", label: "Planned time column" },
  { key: "operating_time_col", label: "Operating time column" },
  { key: "good_units_col", label: "Good units column" },
  { key: "target_rate_col", label: "Target rate column (optional)" },
];

function emptyForm() {
  return {
    integration_id: "",
    name: "",
    machine: "",
    interval_seconds: 300,
    cursor_column: "",
    columnMap: Object.fromEntries(COLUMN_MAP_FIELDS.map((f) => [f.key, ""])) as Record<string, string>,
    sensorMap: [] as { name: string; field: string }[],
  };
}

function LiveMetrics({ scheduleId, initial }: { scheduleId: string; initial: ScheduleSnapshot }) {
  const { data: snap } = useQuery<ScheduleSnapshot>({
    queryKey: ["sync-schedule-snapshot", scheduleId],
    queryFn: async () => {
      const res = await apiFetch(`/sync-schedules/${scheduleId}/snapshot`);
      if (!res.ok) throw new Error("Failed to load snapshot");
      return res.json();
    },
    initialData: initial,
    refetchInterval: 30_000, // polling replaces the old SSE push - see docs/superpowers/specs/2026-08-04-scheduled-cloud-sync-design.md
  });

  const current = snap ?? initial;
  const metricEntries = Object.entries(current.metrics);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <Badge variant={STATUS_VARIANT[current.status] ?? "neutral"}>{current.status.replace(/_/g, " ")}</Badge>
        {current.error && <span className="text-sm text-destructive">{current.error}</span>}
      </div>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard label="Rows in last sync" value={current.row_count} tone="primary" />
        <StatCard label="Last synced" value={current.ts ? new Date(current.ts).toLocaleTimeString() : "—"} tone="accent" />
      </div>
      {metricEntries.length > 0 && (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {metricEntries.map(([key, value], i) => (
            <StatCard
              key={key}
              label={key.replace(/_/g, " ")}
              value={value}
              tone={STAT_TONES[i % STAT_TONES.length]}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export function Realtime() {
  const qc = useQueryClient();
  const [formOpen, setFormOpen] = useState(false);
  const [form, setForm] = useState(emptyForm());
  const [formError, setFormError] = useState<string | null>(null);
  const [liveFor, setLiveFor] = useState<ScheduleSnapshot | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const integrations = useQuery<IntegrationOption[]>({
    queryKey: ["integrations"],
    queryFn: async () => {
      const res = await apiFetch("/integrations");
      if (!res.ok) throw new Error("Failed to load integrations");
      return res.json();
    },
  });
  const incrementalIntegrations = integrations.data?.filter((i) => INCREMENTAL_TYPES.has(i.type)) ?? [];

  const schedules = useQuery<{ schedules: ScheduleSnapshot[] }>({
    queryKey: ["sync-schedules"],
    queryFn: async () => {
      const res = await apiFetch("/sync-schedules");
      if (!res.ok) throw new Error("Failed to load schedules");
      return res.json();
    },
    refetchInterval: 30_000,
  });

  const create = useMutation({
    mutationFn: async () => {
      const column_map = Object.fromEntries(Object.entries(form.columnMap).filter(([, v]) => v.trim()));
      const sensor_map = Object.fromEntries(
        form.sensorMap.filter((s) => s.name.trim() && s.field.trim()).map((s) => [s.name.trim(), s.field.trim()]),
      );
      const res = await apiFetch("/sync-schedules", {
        method: "POST",
        body: JSON.stringify({
          integration_id: form.integration_id,
          name: form.name,
          machine: form.machine.trim() || null,
          interval_seconds: form.interval_seconds,
          cursor_column: form.cursor_column,
          column_map,
          sensor_map,
        }),
      });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Failed to create schedule");
      return res.json();
    },
    onSuccess: () => {
      setFormOpen(false);
      setForm(emptyForm());
      setFormError(null);
      qc.invalidateQueries({ queryKey: ["sync-schedules"] });
    },
    onError: (e: Error) => setFormError(e.message),
  });

  const toggle = useMutation({
    mutationFn: async (schedule: ScheduleSnapshot) => {
      const action = schedule.status === "stopped" ? "start" : "stop";
      const res = await apiFetch(`/sync-schedules/${schedule.schedule_id}/${action}`, { method: "POST" });
      if (!res.ok) throw new Error("Request failed");
      return res.json();
    },
    onMutate: (schedule) => setBusyId(schedule.schedule_id),
    onSettled: () => {
      setBusyId(null);
      qc.invalidateQueries({ queryKey: ["sync-schedules"] });
    },
  });

  const remove = useMutation({
    mutationFn: async (scheduleId: string) => {
      const res = await apiFetch(`/sync-schedules/${scheduleId}`, { method: "DELETE" });
      if (!res.ok) throw new Error("Failed to delete schedule");
    },
    onSettled: () => {
      setBusyId(null);
      qc.invalidateQueries({ queryKey: ["sync-schedules"] });
    },
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Scheduled sync"
        description="Pull new rows from a cloud database integration on an interval and roll up throughput/scrap/OEE metrics from each batch."
        actions={<Button onClick={() => setFormOpen(true)}>New schedule</Button>}
      />

      {schedules.data && schedules.data.schedules.length === 0 ? (
        <EmptyState
          icon={<Radio />}
          title="No scheduled syncs yet"
          description="Pick a cloud database integration and sync it on an interval to see rolled-up metrics."
        />
      ) : (
        <TableWrap>
          <Table>
            <THead>
              <TR>
                <TH>Name</TH>
                <TH>Status</TH>
                <TH>Rows (last sync)</TH>
                <TH>Last synced</TH>
                <TH />
              </TR>
            </THead>
            <TBody>
              {schedules.data?.schedules.map((s) => (
                <TR key={s.schedule_id}>
                  <TD className="font-medium">{s.name}</TD>
                  <TD>
                    <Badge variant={STATUS_VARIANT[s.status] ?? "neutral"}>{s.status.replace(/_/g, " ")}</Badge>
                  </TD>
                  <TD className="text-muted-foreground">{s.row_count}</TD>
                  <TD className="text-muted-foreground">{s.ts ? new Date(s.ts).toLocaleTimeString() : "—"}</TD>
                  <TD className="text-right">
                    <div className="flex justify-end gap-2">
                      <Button variant="secondary" size="sm" onClick={() => setLiveFor(s)}>
                        View details
                      </Button>
                      <Button
                        variant="secondary"
                        size="sm"
                        loading={busyId === s.schedule_id && toggle.isPending}
                        onClick={() => toggle.mutate(s)}
                      >
                        {s.status === "stopped" ? "Start" : "Stop"}
                      </Button>
                      <Button
                        variant="destructive"
                        size="sm"
                        loading={busyId === s.schedule_id && remove.isPending}
                        onClick={() => {
                          setBusyId(s.schedule_id);
                          remove.mutate(s.schedule_id);
                        }}
                      >
                        Delete
                      </Button>
                    </div>
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </TableWrap>
      )}

      <Dialog open={formOpen} onClose={() => setFormOpen(false)} title="New scheduled sync">
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
        >
          <Field label="Integration" hint="Only cloud-database integrations that support incremental sync are listed.">
            <Select
              value={form.integration_id}
              onChange={(e) => setForm((f) => ({ ...f, integration_id: e.target.value }))}
              required
            >
              <option value="">Select an integration…</option>
              {incrementalIntegrations.map((i) => (
                <option key={i.id} value={i.id}>{i.name}</option>
              ))}
            </Select>
          </Field>
          <Field label="Name">
            <Input value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
          </Field>
          <Field label="Sync interval (seconds)" hint="Minimum 60 seconds — this runs on a schedule, not a live poll loop.">
            <Input
              type="number"
              min={60}
              max={86400}
              value={form.interval_seconds}
              onChange={(e) => setForm((f) => ({ ...f, interval_seconds: Number(e.target.value) }))}
            />
          </Field>
          <Field label="Cursor column" hint="A timestamp or auto-increment id column in your source query — used to fetch only new rows each sync.">
            <Input
              value={form.cursor_column}
              onChange={(e) => setForm((f) => ({ ...f, cursor_column: e.target.value }))}
              required
            />
          </Field>
          <Field label="Column map" hint="Map your source's column names to the metrics engine reads. Leave blank to skip a metric.">
            <div className="grid grid-cols-2 gap-3">
              {COLUMN_MAP_FIELDS.map((f) => (
                <Input
                  key={f.key}
                  placeholder={f.label}
                  value={form.columnMap[f.key]}
                  onChange={(e) =>
                    setForm((prev) => ({ ...prev, columnMap: { ...prev.columnMap, [f.key]: e.target.value } }))
                  }
                />
              ))}
            </div>
          </Field>
          <Field label="Machine (for CBM, optional)" hint="Set this to feed synced sensor data into Predictive Maintenance's condition-based monitoring.">
            <Input value={form.machine} onChange={(e) => setForm((f) => ({ ...f, machine: e.target.value }))} />
          </Field>
          {form.machine && (
            <Field
              label="Sensor map"
              hint="Sensor name → field to read from each synced row."
            >
              <div className="space-y-2">
                {form.sensorMap.map((s, i) => (
                  <div key={i} className="flex gap-2">
                    <Input
                      placeholder="Sensor name (e.g. vib_x)"
                      value={s.name}
                      onChange={(e) =>
                        setForm((f) => ({
                          ...f,
                          sensorMap: f.sensorMap.map((row, j) => (j === i ? { ...row, name: e.target.value } : row)),
                        }))
                      }
                    />
                    <Input
                      placeholder="Source field name"
                      value={s.field}
                      onChange={(e) =>
                        setForm((f) => ({
                          ...f,
                          sensorMap: f.sensorMap.map((row, j) => (j === i ? { ...row, field: e.target.value } : row)),
                        }))
                      }
                    />
                    <Button
                      type="button" variant="secondary" size="sm"
                      onClick={() => setForm((f) => ({ ...f, sensorMap: f.sensorMap.filter((_, j) => j !== i) }))}
                    >
                      Remove
                    </Button>
                  </div>
                ))}
                <Button
                  type="button" variant="secondary" size="sm"
                  onClick={() => setForm((f) => ({ ...f, sensorMap: [...f.sensorMap, { name: "", field: "" }] }))}
                >
                  Add sensor
                </Button>
              </div>
            </Field>
          )}
          {formError && <p className="text-sm text-destructive">{formError}</p>}
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="secondary" onClick={() => setFormOpen(false)}>Cancel</Button>
            <Button type="submit" loading={create.isPending}>Create</Button>
          </div>
        </form>
      </Dialog>

      <Dialog open={liveFor !== null} onClose={() => setLiveFor(null)} title={`Details — ${liveFor?.name ?? ""}`} className="max-w-2xl">
        {liveFor && <LiveMetrics scheduleId={liveFor.schedule_id} initial={liveFor} />}
      </Dialog>
    </div>
  );
}
```

Check `frontend/src/api/client.ts`'s `apiFetch` signature and `IntegrationOption`'s real shape (whether `/integrations` actually returns a `type` field) before assuming the `type` field used for `INCREMENTAL_TYPES` filtering above exists on the response — if it doesn't, add it there or adjust this filter to fetch it another way.

- [ ] **Step 3: Type-check and build the frontend**

Run: `cd frontend && npm run build`
Expected: no TypeScript errors, build succeeds. (`App.tsx`'s route registration for `Realtime` needs no change — same component export name, same import path.)

- [ ] **Step 4: Manual verification in the browser**

Start the dev server and confirm the golden path works end to end:
1. `preview_start` with the frontend dev server config (or `npm run dev` if no `.claude/launch.json` entry exists yet — add one first).
2. Navigate to the Realtime/Scheduled sync page.
3. Create a Postgres/MySQL/SQL Server/Oracle integration (or use a fixture one), then create a schedule against it with a cursor column.
4. Confirm the schedule appears in the list with `not_synced_yet` status, "Start"/"Stop"/"Delete" work, and opening "View details" polls `/sync-schedules/{id}/snapshot` every 30s (check the Network tab) instead of any `EventSource`/fetch-stream call.
5. Screenshot the list view and the details dialog as proof.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/Realtime.tsx frontend/src/lib/sse.ts
git commit -m "feat: rewrite Realtime page to poll scheduled-sync REST API instead of SSE"
```

---

### Task 14: Full regression pass

**Files:** none new — verification only.

- [ ] **Step 1: Full backend suite**

Run: `cd backend && pytest -q`
Expected: PASS, 0 failures, 0 errors.

- [ ] **Step 2: Backend lint**

Run: `cd backend && ruff check .`
Expected: no violations (in particular: no unused imports left behind from the deletions in Task 12, no `# noqa` needed for anything new).

- [ ] **Step 3: Frontend build + lint**

Run: `cd frontend && npm run build && npm run lint` (use whatever the actual lint script name is in `frontend/package.json` — check first if `lint` isn't it)
Expected: both succeed.

- [ ] **Step 4: Grep sweep for stray references**

Run: `grep -rln "realtime_streams\|RealtimeStream\|TickProducer\|realtime_cache\|kafka\|aiokafka\|redpanda\|flink" backend/app frontend/src docker-compose.yml docker-compose.override.yml 2>/dev/null | grep -v -i "node_modules"`
Expected: empty, or only hits inside files this plan deliberately left alone (e.g. unrelated docs/specs referencing the old design historically — read anything that shows up before deciding it's fine).

- [ ] **Step 5: Confirm the design doc and this plan are both committed**

Run: `git log --oneline -1 -- docs/superpowers/specs/2026-08-04-scheduled-cloud-sync-design.md docs/superpowers/plans/2026-08-04-scheduled-cloud-sync.md`
Expected: both show a commit.

- [ ] **Step 6: Final commit if anything from Steps 1-4 needed fixing**

```bash
git add -A
git commit -m "chore: fix lint/regression fallout from scheduled cloud-DB sync migration"
```
(Skip this step entirely if Steps 1-4 found nothing to fix.)
