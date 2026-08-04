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
                    f"SELECT * FROM ({base}) AS _cursor WHERE {cursor_column} > :cursor_value "  # nosec B608 - cursor_column is admin-picked from preview() output, not request-time user input
                    f"ORDER BY {cursor_column} LIMIT {max_rows}"
                )
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
