"""The 8 workflow step runners. Every step type reuses an existing service
instead of a new query/ingest engine:

  - sql              -> app.services.sql_query.run_sql (against a stored dataset)
  - filter/aggregate -> app.services.dataset_query.run_query when reading a
                         stored dataset, or a small in-memory helper below
                         when chained off a previous step's rows
  - load_dataset      -> app.services.datasets.preview_rows
  - import_csv/xlsx/json -> app.services.datasets.create_dataset
  - export_result     -> stdlib csv/json over a previous step's rows

A step either reads a stored dataset (config.dataset_id) or the output of an
upstream step (config.input_step, must be one of the step's depends_on) -
never both.
"""
from __future__ import annotations

import base64
import csv
import io
import json
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable

import pandas as pd
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import dataset_query as query_service
from app.services import datasets as ds_service
from app.services import sql_query as sql_query_service


@dataclass
class StepContext:
    db: AsyncSession
    org_id: uuid.UUID
    created_by: uuid.UUID
    config: dict
    # step_id -> that step's output dict, for every step this one depends on.
    upstream: dict[str, dict]


class StepError(RuntimeError):
    """A step failed for a reason worth surfacing verbatim to the run's event
    log (as opposed to an unexpected exception, which the worker logs and
    reports generically)."""


def _upstream_rows(ctx: StepContext) -> list[dict]:
    input_step = ctx.config.get("input_step")
    if not input_step or input_step not in ctx.upstream:
        raise StepError("config.input_step must name a step this one depends_on")
    return ctx.upstream[input_step].get("rows", [])


def _apply_filters_py(rows: list[dict], filters: list[dict]) -> list[dict]:
    def _matches(row: dict, f: dict) -> bool:
        value = row.get(f["column"])
        target = f.get("value")
        op = f["op"]
        if op == "eq":
            return str(value) == str(target)
        if op == "neq":
            return str(value) != str(target)
        if op == "contains":
            return target is not None and str(target).lower() in str(value or "").lower()
        if op == "in":
            return str(value) in {str(v) for v in (target or [])}
        try:
            lhs, rhs = float(value), float(target)
        except (TypeError, ValueError):
            return False
        return {"gt": lhs > rhs, "gte": lhs >= rhs, "lt": lhs < rhs, "lte": lhs <= rhs}.get(op, False)

    return [r for r in rows if all(_matches(r, f) for f in filters)]


# sum uses min_count=1 so an all-missing group aggregates to NaN (-> None
# below) instead of pandas' default empty-sum-is-0; avg/min/max already do
# this by default.
_AGG_FN = {"sum": lambda s: s.sum(min_count=1), "avg": "mean", "min": "min", "max": "max"}


def _aggregate_py(rows: list[dict], group_by: list[str], select: list[dict]) -> list[dict]:
    """Group + aggregate a previous step's in-memory rows. pandas is already a
    dependency in this codebase (dataset ingest, dataframe_for) - reused here
    instead of hand-rolled dict bucketing for the same job."""
    if not rows:
        return []
    df = pd.DataFrame(rows)
    named: dict[str, pd.NamedAgg] = {}
    for field in select:
        col, agg = field["column"], field.get("agg")
        alias = field.get("alias") or (f"{agg}_{col}" if agg else col)
        if agg == "count":
            named[alias] = pd.NamedAgg(column=col, aggfunc="size")
        else:
            df[col] = pd.to_numeric(df[col], errors="coerce")
            named[alias] = pd.NamedAgg(column=col, aggfunc=_AGG_FN.get(agg or "sum", "sum"))

    grouped = df.groupby(group_by, sort=False) if group_by else df.assign(_all=0).groupby("_all")
    result = grouped.agg(**named).reset_index(drop=not group_by)
    records = result.astype(object).where(pd.notnull(result), None).to_dict("records")
    # .item() unwraps numpy scalars (int64/float64) to native Python types -
    # str/None pass through untouched since they have no .item() method.
    return [{k: (v.item() if hasattr(v, "item") else v) for k, v in row.items()} for row in records]


async def _run_sql(ctx: StepContext) -> dict:
    dataset_id = ctx.config.get("dataset_id")
    if not dataset_id:
        raise StepError("sql step needs config.dataset_id")
    sql = ctx.config.get("sql")
    if not sql or not str(sql).strip():
        raise StepError("sql step needs a non-empty config.sql")
    dataset = await ds_service.resolve_dataset(ctx.db, ctx.org_id, dataset_id)
    return await sql_query_service.run_sql(dataset, sql, row_limit=int(ctx.config.get("limit", 500)))


