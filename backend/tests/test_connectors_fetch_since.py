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
