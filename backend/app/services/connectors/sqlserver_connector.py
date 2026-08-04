"""Query SQL Server via SQLAlchemy + pyodbc. Only reachable if the customer's
SQL Server instance is network-accessible from this server (firewall
allow-list, VPN, or public endpoint) — same model as any cloud BI tool's
"connect to your database" feature."""
from __future__ import annotations
from typing import Any

try:
    from sqlalchemy import create_engine, text
    _HAS_SA = True
except ImportError:
    _HAS_SA = False

try:
    import pyodbc  # noqa: F401
    _HAS_PYODBC = True
except ImportError:
    _HAS_PYODBC = False


def _detect_mssql_driver() -> str:
    """Pick the best available ODBC driver for SQL Server."""
    if not _HAS_PYODBC:
        return "ODBC Driver 17 for SQL Server"
    drivers = [d for d in pyodbc.drivers() if "SQL Server" in d]
    for preferred in ["ODBC Driver 18", "ODBC Driver 17", "ODBC Driver 13", "SQL Server"]:
        for d in drivers:
            if preferred in d:
                return d
    return drivers[0] if drivers else "ODBC Driver 17 for SQL Server"


class SqlServerConnector:
    def __init__(self, config: dict[str, Any]):
        self.host = str(config.get("host", "127.0.0.1")).strip()
        self.port = int(config.get("port", 1433))
        self.database = str(config.get("database", "")).strip()
        self.username = str(config.get("username", "")).strip()
        self.password = str(config.get("password", "")).strip()
        self.query = str(config.get("query", "")).strip()
        self.driver = str(config.get("driver", "")).strip() or _detect_mssql_driver()
        self.trust_cert = bool(config.get("trust_server_certificate", True))

        if not self.database:
            raise ValueError("database is required for SQL Server connector")
        if not self.query:
            raise ValueError("query is required for SQL Server connector")

    def _check_deps(self):
        if not _HAS_SA:
            raise RuntimeError("sqlalchemy is required. Install with: pip install sqlalchemy")
        if not _HAS_PYODBC:
            raise RuntimeError("pyodbc is required. Install with: pip install pyodbc")

    def _engine(self):
        self._check_deps()
        from urllib.parse import quote_plus
        trust = "yes" if self.trust_cert else "no"
        conn_str = (
            f"DRIVER={{{self.driver}}};"
            f"SERVER={self.host},{self.port};"
            f"DATABASE={self.database};"
            f"UID={self.username};"
            f"PWD={self.password};"
            f"TrustServerCertificate={trust};"
        )
        url = f"mssql+pyodbc:///?odbc_connect={quote_plus(conn_str)}"
        return create_engine(url, pool_pre_ping=True, pool_size=1, max_overflow=0)

    def test(self) -> dict[str, Any]:
        eng = self._engine()
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        eng.dispose()
        return {"ok": True, "message": f"Connected to {self.host}/{self.database}"}

    def preview(self, n: int = 10) -> list[dict[str, Any]]:
        eng = self._engine()
        # nosec B608 - self.query is admin-configured connector config, not per-request
        # user input; it's already executed unwrapped in iter_rows/fetch_all below, so
        # this preview wrapper grants no privilege the connector doesn't already have.
        limited = f"SELECT TOP {n} * FROM ({self.query}) AS _preview"  # nosec B608
        with eng.connect() as conn:
            result = conn.execute(text(limited))
            cols = list(result.keys())
            rows = [dict(zip(cols, row)) for row in result.fetchall()]
        eng.dispose()
        return rows

    def iter_rows(self, max_rows: int = 500_000):
        """Stream rows via a server-side cursor (stream_results) — constant memory
        regardless of result size. Essential for large industrial tables."""
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
