"""Report builders, custom-report section resolution and saved-report lookups."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any, Callable, Literal

from fastapi import HTTPException, status
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.api.routes.downtime import DowntimeAnalyzeRequest, analyze_downtime
from app.api.routes.oee import OeeComputeRequest, oee_compute
from app.api.routes.plant_summary import PlantSummaryRequest, plant_summary
from app.api.routes.scrap import ScrapComputeRequest, compute_scrap
from app.api.routes.shifts import ShiftAnalyzeRequest, analyze_shifts
from app.api.routes.spc import SPCRequest, analyze_spc
from app.api.routes.throughput import ThroughputComputeRequest, compute_throughput
from app.models.dashboard import Chart
from app.models.dataset import Dataset
from app.models.report_spec import SavedReport
from app.services import dataset_query as query_service
from app.services import datasets as ds_service
from app.services import rca_cases as rca_cases_service
from app.services import sql_query as sql_query_service
from app.services.reports.model import (
    KINDS,
    RETIRED_KINDS,
    Section,
    Report,
    EM_DASH,
    _num,
    _int,
    _org_name,
    _now,
)

def reject_retired_kind_sections(sections: list[ReportSectionSpec]) -> None:
    """Creation/update-time guard for saved_reports and report_inbox_items:
    a NEW spec must not be allowed to reference a retired report_kind, only
    an old one that already exists is tolerated (see RETIRED_KINDS and
    _resolve_kind_report_sections, which silently drops the section at
    render time rather than failing the whole report - the right behavior
    for a spec that predates the retirement, wrong behavior for one being
    saved today with no excuse not to know better)."""
    for section in sections:
        if section.type == "kind_report" and section.report_kind in RETIRED_KINDS:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"report_kind '{section.report_kind}' has been retired and can no longer "
                "be used in a new report section",
            )
# --------------------------------------------------------------------------- #
# Builders - one per report kind
# --------------------------------------------------------------------------- #
async def build_dataset_report(db: AsyncSession, org_id: uuid.UUID, dataset: Dataset) -> Report:
    """A report over an uploaded dataset instead of live org data - a capped
    row preview plus the same size/shape KPIs the Datasets page shows.
    Column dtypes aren't included: CSV/TSV uploads are sniffed ALL_VARCHAR
    (see services.datasets.relation_for), so a per-column type line would
    read "VARCHAR" for nearly every column and add noise, not signal.
    """
    preview_limit = 200
    preview = ds_service.preview_rows(dataset, limit=preview_limit)
    col_names = [c["name"] for c in dataset.columns]

    return Report(
        slug="dataset",
        title=dataset.name,
        subtitle=f"Uploaded dataset — {_int(dataset.row_count)} rows, {_int(len(col_names))} columns",
        org_name=await _org_name(db, org_id),
        generated_at=_now(),
        kpis=[
            ("Rows", _int(dataset.row_count)),
            ("Columns", _int(len(col_names))),
            ("Size", f"{dataset.size_bytes / 1024:,.1f} KB"),
            ("Format", dataset.fmt.upper()),
        ],
        sections=[
            Section(
                title=f"Preview (first {len(preview)} of {dataset.row_count:,} rows)",
                columns=col_names,
                rows=[
                    [EM_DASH if row.get(c) is None else str(row.get(c)) for c in col_names] for row in preview
                ],
                note=None if preview else "Dataset is empty.",
            )
        ],
        notes=[
            f"Preview capped at {preview_limit} rows - use the Datasets page to work with the full file.",
        ],
    )


# --------------------------------------------------------------------------- #
# Kind-report dispatch - shared by the standalone /reports/{kind} routes and
# the custom-report-builder "kind_report" capture type (see ReportSectionSpec
# below): both need "given a kind name and its params, build the Report."
# --------------------------------------------------------------------------- #
async def build_report_by_kind(
    db: AsyncSession,
    org_id: uuid.UUID,
    kind: str,
    *,
    horizon_start: date | None,
    horizon_end: date | None,
    dataset_id: str | None,
) -> Report:
    # kind == "dataset" - the only entry left in KINDS; validated by the caller.
    if not dataset_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "dataset reports need dataset_id")
    dataset = await ds_service.resolve_dataset(db, org_id, dataset_id)
    return await build_dataset_report(db, org_id, dataset)


# --------------------------------------------------------------------------- #
# Custom report builder - a user-composed Report from an ordered list of
# sections, saved as a SavedReport and rendered through the same
# render_csv/html/pdf below as every other report kind. Ported from the
# desktop app's reportsView.js (a list-based section builder, not drag/drop -
# see the workflow/report-builder port plan). kpi_grid is deliberately not
# included: it depends on a dedicated KPIs engine this app doesn't have yet.
# --------------------------------------------------------------------------- #
_BULLET_LABELS = {
    "findings": "Finding",
    "risks": "Risk",
    "recommendations": "Recommendation",
    "caveats": "Caveat",
}


class ReportSectionSpec(BaseModel):
    type: Literal[
        "title", "exec_summary", "table", "bi_chart",
        "findings", "risks", "recommendations", "caveats", "text",
        "engine", "kind_report", "rca_case", "sql",
    ]
    title: str | None = None
    text: str | None = None
    items: list[str] | None = None
    dataset_id: uuid.UUID | None = None
    query: query_service.DatasetQueryRequest | None = None
    chart_id: uuid.UUID | None = None
    # engine - one of the 7 analytics-engine pages (all share EngineScreen)
    engine_path: str | None = None
    engine_body: dict | None = None
    # kind_report - inventory-analytics / rca / scenario / dataset
    report_kind: str | None = None
    report_params: dict | None = None
    # rca_case - the fishbone case-management tool
    case_id: uuid.UUID | None = None
    # sql - SQL Editor's raw-SQL results (not a structured DatasetQueryRequest,
    # so it can't reuse "table")
    sql_text: str | None = None


def _cell(v: Any) -> str:
    if v is None:
        return EM_DASH
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, float):
        return _num(v)
    return str(v)


def _flatten_response_to_sections(title: str, data: dict[str, Any]) -> list[Section]:
    """Turn one analytics-engine response (already .model_dump()-ed) into
    report Sections, with no per-engine field names hardcoded: scalar fields
    join one "Summary" table; each list[dict] field becomes its own named
    table; a nested dict (e.g. OEE's revenue_impact/teep/six_big_losses) is
    flattened one level deep - its own scalars join the parent Summary under
    a prefixed label, its own list[dict] fields become their own tables.
    Empty/None fields are skipped so the Summary table isn't padded with
    "not applicable for this dataset" rows."""
    summary_rows: list[list[str]] = []
    sections: list[Section] = []

    def label(field: str) -> str:
        return field.replace("_", " ").title()

    def add_list(section_title: str, items: list) -> None:
        if not items or not isinstance(items[0], dict):
            return
        columns = list(items[0].keys())
        sections.append(Section(
            title=section_title,
            columns=[label(c) for c in columns],
            rows=[[_cell(row.get(c)) for c in columns] for row in items],
        ))

    # named "key" (not "field") - "field" is already imported from dataclasses
    # at module scope, and ruff's F402 flags a loop variable that shadows it.
    for key, value in data.items():
        if value is None:
            continue
        if isinstance(value, list):
            add_list(f"{title} — {label(key)}", value)
        elif isinstance(value, dict):
            for sub_field, sub_value in value.items():
                if sub_value is None:
                    continue
                if isinstance(sub_value, list):
                    add_list(f"{title} — {label(key)} — {label(sub_field)}", sub_value)
                elif not isinstance(sub_value, dict):
                    summary_rows.append([f"{label(key)}: {label(sub_field)}", _cell(sub_value)])
        else:
            summary_rows.append([label(key), _cell(value)])

    if summary_rows:
        sections.insert(0, Section(title=f"{title} — Summary", columns=["Metric", "Value"], rows=summary_rows))
    return sections


_ENGINE_HANDLERS: dict[str, tuple[type[BaseModel], Callable]] = {
    "/analytics/oee": (OeeComputeRequest, oee_compute),
    "/analytics/downtime": (DowntimeAnalyzeRequest, analyze_downtime),
    "/analytics/throughput": (ThroughputComputeRequest, compute_throughput),
    "/analytics/scrap": (ScrapComputeRequest, compute_scrap),
    "/analytics/shifts": (ShiftAnalyzeRequest, analyze_shifts),
    "/analytics/spc": (SPCRequest, analyze_spc),
    "/analytics/plant-summary": (PlantSummaryRequest, plant_summary),
}


async def _resolve_engine_sections(db: AsyncSession, current: CurrentUser, spec: ReportSectionSpec) -> list[Section]:
    if spec.engine_path not in _ENGINE_HANDLERS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown engine_path: {spec.engine_path}")
    request_model, handler = _ENGINE_HANDLERS[spec.engine_path]
    try:
        req = request_model.model_validate(spec.engine_body or {})
    except ValidationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid engine_body: {exc}") from exc
    response = await handler(req, current, db)
    title = spec.title or spec.engine_path.rsplit("/", 1)[-1].replace("-", " ").title()
    return _flatten_response_to_sections(title, response.model_dump())


async def _resolve_kind_report_sections(db: AsyncSession, org_id: uuid.UUID, spec: ReportSectionSpec) -> list[Section]:
    if spec.report_kind in RETIRED_KINDS:
        # A report saved before this kind was retired still carries it in its
        # stored spec. Drop just this section rather than failing the whole
        # report - the rest of the report is unaffected by one export that no
        # longer exists.
        return []
    if spec.report_kind not in KINDS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"report_kind must be one of {sorted(KINDS)}")
    params = spec.report_params or {}
    horizon_start = date.fromisoformat(params["horizon_start"]) if params.get("horizon_start") else None
    horizon_end = date.fromisoformat(params["horizon_end"]) if params.get("horizon_end") else None
    report = await build_report_by_kind(
        db, org_id, spec.report_kind,
        horizon_start=horizon_start, horizon_end=horizon_end,
        dataset_id=params.get("dataset_id"),
    )
    return report.sections


async def _resolve_rca_case_sections(db: AsyncSession, org_id: uuid.UUID, spec: ReportSectionSpec) -> list[Section]:
    if spec.case_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "'rca_case' section needs case_id")
    try:
        case = await rca_cases_service.get_case_or_404(db, org_id, spec.case_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    nodes = await rca_cases_service.list_nodes(db, org_id, spec.case_id)
    actions = await rca_cases_service.list_actions(db, org_id, spec.case_id)

    causes = Section(
        title=spec.title or f"{case.title} — Causes",
        columns=["Text", "Type", "Category", "Status"],
        rows=[[n.text, n.node_type, n.category or EM_DASH, n.status] for n in nodes] or [[EM_DASH] * 4],
    )
    actions_section = Section(
        title=f"{case.title} — Actions",
        columns=["Description", "Owner", "Status", "OEE Δ"],
        rows=[
            [
                a.description,
                a.owner or EM_DASH,
                a.status,
                _num(a.verification["delta_oee"]) if a.verification else EM_DASH,
            ]
            for a in actions
        ] or [[EM_DASH] * 4],
    )
    return [causes, actions_section]


async def _resolve_sections(db: AsyncSession, current: CurrentUser, spec: ReportSectionSpec) -> list[Section]:
    org_id = current.org_id

    if spec.type in ("title", "exec_summary", "text"):
        lines = [line for line in (spec.text or "").splitlines() if line.strip()]
        return [Section(
            title=spec.title or spec.type.replace("_", " ").title(),
            columns=["Text"],
            rows=[[line] for line in lines] or [[EM_DASH]],
        )]

    if spec.type in _BULLET_LABELS:
        return [Section(
            title=spec.title or spec.type.title(),
            columns=[_BULLET_LABELS[spec.type]],
            rows=[[item] for item in (spec.items or [])] or [[EM_DASH]],
        )]

    if spec.type == "table":
        if spec.dataset_id is None or spec.query is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"'{spec.type}' section needs dataset_id and query")
        dataset = await ds_service.get_dataset_or_404(db, org_id, spec.dataset_id)
        result = await query_service.run_query(db, dataset, org_id, spec.query)
        columns = result["columns"]
        return [Section(
            title=spec.title or dataset.name,
            columns=columns,
            rows=[[EM_DASH if r.get(c) is None else str(r.get(c)) for c in columns] for r in result["rows"]],
        )]

    if spec.type == "sql":
        if spec.dataset_id is None or not spec.sql_text:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "'sql' section needs dataset_id and sql_text")
        dataset = await ds_service.get_dataset_or_404(db, org_id, spec.dataset_id)
        result = await sql_query_service.run_sql(dataset, spec.sql_text)
        columns = result["columns"]
        return [Section(
            title=spec.title or dataset.name,
            columns=columns,
            rows=[[EM_DASH if r.get(c) is None else str(r.get(c)) for c in columns] for r in result["rows"]],
        )]

    if spec.type == "bi_chart":
        # replay a saved Chart's query, same as GET /bi/charts/{id}/data - a
        # table is the correct rendering here too, since a PDF/CSV export
        # can't embed a live chart anyway.
        if spec.chart_id is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "'bi_chart' section needs chart_id")
        chart = (
            await db.execute(select(Chart).where(Chart.id == spec.chart_id, Chart.org_id == org_id))
        ).scalar_one_or_none()
        if chart is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Chart not found: {spec.chart_id}")
        dataset = await ds_service.get_dataset_or_404(db, org_id, chart.dataset_id)
        query = query_service.DatasetQueryRequest.model_validate(chart.query_config)
        result = await query_service.run_query(db, dataset, org_id, query)
        columns = result["columns"]
        return [Section(
            title=spec.title or chart.name,
            columns=columns,
            rows=[[EM_DASH if r.get(c) is None else str(r.get(c)) for c in columns] for r in result["rows"]],
        )]

    if spec.type == "engine":
        return await _resolve_engine_sections(db, current, spec)

    if spec.type == "kind_report":
        return await _resolve_kind_report_sections(db, org_id, spec)

    # spec.type == "rca_case"
    return await _resolve_rca_case_sections(db, org_id, spec)


async def build_custom_report(
    db: AsyncSession, current: CurrentUser, name: str, sections: list[ReportSectionSpec]
) -> Report:
    resolved: list[Section] = []
    for s in sections:
        resolved.extend(await _resolve_sections(db, current, s))
    return Report(
        slug="custom",
        title=name,
        subtitle=f"{len(resolved)} section(s)",
        org_name=await _org_name(db, current.org_id),
        generated_at=_now(),
        sections=resolved,
    )


async def list_saved_reports(db: AsyncSession, org_id: uuid.UUID) -> list[SavedReport]:
    rows = await db.execute(
        select(SavedReport).where(SavedReport.org_id == org_id).order_by(SavedReport.created_at.desc())
    )
    return list(rows.scalars().all())


async def get_saved_report_or_404(db: AsyncSession, org_id: uuid.UUID, report_id: uuid.UUID) -> SavedReport:
    row = (
        await db.execute(select(SavedReport).where(SavedReport.id == report_id, SavedReport.org_id == org_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found")
    return row
