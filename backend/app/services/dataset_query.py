"""Ad-hoc query layer over an uploaded dataset, with an optional join to the
caller's org Items (by a column the user designates as matching Item.sku).

Every column name that reaches SQL is validated against the dataset's sniffed
schema (or the small fixed set of joinable Item columns below) before it is
identifier-quoted (datasets.qi) - that allow-list is the entire SQL-injection
boundary for this feature. There is no free-text SQL entry anywhere.
"""
from __future__ import annotations

import uuid
from typing import Literal

import duckdb
from fastapi import HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bom import Item
from app.models.dataset import Dataset
from app.services import datasets as ds_service
from app.services.inventory_analytics import _on_hand_by_item

AggFn = Literal["sum", "avg", "min", "max", "count"]
FilterOp = Literal["eq", "neq", "gt", "gte", "lt", "lte", "contains", "in"]

# The only join target in v1 (see Phase 5 scope): items.<col> in a select/
# group_by/filter/order_by column refers to one of these, joined by
# join_items_on = dataset.sku.
ITEM_JOIN_COLUMNS = {"sku", "name", "item_type", "lead_time_days", "on_hand"}
_AGG_SQL = {"sum": "SUM", "avg": "AVG", "min": "MIN", "max": "MAX", "count": "COUNT"}
_OP_SQL = {"eq": "=", "neq": "!=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}


class SelectField(BaseModel):
    column: str
    agg: AggFn | None = None
    alias: str | None = None


class QueryFilter(BaseModel):
    column: str
    op: FilterOp
    value: str | float | list[str] | None = None


class DatasetQueryRequest(BaseModel):
    select: list[SelectField]
    group_by: list[str] = []
    filters: list[QueryFilter] = []
    join_items_on: str | None = None
    order_by: str | None = None
    order_desc: bool = False
    limit: int = 500

    @field_validator("limit")
    @classmethod
    def _cap_limit(cls, v: int) -> int:
        return max(1, min(v, 5000))

    @field_validator("select")
    @classmethod
    def _require_select(cls, v: list[SelectField]) -> list[SelectField]:
        if not v:
            raise ValueError("select must include at least one column")
        return v


def _column_sql(dataset: Dataset, col: str, *, join_enabled: bool) -> str:
    """Resolve a requested column to a quoted, aliased SQL reference, rejecting
    anything that isn't a real dataset header or a known joinable Item column."""
    if col.startswith("items."):
        if not join_enabled:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"'{col}' requires join_items_on to be set")
        item_col = col[len("items."):]
        if item_col not in ITEM_JOIN_COLUMNS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown items column: '{item_col}'")
        return f"i.{ds_service.qi(item_col)}"
    ds_service.validate_columns(dataset, [col])
    return f"d.{ds_service.qi(col)}"


def _filter_sql(dataset: Dataset, f: QueryFilter, *, join_enabled: bool) -> str:
    col_sql = _column_sql(dataset, f.column, join_enabled=join_enabled)
    if f.op == "in":
        if not isinstance(f.value, list) or not f.value:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"Filter on '{f.column}' needs a non-empty list value for 'in'"
            )
        return f"{col_sql} IN ({', '.join(ds_service.qs(str(v)) for v in f.value)})"
    if f.op == "contains":
        return f"CAST({col_sql} AS VARCHAR) ILIKE {ds_service.qs('%' + str(f.value) + '%')}"
    if isinstance(f.value, (int, float)):
        return f"TRY_CAST({col_sql} AS DOUBLE) {_OP_SQL[f.op]} {f.value}"
    return f"CAST({col_sql} AS VARCHAR) {_OP_SQL[f.op]} {ds_service.qs(str(f.value))}"


def _sql_literal(value: object, cast: str) -> str:
    """A type-explicit SQL literal (CAST per value, not per column) - avoids
    DuckDB's VALUES clause guessing a column's type from whichever row's NULL
    happens to come first."""
    if value is None:
        return f"NULL::{cast}"
    if isinstance(value, str):
        return f"CAST({ds_service.qs(value)} AS {cast})"
    return f"CAST({value} AS {cast})"


