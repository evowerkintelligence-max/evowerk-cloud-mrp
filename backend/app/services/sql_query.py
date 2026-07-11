"""Free-text SQL over a single uploaded dataset — the desktop app's raw SQL
box, ported with a safety model the desktop app didn't need (single-tenant,
local file access was fine there; here every query runs against shared
infrastructure).

Every other SQL-building path in this codebase (see datasets.py, dataset_query.py)
allow-lists column names before qi()-quoting them - there is no free text.
This module is the one place that breaks that rule, so it closes the gap with
three independent layers instead:

  1. Statement shape: con.extract_statements() rejects anything but exactly
     one SELECT statement (blocks ATTACH/COPY/CREATE/DROP/PRAGMA/stacked
     statements).
  2. Filesystem/network lockdown: the dataset is materialized into a real
     TEMP TABLE (not a view - views re-evaluate read_csv_auto per query and
     break once external access is off), then enable_external_access=false
     blocks read_csv_auto/read_parquet/httpfs/COPY from inside the query.
  3. A wall-clock timeout (DuckDB has no native statement_timeout) via a
     watchdog thread calling con.interrupt(), plus a hard row cap. The budget
     covers materializing the dataset too, not just the final SELECT - both
     run inside the same worker thread so a slow/huge dataset can't block the
     event loop or run unbounded before the query itself even starts.

Errors are deliberately generic to the client — the real duckdb.Error is
logged server-side only, since a bad query here is entirely user-authored
input and DuckDB's error text can reference internal structure.
"""
from __future__ import annotations

import asyncio
import logging
import threading

import duckdb
from fastapi import HTTPException, status

from app.models.dataset import Dataset
from app.services import datasets as ds_service

logger = logging.getLogger(__name__)

_MAX_ROW_LIMIT = 5000


def _validate_single_select(con: duckdb.DuckDBPyConnection, sql: str) -> None:
    try:
        statements = con.extract_statements(sql)
    except duckdb.Error as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Query failed: invalid SQL") from exc
    if len(statements) != 1 or statements[0].type != duckdb.StatementType.SELECT:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only a single SELECT statement is allowed")


def _execute_with_timeout(con: duckdb.DuckDBPyConnection, statements: list[str], timeout_seconds: float) -> list:
    """Run `statements` in order on a worker thread, interrupting `con` if the
    whole sequence outruns the timeout. Only the last statement's result set
    is returned - the earlier ones are setup (materializing the dataset,
    locking down external access), run here rather than on the caller's
    coroutine so they don't block the event loop and so they share the same
    wall-clock budget as the query they're setting up for. Runs synchronously
    (called via asyncio.to_thread by run_sql)."""
    result: dict = {}

    def _work() -> None:
        try:
            cur = None
            for stmt in statements:
                cur = con.execute(stmt)
            result["rows"] = cur.fetchall()
            result["columns"] = [d[0] for d in (cur.description or [])]
        except Exception as exc:  # noqa: BLE001 - must never leave `result` without "error"/"rows", see caller
            result["error"] = exc

    worker = threading.Thread(target=_work, daemon=True)
    worker.start()
    worker.join(timeout_seconds)
    if worker.is_alive():
        con.interrupt()
        worker.join()
        raise HTTPException(status.HTTP_408_REQUEST_TIMEOUT, "Query timed out")
    if "error" in result:
        logger.warning("sql_query failed: %s", result["error"])
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Query failed: invalid SQL or unsupported operation")
    return [result["columns"], result["rows"]]


async def run_sql(
    dataset: Dataset,
    sql: str,
    *,
    timeout_seconds: float = 10.0,
    row_limit: int = 500,
) -> dict:
    row_limit = max(1, min(row_limit, _MAX_ROW_LIMIT))
    rel = ds_service.dataset_relation(dataset)
    con = ds_service.open_con()
    try:
        # Statement shape is checked before paying for materialization below -
        # extract_statements() only parses `sql`, it doesn't need `dataset` to
        # exist yet.
        _validate_single_select(con, sql)
        # extract_statements() confirmed exactly one statement above, so the only
        # semicolon that can appear is a single trailing terminator - strip it
        # before embedding the query in a subquery (a mid-query ';' would have
        # split into multiple statements and already been rejected).
        inner = sql.strip()
        if inner.endswith(";"):
            inner = inner[:-1].strip()
        # nosec B608 - this is the one place in the codebase that embeds raw,
        # not-column-allow-listed user text into SQL (that's the point of a
        # free-text SQL feature). Safety here comes from the three layers
        # above instead of allow-listing: extract_statements() just confirmed
        # `inner` is a single SELECT statement, enable_external_access=false
        # blocks filesystem/network reads from inside it, and the caller
        # wraps execution in a timeout. See the module docstring.
        wrapped = f"SELECT * FROM (\n{inner}\n) AS _q LIMIT {row_limit}"  # nosec B608

        # nosec B608 - rel is the server-built, quote-escaped relation string
        # from dataset_relation(); no client input reaches this line.
        materialize = f"CREATE TEMP TABLE dataset AS SELECT * FROM {rel}"  # nosec B608
        statements = [materialize, "SET enable_external_access=false", wrapped]

        columns, rows = await asyncio.to_thread(_execute_with_timeout, con, statements, timeout_seconds)
        return {
            "columns": columns,
            "rows": [{n: ds_service.json_safe(v) for n, v in zip(columns, row)} for row in rows],
        }
    finally:
        con.close()
