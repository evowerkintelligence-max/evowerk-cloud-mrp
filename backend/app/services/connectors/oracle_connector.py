"""Query Oracle Database via SQLAlchemy + oracledb (thin mode). Only reachable
if the customer's Oracle instance is network-accessible from this server."""
from __future__ import annotations
from typing import Any

try:
    from sqlalchemy import create_engine, text
    _HAS_SA = True
except ImportError:
    _HAS_SA = False

# oracledb (thin mode, no Oracle Client required) is the modern driver
try:
    import oracledb  # noqa: F401
    _HAS_ORACLE = True
    _ORACLE_PKG = "oracledb"
except ImportError:
    try:
        import cx_Oracle  # noqa: F401
        _HAS_ORACLE = True
        _ORACLE_PKG = "cx_Oracle"
    except ImportError:
        _HAS_ORACLE = False
        _ORACLE_PKG = None


class OracleConnector:
    def __init__(self, config: dict[str, Any]):
        self.host = str(config.get("host", "127.0.0.1")).strip()
        self.port = int(config.get("port", 1521))
        self.service_name = str(config.get("service_name", "")).strip()
        self.username = str(config.get("username", "")).strip()
        self.password = str(config.get("password", "")).strip()
        self.query = str(config.get("query", "")).strip()

        if not self.service_name:
            raise ValueError("service_name is required for Oracle connector")
        if not self.query:
            raise ValueError("query is required for Oracle connector")

    def _check_deps(self):
        if not _HAS_SA:
            raise RuntimeError("sqlalchemy is required. Install with: pip install sqlalchemy")
        if not _HAS_ORACLE:
            raise RuntimeError(
                "oracledb is required (no Oracle Client needed). Install with: pip install oracledb"
            )

    def _engine(self):
        self._check_deps()
        from urllib.parse import quote_plus
        dsn = f"{self.host}:{self.port}/{self.service_name}"
        user = quote_plus(self.username)
        pwd = quote_plus(self.password)
        if _ORACLE_PKG == "oracledb":
            # Thin mode (pure Python, no Oracle Instant Client required) is the
            # default in python-oracledb. Do NOT call init_oracle_client() —
            # that forces THICK mode and fails without the client libs.
            url = f"oracle+oracledb://{user}:{pwd}@{dsn}"
        else:
            url = f"oracle+cx_oracle://{user}:{pwd}@{dsn}"

        return create_engine(url, pool_pre_ping=True, pool_size=1, max_overflow=0)

    def test(self) -> dict[str, Any]:
        eng = self._engine()
        with eng.connect() as conn:
            conn.execute(text("SELECT 1 FROM DUAL"))
        eng.dispose()
        return {"ok": True, "message": f"Connected to {self.host}/{self.service_name}"}

    def preview(self, n: int = 10) -> list[dict[str, Any]]:
        eng = self._engine()
        limited = f"SELECT * FROM ({self.query}) WHERE ROWNUM <= {n}"
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