async def _items_relation_sql(db: AsyncSession, con: duckdb.DuckDBPyConnection, org_id: uuid.UUID) -> None:
    """Materialize the org's Items as a temp view joinable into the dataset's
    DuckDB connection, built as a plain SQL VALUES list (stdlib string
    formatting - no pandas needed just to hand DuckDB a handful of rows).
    on_hand reuses the existing lot-tracked/non-lot-tracked split (see
    inventory_analytics._on_hand_by_item) instead of re-deriving it."""
    items = (await db.execute(select(Item).where(Item.org_id == org_id))).scalars().all()
    item_ids = {i.id for i in items}
    on_hand = await _on_hand_by_item(db, org_id, item_ids) if item_ids else {}
    col_names = ("sku", "name", "item_type", "lead_time_days", "on_hand")
    col_types = ("VARCHAR", "VARCHAR", "VARCHAR", "BIGINT", "DOUBLE")
    if not items:
        con.execute(f"CREATE TEMP TABLE items ({', '.join(f'{n} {t}' for n, t in zip(col_names, col_types))})")
        return
    rows = ", ".join(
        "({}, {}, {}, {}, {})".format(
            _sql_literal(i.sku, "VARCHAR"),
            _sql_literal(i.name, "VARCHAR"),
            _sql_literal(i.item_type, "VARCHAR"),
            _sql_literal(i.lead_time_days, "BIGINT"),
            _sql_literal(float(on_hand.get(i.id, 0)), "DOUBLE"),
        )
        for i in items
    )
    # nosec B608 - col_names is the fixed ITEM_JOIN_COLUMNS tuple above, rows is
    # built from _sql_literal (typed, quote-escaped literals) - no external input here.
    con.execute(f"CREATE TEMP VIEW items AS SELECT * FROM (VALUES {rows}) AS t({', '.join(col_names)})")  # nosec B608


async def run_query(db: AsyncSession, dataset: Dataset, org_id: uuid.UUID, req: DatasetQueryRequest) -> dict:
    join_enabled = req.join_items_on is not None
    if join_enabled:
        ds_service.validate_columns(dataset, [req.join_items_on])

    select_sql: list[str] = []
    used_aliases: set[str] = set()
    for idx, f in enumerate(req.select):
        col_sql = _column_sql(dataset, f.column, join_enabled=join_enabled)
        if f.agg:
            cast = col_sql if f.agg == "count" else f"TRY_CAST({col_sql} AS DOUBLE)"
            expr = f"{_AGG_SQL[f.agg]}({cast})"
            default_alias = f"{f.agg}_{f.column.replace('.', '_')}"
        else:
            expr = col_sql
            default_alias = f.column.replace(".", "_")
        alias = f.alias or default_alias
        if alias in used_aliases:
            alias = f"{alias}_{idx}"
        used_aliases.add(alias)
        select_sql.append(f"{expr} AS {ds_service.qi(alias)}")

    # order_by may name an output alias (e.g. an aggregate like "sum_qty") -
    # the common "sort by the number I just computed" case - or a raw column.
    order_by_sql = None
    if req.order_by:
        order_by_sql = (
            ds_service.qi(req.order_by)
            if req.order_by in used_aliases
            else _column_sql(dataset, req.order_by, join_enabled=join_enabled)
        )

    group_by_sql = [_column_sql(dataset, c, join_enabled=join_enabled) for c in req.group_by]
    where_sql = [_filter_sql(dataset, f, join_enabled=join_enabled) for f in req.filters]

    rel = ds_service.dataset_relation(dataset)
    con = ds_service.open_con()
    try:
        from_sql = f"{rel} d"
        if join_enabled:
            await _items_relation_sql(db, con, org_id)
            join_col_sql = _column_sql(dataset, req.join_items_on, join_enabled=False)
            # TRIM both sides: real spreadsheets routinely have padded sku cells,
            # and an exact-match join would silently drop those rows to NULL.
            from_sql += f' LEFT JOIN items i ON TRIM(CAST({join_col_sql} AS VARCHAR)) = TRIM(i."sku")'

        sql = f"SELECT {', '.join(select_sql)} FROM {from_sql}"  # nosec B608 - select_sql/from_sql built entirely from qi()-quoted, allow-listed columns above
        if where_sql:
            sql += f" WHERE {' AND '.join(where_sql)}"
        if group_by_sql:
            sql += f" GROUP BY {', '.join(group_by_sql)}"
        if order_by_sql:
            sql += f" ORDER BY {order_by_sql} {'DESC' if req.order_desc else 'ASC'}"
        sql += f" LIMIT {req.limit}"

        try:
            cur = con.execute(sql)
        except duckdb.Error as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Query failed: {exc}") from exc
        names = [d[0] for d in (cur.description or [])]
        rows = [{n: ds_service.json_safe(v) for n, v in zip(names, row)} for row in cur.fetchall()]
        return {"columns": names, "rows": rows}
    finally:
        con.close()