async def _run_filter(ctx: StepContext) -> dict:
    filters = ctx.config.get("filters", [])
    dataset_id = ctx.config.get("dataset_id")
    if dataset_id:
        dataset = await ds_service.resolve_dataset(ctx.db, ctx.org_id, dataset_id)
        cols = [c["name"] for c in dataset.columns]
        req = query_service.DatasetQueryRequest(
            select=[query_service.SelectField(column=c) for c in cols],
            filters=[query_service.QueryFilter(**f) for f in filters],
            limit=int(ctx.config.get("limit", 500)),
        )
        return await query_service.run_query(ctx.db, dataset, ctx.org_id, req)
    rows = _apply_filters_py(_upstream_rows(ctx), filters)
    return {"columns": list(rows[0].keys()) if rows else [], "rows": rows}


async def _run_aggregate(ctx: StepContext) -> dict:
    group_by = ctx.config.get("group_by", [])
    select = ctx.config.get("select", [])
    if not select:
        raise StepError("aggregate step needs at least one config.select entry")
    dataset_id = ctx.config.get("dataset_id")
    if dataset_id:
        dataset = await ds_service.resolve_dataset(ctx.db, ctx.org_id, dataset_id)
        req = query_service.DatasetQueryRequest(
            select=[query_service.SelectField(**s) for s in select],
            group_by=group_by,
            filters=[query_service.QueryFilter(**f) for f in ctx.config.get("filters", [])],
            limit=int(ctx.config.get("limit", 500)),
        )
        return await query_service.run_query(ctx.db, dataset, ctx.org_id, req)
    rows = _aggregate_py(_upstream_rows(ctx), group_by, select)
    return {"columns": list(rows[0].keys()) if rows else [], "rows": rows}


async def _run_load_dataset(ctx: StepContext) -> dict:
    dataset_id = ctx.config.get("dataset_id")
    if not dataset_id:
        raise StepError("load_dataset step needs config.dataset_id")
    dataset = await ds_service.resolve_dataset(ctx.db, ctx.org_id, dataset_id)
    rows = ds_service.preview_rows(dataset, limit=int(ctx.config.get("limit", 5000)))
    return {"columns": [c["name"] for c in dataset.columns], "rows": rows}


async def _run_import(ext: str, ctx: StepContext) -> dict:
    content_b64 = ctx.config.get("content_base64")
    if not content_b64:
        raise StepError(f"{ext} import step needs config.content_base64")
    filename = ctx.config.get("filename") or f"import.{ext}"
    try:
        data = base64.b64decode(content_b64)
    except (ValueError, TypeError) as exc:
        raise StepError("config.content_base64 is not valid base64") from exc
    try:
        dataset = await ds_service.create_dataset(
            ctx.db,
            org_id=ctx.org_id,
            created_by=ctx.created_by,
            name=ctx.config.get("name"),
            filename=filename,
            data=data,
        )
    except HTTPException as exc:
        raise StepError(str(exc.detail)) from exc
    return {"dataset_id": str(dataset.id), "row_count": dataset.row_count, "columns": [c["name"] for c in dataset.columns]}


async def _run_export_result(ctx: StepContext) -> dict:
    rows = _upstream_rows(ctx)
    fmt = ctx.config.get("format", "csv")
    if fmt not in ("csv", "json"):
        raise StepError("export_result config.format must be 'csv' or 'json'")
    if fmt == "json":
        content = json.dumps(rows, default=str)
    else:
        buf = io.StringIO()
        if rows:
            writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        content = buf.getvalue()
    return {"format": fmt, "content": content, "row_count": len(rows)}


STEP_RUNNERS: dict[str, Callable[[StepContext], Awaitable[dict]]] = {
    "sql": _run_sql,
    "filter": _run_filter,
    "aggregate": _run_aggregate,
    "load_dataset": _run_load_dataset,
    "import_csv": lambda ctx: _run_import("csv", ctx),
    "import_excel": lambda ctx: _run_import("xlsx", ctx),
    "import_json": lambda ctx: _run_import("json", ctx),
    "export_result": _run_export_result,
}


async def run_step(step_type: str, ctx: StepContext) -> dict:
    runner = STEP_RUNNERS.get(step_type)
    if runner is None:
        raise StepError(f"Unknown step type: {step_type!r}")
    return await runner(ctx)
