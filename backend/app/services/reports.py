from __future__ import annotations

import csv
import html
import io
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal

from fastapi import HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.models.bom import Item
from app.models.dashboard import Chart
from app.models.dataset import Dataset
from app.models.report_spec import SavedReport
from app.models.tenant import Org
from app.services import dataset_query as query_service
from app.services import datasets as ds_service
from app.services import llm
from app.services import sql_query as sql_query_service
from app.services.inventory_analytics import compute_inventory_analytics
from app.services.mrp import compute_mrp_plan, resolve_horizon
from app.services.rca import DIMENSIONS, RcaResult, compute_rca
from app.services.scenario import compare_lead_time_scenario

# Ported from a desktop app's reports_v2 (ReportSpec + csv/html/pdf renderers).
# Dropped on the way over: the on-disk spec store, the artifact registry, and
# the BI-widget embeds - all of that existed to snapshot a *workflow run*, and
# every source here (MRP plan, analytics, RCA, scenario) is recomputed from
# live org data on request instead. A report is therefore a pure function of
# (org, kind, params) and needs no table, no migration and no report_id: build
# a Report, render it, stream it.
#
# AI narration (ported from report_narrate.py, see narrate_report below) keeps
# that same shape: nothing is stored, a Report is narrated fresh on request
# from whatever data it just computed.
BRAND = "EvoWerk Cloud MRP"

KINDS = ("mrp-plan", "inventory-analytics", "rca", "scenario", "dataset")
FORMATS = {
    "csv": "text/csv",
    "html": "text/html; charset=utf-8",
    "pdf": "application/pdf",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

# A cell string starting with one of these opens as a live formula in Excel/
# Sheets - a dataset value like "=cmd|'/c calc'!A0" would otherwise execute on
# open. Prefixing with a quote forces it to render as inert text instead.
_FORMULA_PREFIXES = ("=", "+", "-", "@")


def _excel_safe(cell: str) -> str:
    # A leading "-" or "+" is also how a real negative/positive number renders
    # (see _num) - only quote-prefix cells that AREN'T plain numbers, so a
    # legitimate "-5.20" isn't turned into text.
    if cell.startswith(_FORMULA_PREFIXES) and not _is_numeric(cell):
        return "'" + cell
    return cell


@dataclass
class Section:
    title: str
    columns: list[str]
    rows: list[list[str]]
    # Shown under the section heading - what the table means, or why it's empty.
    note: str | None = None


@dataclass
class Report:
    slug: str
    title: str
    subtitle: str
    org_name: str
    generated_at: datetime
    # Headline figures rendered as a card strip above the tables.
    kpis: list[tuple[str, str]] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    # Methodology / caveats, printed as footnotes.
    notes: list[str] = field(default_factory=list)

    def filename(self, fmt: str) -> str:
        return f"{self.slug}-{self.generated_at:%Y%m%d}.{fmt}"


# --------------------------------------------------------------------------- #
# Value formatting
# --------------------------------------------------------------------------- #
EM_DASH = "—"


def _num(value: float | Decimal | None, dp: int = 2) -> str:
    """Thousands-separated fixed-point, or an em dash for a missing value.
    Every table cell is a string by the time it reaches a renderer, so the
    three renderers never re-implement number formatting."""
    if value is None:
        return EM_DASH
    return f"{float(value):,.{dp}f}"


def _int(value: int | None) -> str:
    return EM_DASH if value is None else f"{value:,}"


def _text(value: str | None) -> str:
    return value if value else EM_DASH


def _is_numeric(cell: str) -> bool:
    """A cell that reads as a number gets right-aligned (and tabular figures) in
    HTML and PDF. Cheaper and more robust than declaring column types up front."""
    if cell in ("", EM_DASH):
        return True
    try:
        float(cell.replace(",", "").rstrip("%").lstrip("+"))
    except ValueError:
        return False
    return True


def _numeric_columns(section: Section) -> list[bool]:
    """A column is numeric only if every one of its cells is."""
    return [
        all(_is_numeric(row[i]) for row in section.rows) if section.rows else False
        for i in range(len(section.columns))
    ]


async def _org_name(db: AsyncSession, org_id: uuid.UUID) -> str:
    return (await db.execute(select(Org.name).where(Org.id == org_id))).scalar_one()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Builders - one per report kind
# --------------------------------------------------------------------------- #
async def build_mrp_plan_report(
    db: AsyncSession, org_id: uuid.UUID, horizon_start: date, horizon_end: date
) -> Report:
    lines = await compute_mrp_plan(db, org_id, horizon_start, horizon_end)
    actions = [ln for ln in lines if ln.net_requirement > 0]
    to_order = [ln for ln in actions if not ln.is_manufactured]
    to_build = [ln for ln in actions if ln.is_manufactured]

    def action_rows(subset):
        return [
            [
                ln.item_sku,
                ln.item_name,
                "Build" if ln.is_manufactured else "Order",
                _num(ln.gross_demand),
                _num(ln.on_hand),
                _num(ln.on_order),
                _num(ln.on_draft),
                _num(ln.suggested_order_quantity),
            ]
            for ln in subset
        ]

    sections = [
        Section(
            title="Suggested actions",
            columns=["SKU", "Item", "Action", "Gross demand", "On hand", "On order", "On draft", "Quantity"],
            rows=action_rows(actions),
            note="Items with a shortfall over this horizon, largest first."
            if actions
            else "No shortfalls: forecast demand is fully covered by stock and open orders.",
        ),
        Section(
            title="Full plan",
            columns=[
                "SKU",
                "Item",
                "Type",
                "Independent",
                "Dependent",
                "Gross demand",
                "On hand",
                "On order",
                "On draft",
                "Net requirement",
            ],
            rows=[
                [
                    ln.item_sku,
                    ln.item_name,
                    "Manufactured" if ln.is_manufactured else "Purchased",
                    _num(ln.independent_demand),
                    _num(ln.dependent_demand),
                    _num(ln.gross_demand),
                    _num(ln.on_hand),
                    _num(ln.on_order),
                    _num(ln.on_draft),
                    _num(ln.net_requirement),
                ]
                for ln in lines
            ],
            note="Every item reached by a forecast or exploded onto from a parent assembly."
            if lines
            else "No forecast demand falls inside this horizon.",
        ),
    ]

    return Report(
        slug="mrp-plan",
        title="MRP Plan",
        subtitle=f"Demand horizon {horizon_start:%d %b %Y} — {horizon_end:%d %b %Y}",
        org_name=await _org_name(db, org_id),
        generated_at=_now(),
        kpis=[
            ("Planned items", _int(len(lines))),
            ("To order", _int(len(to_order))),
            ("To build", _int(len(to_build))),
            ("Total net requirement", _num(sum((ln.net_requirement for ln in lines), Decimal(0)))),
        ],
        sections=sections,
        notes=[
            "net requirement = max(0, gross demand − on hand − on order − on draft); "
            "gross demand = independent (own forecast) + dependent (exploded from parent assemblies).",
            "Explosion is netted, not gross: an assembly is netted against its own stock before its "
            "remaining requirement explodes onto its components.",
            "On-hand excludes lots on QA hold or recall. Draft purchase orders count as supply, so a "
            "shortfall already drafted does not appear as a new suggestion.",
            "Lot sizing, minimum order quantities and lead-time offsetting are not applied — quantity "
            "is the raw net requirement.",
        ],
    )


async def build_inventory_analytics_report(db: AsyncSession, org_id: uuid.UUID) -> Report:
    lines = await compute_inventory_analytics(db, org_id)
    counts: dict[str, int] = {}
    for ln in lines:
        counts[ln.status] = counts.get(ln.status, 0) + 1

    return Report(
        slug="inventory-analytics",
        title="Inventory Analytics",
        subtitle="Reorder point, safety stock, days of supply and ABC/XYZ classification",
        org_name=await _org_name(db, org_id),
        generated_at=_now(),
        kpis=[
            ("Items", _int(len(lines))),
            ("Stockout", _int(counts.get("stockout", 0))),
            ("Low", _int(counts.get("low", 0))),
            ("Excess", _int(counts.get("excess", 0))),
            ("Slow moving", _int(counts.get("slow_moving", 0))),
            ("Healthy", _int(counts.get("healthy", 0))),
        ],
        sections=[
            Section(
                title="Item positions",
                columns=[
                    "SKU",
                    "Item",
                    "Status",
                    "On hand",
                    "Avg daily demand",
                    "Demand std",
                    "Lead time (d)",
                    "Safety stock",
                    "Reorder point",
                    "Days of supply",
                    "ABC",
                    "XYZ",
                ],
                rows=[
                    [
                        ln.item_sku,
                        ln.item_name,
                        ln.status,
                        _num(ln.on_hand),
                        _num(ln.avg_daily_demand),
                        _num(ln.demand_std),
                        _int(ln.lead_time_days),
                        _num(ln.safety_stock),
                        _num(ln.reorder_point),
                        _num(ln.days_of_supply, dp=1),
                        _text(ln.abc_class),
                        _text(ln.xyz_class),
                    ]
                    for ln in lines
                ],
                note=None if lines else "No items yet.",
            )
        ],
        notes=[
            "Safety stock = z × σ_DLT at the target cycle service level; "
            "σ_DLT = √(L × σ_d² + d² × σ_L²) (APICS CPIM).",
            "Reorder point = average demand during lead time + safety stock.",
            "ABC classes items by annualised consumption value (Pareto); XYZ by demand variability "
            "(coefficient of variation).",
            "Where consumption history is too short to estimate variability, an assumed 30% "
            "coefficient of variation is used — see the demand std source column of the API.",
        ],
    )


def _rca_kpis(result: RcaResult) -> list[tuple[str, str]]:
    if result.method == "insufficient_data":
        return [("Method", "Insufficient data")]
    significant = sum(1 for v in result.values if v.is_significant)
    return [
        ("Method", "Chi-square independence"),
        ("χ² statistic", _num(result.chi2_statistic)),
        ("p-value", _num(result.p_value, dp=4)),
        ("Degrees of freedom", _int(result.degrees_of_freedom)),
        ("Significant values", _int(significant)),
        ("Association", "Yes (p < 0.05)" if (result.p_value or 1.0) < 0.05 else "Not detected"),
    ]


async def build_rca_report(db: AsyncSession, org_id: uuid.UUID, dimension: str) -> Report:
    result = await compute_rca(db, org_id, dimension)
    label = dimension.replace("_", " ")

    notes = [
        "Chi-square test of independence over a [dimension value] × [at risk, not at risk] "
        "contingency table. At risk = an item whose status is stockout or low.",
        "Standardized residual = (observed − expected) / √expected. |residual| > 1.96 marks a value "
        "contributing significantly to the association at the two-tailed 95% level.",
        "A positive residual means more at-risk items than independence predicts; negative means fewer.",
    ]
    if result.method == "insufficient_data":
        notes.append(
            "Not enough data for a valid test: fewer than two dimension values were present, or every "
            "item fell on the same side of the at-risk split."
        )
    elif result.low_expected_frequency_warning:
        notes.append(
            "Warning: at least one expected cell count is below 5. The chi-square approximation is "
            "unreliable at this sample size — treat the p-value as indicative only."
        )

    return Report(
        slug=f"rca-{dimension.replace('_', '-')}",
        title="Root Cause Analysis",
        subtitle=f"Stockout and low-stock risk by {label}",
        org_name=await _org_name(db, org_id),
        generated_at=_now(),
        kpis=_rca_kpis(result),
        sections=[
            Section(
                title=f"Findings by {label}",
                columns=[
                    label.title(),
                    "Items",
                    "At risk",
                    "Expected at risk",
                    "Std. residual",
                    "Significant",
                ],
                rows=[
                    [
                        v.value,
                        _int(v.item_count),
                        _int(v.at_risk_count),
                        _num(v.expected_at_risk_count),
                        _num(v.standardized_residual),
                        "Yes" if v.is_significant else "No",
                    ]
                    for v in result.values
                ],
                note="Ranked by |standardized residual| — biggest anomaly first."
                if result.values
                else "No findings to rank.",
            )
        ],
        notes=notes,
    )


async def build_scenario_report(
    db: AsyncSession, org_id: uuid.UUID, lead_time_overrides: dict[uuid.UUID, int]
) -> Report:
    deltas = await compare_lead_time_scenario(db, org_id, lead_time_overrides)
    counts: dict[str, int] = {}
    for d in deltas:
        counts[d.classification] = counts.get(d.classification, 0) + 1

    return Report(
        slug="scenario-comparison",
        title="Scenario Comparison",
        subtitle=f"Lead-time what-if across {len(lead_time_overrides)} item(s) — nothing was persisted",
        org_name=await _org_name(db, org_id),
        generated_at=_now(),
        kpis=[
            ("Items compared", _int(len(deltas))),
            ("Regressed", _int(counts.get("regressed", 0))),
            ("Improved", _int(counts.get("improved", 0))),
            ("Unchanged", _int(counts.get("unchanged", 0))),
        ],
        sections=[
            Section(
                title="Baseline vs scenario",
                columns=[
                    "SKU",
                    "Item",
                    "Lead time (base → scenario)",
                    "Reorder point Δ",
                    "Safety stock Δ",
                    "Days of supply Δ",
                    "Status (base → scenario)",
                    "Result",
                ],
                rows=[
                    [
                        d.item_sku,
                        d.item_name,
                        f"{_int(d.baseline_lead_time_days)} → {_int(d.scenario_lead_time_days)}",
                        _num(d.reorder_point_delta),
                        _num(d.safety_stock_delta),
                        _num(d.days_of_supply_delta, dp=1),
                        f"{d.baseline_status} → {d.scenario_status}",
                        d.classification,
                    ]
                    for d in deltas
                ],
                note="Regressions first, then improvements, ranked by |reorder point Δ| within each group."
                if deltas
                else "No comparable items: the overridden items produced no analytics rows.",
            )
        ],
        notes=[
            "Both sides are computed fresh from the same live data and differ only in the lead time fed "
            "to the reorder-point and safety-stock model. No override is written to the database.",
            "Improved / regressed compares how far each status sits from healthy — a move from excess "
            "toward healthy improves just as a move from low toward healthy does.",
            "A longer lead time raises σ_DLT, so both safety stock and reorder point rise with it.",
        ],
    )


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
async def _parse_lead_times(
    db: AsyncSession, org_id: uuid.UUID, raw: str | None
) -> dict[uuid.UUID, int]:
    """`lead_times=<item_id>:<days>,<item_id>:<days>` - the scenario report's
    overrides live in the query string so a report is one GET and downloads
    from a plain link, like every other kind."""
    if not raw or not raw.strip():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "scenario reports need lead_times=<item_id>:<days>[,<item_id>:<days>]",
        )

    overrides: dict[uuid.UUID, int] = {}
    for pair in raw.split(","):
        item_raw, sep, days_raw = pair.strip().partition(":")
        if not sep:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Malformed lead_times entry: {pair}")
        try:
            item_id = uuid.UUID(item_raw)
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid item_id: {item_raw}") from None
        try:
            days = int(days_raw)
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid lead time: {days_raw}") from None
        if days < 0:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"lead_time_days must be non-negative (item {item_raw})"
            )
        overrides[item_id] = days

    found = (
        await db.execute(select(Item.id).where(Item.org_id == org_id, Item.id.in_(overrides.keys())))
    ).scalars().all()
    missing = overrides.keys() - set(found)
    if missing:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Unknown item(s) for this org: {', '.join(str(m) for m in missing)}"
        )
    return overrides


async def build_report_by_kind(
    db: AsyncSession,
    org_id: uuid.UUID,
    kind: str,
    *,
    horizon_start: date | None,
    horizon_end: date | None,
    dimension: str,
    lead_times: str | None,
    dataset_id: str | None,
) -> Report:
    if kind == "mrp-plan":
        hs, he = resolve_horizon(horizon_start, horizon_end)
        return await build_mrp_plan_report(db, org_id, hs, he)
    if kind == "inventory-analytics":
        return await build_inventory_analytics_report(db, org_id)
    if kind == "rca":
        if dimension not in DIMENSIONS:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"dimension must be one of {sorted(DIMENSIONS)}"
            )
        return await build_rca_report(db, org_id, dimension)
    if kind == "dataset":
        if not dataset_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "dataset reports need dataset_id")
        dataset = await ds_service.resolve_dataset(db, org_id, dataset_id)
        return await build_dataset_report(db, org_id, dataset)
    # kind == "scenario" - validated by the caller against KINDS.
    return await build_scenario_report(db, org_id, await _parse_lead_times(db, org_id, lead_times))


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
    # kind_report - mrp-plan / inventory-analytics / rca / scenario / dataset
    report_kind: str | None = None
    report_params: dict | None = None
    # rca_case - the fishbone case-management tool
    case_id: uuid.UUID | None = None
    # sql - SQL Editor's raw-SQL results (not a structured DatasetQueryRequest,
    # so it can't reuse "table")
    sql_text: str | None = None


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
        return await _resolve_engine_sections(db, current, spec)  # noqa: F821 - added in Task 4

    if spec.type == "kind_report":
        return await _resolve_kind_report_sections(db, org_id, spec)  # noqa: F821 - added in Task 5

    # spec.type == "rca_case"
    return await _resolve_rca_case_sections(db, org_id, spec)  # noqa: F821 - added in Task 6


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


# --------------------------------------------------------------------------- #
# AI narration
# --------------------------------------------------------------------------- #
# Ported from the desktop app's report_narrate.py: an LLM writes the prose,
# strictly grounded in numbers this module already computed - it never sees
# raw org data, only the same Section(columns, rows) tables render_* already
# use, and it never invents a figure that isn't in one of those tables.
#
# Generalized over any Section instead of the desktop's nine per-source
# extractors (oee/downtime/shifts/...): every report here already normalizes
# down to Section, so one generic prompt covers every report kind.
NARRATION_STYLES = ("narrative", "bullets", "brief")
_MAX_NARRATION_ROWS = 40  # enough shape for a grounded narrative, not the whole table


class SectionNarrative(BaseModel):
    title: str
    narrative: str


class ReportNarration(BaseModel):
    executive_summary: str
    sections: list[SectionNarrative]
    error: str | None = None


_STYLE_INSTRUCTIONS = {
    "narrative": "Write 2-4 professional paragraphs per section for a plant manager or executive audience.",
    "bullets": 'Write 4-8 short bullet points per section, one fact per bullet, each prefixed with "• ".',
    "brief": "Write exactly one tight paragraph per section (max 3 sentences), covering only the highest-impact figures.",
}


def _narration_system(style: str) -> str:
    instruction = _STYLE_INSTRUCTIONS.get(style, _STYLE_INSTRUCTIONS["narrative"])
    return (
        "You are a professional manufacturing report writer working as a scribe. "
        "Your ONLY job is to convert the provided tables into narrative text.\n\n"
        "STRICT RULES:\n"
        "1. Every number, name, or date you write MUST come directly from the provided data.\n"
        "2. Do not invent, estimate, or add any figures not present in the data.\n"
        "3. Do not add recommendations or analysis beyond what the data explicitly shows.\n"
        f"4. {instruction}\n"
        "5. Executive summary: 1-2 sentences referencing the most significant findings across all sections.\n"
        "6. Return EXACTLY one section per input block below, in the same order, copying its title exactly.\n\n"
        'Return ONLY a JSON object: {"executive_summary": "...", '
        '"sections": [{"title": "<exact title from block>", "narrative": "..."}]}'
    )


def _section_block(section: Section) -> str:
    sample = section.rows[:_MAX_NARRATION_ROWS]
    lines = [",".join(section.columns)] + [",".join(str(c) for c in row) for row in sample]
    omitted = len(section.rows) - len(sample)
    if omitted > 0:
        lines.append(f"... ({omitted} more row(s) not shown)")
    return f"=== {section.title} ===\n" + "\n".join(lines)


def _narration_prompt(report: Report) -> str:
    parts = [f"Report: {report.title}\nGenerated: {report.generated_at:%d %B %Y}"]
    if report.kpis:
        parts.append("Headline figures: " + ", ".join(f"{label}: {value}" for label, value in report.kpis))
    parts += [_section_block(s) for s in report.sections]
    return "\n\n".join(parts)


def _fallback_narration(report: Report, error: str | None = None) -> ReportNarration:
    kpi_str = "; ".join(f"{label}: {value}" for label, value in report.kpis)
    summary = f"This report covers {len(report.sections)} section(s)."
    if kpi_str:
        summary += f" Key figures — {kpi_str}."
    summary += " AI narration was unavailable; every figure above is computed from your live data."
    return ReportNarration(
        executive_summary=summary,
        sections=[
            SectionNarrative(
                title=s.title,
                narrative=f"{s.title}: {len(s.rows)} row(s) across {len(s.columns)} column(s). See the table above for full figures.",
            )
            for s in report.sections
        ],
        error=error,
    )


async def narrate_report(report: Report, style: str = "narrative") -> ReportNarration:
    if not report.sections:
        return ReportNarration(executive_summary="This report has no sections to narrate.", sections=[])
    if not llm.is_configured():
        return _fallback_narration(report)

    try:
        parsed = await llm.generate_json(
            _narration_prompt(report), system=_narration_system(style), max_output_tokens=2500,
        )
    except Exception as exc:  # transient LLM error or malformed JSON - narrate via the deterministic path instead
        return _fallback_narration(report, error=str(exc))

    ai_sections = parsed.get("sections") or []
    by_title = {str(s.get("title")): str(s.get("narrative") or "") for s in ai_sections if isinstance(s, dict)}
    # The model was told to echo titles verbatim, but that isn't guaranteed - if
    # it dropped them entirely but returned the right count, match positionally
    # instead of silently producing zero narratives.
    by_position = (
        [s.get("narrative", "") if isinstance(s, dict) else "" for s in ai_sections]
        if not by_title and len(ai_sections) == len(report.sections) else None
    )
    fallback_by_title = {s.title: s.narrative for s in _fallback_narration(report).sections}

    sections_out = []
    for idx, section in enumerate(report.sections):
        narrative = by_title.get(section.title)
        if not narrative and by_position is not None:
            narrative = by_position[idx]
        if not narrative:
            narrative = fallback_by_title[section.title]
        sections_out.append(SectionNarrative(title=section.title, narrative=narrative))

    return ReportNarration(
        executive_summary=str(parsed.get("executive_summary") or "").strip() or _fallback_narration(report).executive_summary,
        sections=sections_out,
    )


# --------------------------------------------------------------------------- #
# Renderers
# --------------------------------------------------------------------------- #
def render_csv(report: Report) -> bytes:
    """One CSV holding every section, separated by a blank line and a titled
    header row. Spreadsheets open this fine and it keeps a multi-section report
    to a single download."""
    buf = io.StringIO(newline="")
    w = csv.writer(buf)
    w.writerow([report.title])
    w.writerow([report.subtitle])
    w.writerow([report.org_name, f"Generated {report.generated_at:%Y-%m-%d %H:%M UTC}"])

    if report.kpis:
        w.writerow([])
        w.writerow(["Summary"])
        for label, value in report.kpis:
            w.writerow([_excel_safe(label), _excel_safe(value)])

    for section in report.sections:
        w.writerow([])
        w.writerow([section.title])
        if section.note:
            w.writerow([section.note])
        w.writerow(section.columns)
        w.writerows([[_excel_safe(cell) for cell in row] for row in section.rows])

    if report.notes:
        w.writerow([])
        w.writerow(["Notes"])
        for note in report.notes:
            w.writerow([note])

    # utf-8-sig: Excel on Windows reads a BOM-less UTF-8 CSV as the system
    # codepage and mangles the em dashes and Greek letters used above.
    return buf.getvalue().encode("utf-8-sig")


_HTML_CSS = """
:root {
  --ink: #101418; --muted: #5b6672; --line: #e2e6ea; --bg: #ffffff;
  --panel: #f7f9fa; --accent: #1f5f8b;
  --ok: #17663f; --ok-bg: #e6f4ec; --warn: #8a5a00; --warn-bg: #fdf3e0;
  --bad: #9b1c1c; --bad-bg: #fdeaea; --neutral: #46505a; --neutral-bg: #eef1f3;
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 40px 24px; background: var(--panel); color: var(--ink);
  font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  -webkit-font-smoothing: antialiased;
}
.sheet {
  max-width: 1160px; margin: 0 auto; background: var(--bg); border: 1px solid var(--line);
  border-radius: 10px; padding: 40px 44px 32px;
}
header { border-bottom: 2px solid var(--ink); padding-bottom: 20px; margin-bottom: 28px; }
.eyebrow {
  font-size: 11px; letter-spacing: .12em; text-transform: uppercase; color: var(--muted);
  display: flex; justify-content: space-between; gap: 16px;
}
h1 { font-size: 30px; line-height: 1.2; margin: 12px 0 6px; letter-spacing: -.015em; }
.subtitle { color: var(--muted); margin: 0; }
.meta { margin-top: 14px; font-size: 12.5px; color: var(--muted); }
.kpis {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 12px; margin-bottom: 34px;
}
.kpi { border: 1px solid var(--line); border-radius: 8px; padding: 12px 14px; background: var(--panel); }
.kpi dt { font-size: 11px; letter-spacing: .07em; text-transform: uppercase; color: var(--muted); }
.kpi dd {
  margin: 6px 0 0; font-size: 22px; font-weight: 600; letter-spacing: -.01em;
  font-variant-numeric: tabular-nums;
}
section { margin-bottom: 36px; }
h2 { font-size: 17px; margin: 0 0 4px; }
.note { color: var(--muted); font-size: 13px; margin: 0 0 14px; }
.scroll { overflow-x: auto; border: 1px solid var(--line); border-radius: 8px; }
table { width: 100%; border-collapse: collapse; font-size: 13.5px; }
th, td { padding: 9px 12px; text-align: left; border-bottom: 1px solid var(--line); white-space: nowrap; }
th {
  background: var(--panel); font-size: 11px; font-weight: 600; letter-spacing: .06em;
  text-transform: uppercase; color: var(--muted); position: sticky; top: 0;
}
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
tbody tr:nth-child(even) { background: #fbfcfd; }
tbody tr:last-child td { border-bottom: 0; }
.empty { padding: 22px; color: var(--muted); font-size: 13.5px; text-align: center; }
.badge {
  display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11.5px;
  font-weight: 600; background: var(--neutral-bg); color: var(--neutral); white-space: nowrap;
}
.badge.b-healthy, .badge.b-improved, .badge.b-yes { background: var(--ok-bg); color: var(--ok); }
.badge.b-low, .badge.b-excess, .badge.b-slow-moving { background: var(--warn-bg); color: var(--warn); }
.badge.b-stockout, .badge.b-regressed { background: var(--bad-bg); color: var(--bad); }
footer { border-top: 1px solid var(--line); padding-top: 18px; color: var(--muted); font-size: 12px; }
footer ol { margin: 0 0 14px; padding-left: 18px; }
footer li { margin-bottom: 5px; }
.brand { color: var(--accent); font-weight: 600; }
@media print {
  body { background: #fff; padding: 0; font-size: 11pt; }
  .sheet { max-width: none; border: 0; border-radius: 0; padding: 0; }
  .scroll { overflow: visible; border: 0; }
  th { position: static; }
  section, tr { break-inside: avoid; }
  h2 { break-after: avoid; }
}
"""

# Columns whose values are drawn as pills rather than plain text.
_BADGE_COLUMNS = {"status", "significant", "result", "action"}


def _badge(value: str) -> str:
    slug = value.strip().lower().replace("_", "-").replace(" ", "-")
    return f'<span class="badge b-{html.escape(slug)}">{html.escape(value)}</span>'


def _render_html_section(section: Section) -> str:
    heading = f"<h2>{html.escape(section.title)}</h2>"
    note = f'<p class="note">{html.escape(section.note)}</p>' if section.note else ""

    if not section.rows:
        return f'<section>{heading}{note}<div class="scroll"><p class="empty">No rows.</p></div></section>'

    numeric = _numeric_columns(section)
    badge_at = [col.strip().lower() in _BADGE_COLUMNS for col in section.columns]

    head = "".join(
        f'<th class="num">{html.escape(col)}</th>' if numeric[i] else f"<th>{html.escape(col)}</th>"
        for i, col in enumerate(section.columns)
    )
    body = "".join(
        "<tr>"
        + "".join(
            f'<td class="num">{html.escape(cell)}</td>'
            if numeric[i]
            else f"<td>{_badge(cell) if badge_at[i] else html.escape(cell)}</td>"
            for i, cell in enumerate(row)
        )
        + "</tr>"
        for row in section.rows
    )
    return (
        f"<section>{heading}{note}"
        f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'
        f"</section>"
    )


def render_html(report: Report) -> bytes:
    """A self-contained, print-ready HTML document: no external CSS, fonts or
    scripts, so it survives being emailed as an attachment, and Ctrl-P produces
    a usable PDF on its own."""
    kpis = "".join(
        f'<div class="kpi"><dt>{html.escape(label)}</dt><dd>{html.escape(value)}</dd></div>'
        for label, value in report.kpis
    )
    notes = "".join(f"<li>{html.escape(note)}</li>" for note in report.notes)
    stamp = f"{report.generated_at:%d %b %Y, %H:%M} UTC"

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(report.title)} — {html.escape(report.org_name)}</title>
<style>{_HTML_CSS}</style>
</head>
<body>
<div class="sheet">
  <header>
    <div class="eyebrow"><span>{html.escape(report.org_name)}</span><span class="brand">{BRAND}</span></div>
    <h1>{html.escape(report.title)}</h1>
    <p class="subtitle">{html.escape(report.subtitle)}</p>
    <div class="meta">Generated {stamp}</div>
  </header>
  {f'<dl class="kpis">{kpis}</dl>' if kpis else ''}
  {''.join(_render_html_section(s) for s in report.sections)}
  <footer>
    {f'<ol>{notes}</ol>' if notes else ''}
    <div>{html.escape(report.org_name)} · {html.escape(report.title)} · {stamp} · {BRAND}</div>
  </footer>
</div>
</body>
</html>
""".encode()


def render_pdf(report: Report) -> bytes:
    # Imported lazily: reportlab pulls in a font/graphics stack that CSV and
    # HTML callers have no use for.
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        KeepTogether,
        ListFlowable,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    ink = colors.HexColor("#101418")
    muted = colors.HexColor("#5b6672")
    line = colors.HexColor("#e2e6ea")
    panel = colors.HexColor("#f7f9fa")

    base = getSampleStyleSheet()
    title_style = ParagraphStyle("t", base["Title"], fontName="Helvetica-Bold", fontSize=20, leading=24,
                                 alignment=0, spaceAfter=2, textColor=ink)
    subtitle_style = ParagraphStyle("st", base["Normal"], fontSize=10, leading=13, textColor=muted)
    eyebrow_style = ParagraphStyle("e", base["Normal"], fontSize=7.5, leading=10, textColor=muted)
    brand_style = ParagraphStyle("b", eyebrow_style, alignment=TA_RIGHT, textColor=colors.HexColor("#1f5f8b"))
    h2_style = ParagraphStyle("h2", base["Heading2"], fontName="Helvetica-Bold", fontSize=12, leading=15,
                              spaceBefore=0, spaceAfter=2, textColor=ink)
    note_style = ParagraphStyle("n", base["Normal"], fontSize=8.5, leading=11, textColor=muted, spaceAfter=6)
    th_style = ParagraphStyle("th", base["Normal"], fontName="Helvetica-Bold", fontSize=7, leading=9,
                              textColor=muted)
    th_right = ParagraphStyle("thr", th_style, alignment=TA_RIGHT)
    td_style = ParagraphStyle("td", base["Normal"], fontSize=7.8, leading=10, textColor=ink)
    td_right = ParagraphStyle("tdr", td_style, alignment=TA_RIGHT)
    kpi_label = ParagraphStyle("kl", base["Normal"], fontSize=6.8, leading=9, textColor=muted)
    kpi_value = ParagraphStyle("kv", base["Normal"], fontName="Helvetica-Bold", fontSize=12, leading=15,
                               textColor=ink)
    footnote_style = ParagraphStyle("fn", base["Normal"], fontSize=7.5, leading=10, textColor=muted)

    buf = io.BytesIO()
    # Landscape: an MRP plan line is 10 columns wide and portrait squeezes item
    # names into unreadable slivers.
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=14 * mm,
        bottomMargin=16 * mm,
        title=f"{report.title} - {report.org_name}",
        author=BRAND,
        subject=report.subtitle,
    )
    width = doc.width
    stamp = f"{report.generated_at:%d %b %Y, %H:%M} UTC"

    def page_furniture(canvas, doc_):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(muted)
        canvas.drawString(doc_.leftMargin, 9 * mm, f"{report.org_name} · {report.title} · {stamp}")
        canvas.drawRightString(doc_.pagesize[0] - doc_.rightMargin, 9 * mm, f"Page {canvas.getPageNumber()}")
        canvas.setStrokeColor(line)
        canvas.line(doc_.leftMargin, 12 * mm, doc_.pagesize[0] - doc_.rightMargin, 12 * mm)
        canvas.restoreState()

    story: list = []

    header = Table(
        [[Paragraph(report.org_name.upper(), eyebrow_style), Paragraph(BRAND.upper(), brand_style)]],
        colWidths=[width * 0.6, width * 0.4],
    )
    header.setStyle(TableStyle([("BOTTOMPADDING", (0, 0), (-1, -1), 6), ("TOPPADDING", (0, 0), (-1, -1), 0),
                                ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story += [header, Paragraph(report.title, title_style), Paragraph(report.subtitle, subtitle_style),
              Spacer(1, 3), Paragraph(f"Generated {stamp}", note_style)]

    if report.kpis:
        # Four cards per row keeps each one wide enough for a formatted number.
        per_row = 4
        cells = [[Paragraph(label, kpi_label), Paragraph(value, kpi_value)] for label, value in report.kpis]
        for start in range(0, len(cells), per_row):
            chunk = cells[start : start + per_row]
            padded = chunk + [["", ""]] * (per_row - len(chunk))
            grid = Table(
                [[c[0] for c in padded], [c[1] for c in padded]],
                colWidths=[width / per_row] * per_row,
            )
            grid.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (len(chunk) - 1, -1), panel),
                        ("BOX", (0, 0), (len(chunk) - 1, -1), 0.4, line),
                        ("INNERGRID", (0, 0), (len(chunk) - 1, -1), 0.4, line),
                        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 7),
                        ("TOPPADDING", (0, 0), (-1, 0), 6),
                        ("BOTTOMPADDING", (0, 1), (-1, 1), 6),
                    ]
                )
            )
            story.append(grid)
        story.append(Spacer(1, 12))

    for section in report.sections:
        head: list = [Paragraph(section.title, h2_style)]
        if section.note:
            head.append(Paragraph(section.note, note_style))

        if not section.rows:
            story += [KeepTogether(head + [Paragraph("No rows.", note_style)]), Spacer(1, 8)]
            continue

        numeric = _numeric_columns(section)
        data = [
            [Paragraph(col, th_right if numeric[i] else th_style) for i, col in enumerate(section.columns)]
        ] + [
            [Paragraph(cell, td_right if numeric[i] else td_style) for i, cell in enumerate(row)]
            for row in section.rows
        ]
        # Numeric columns need far less room than an item name; give text columns
        # double the share of the page and let Paragraph cells wrap inside them.
        weights = [1.0 if numeric[i] else 2.0 for i in range(len(section.columns))]
        total = sum(weights)
        table = Table(
            data,
            colWidths=[width * w / total for w in weights],
            repeatRows=1,
            hAlign="LEFT",
        )
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), panel),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.6, ink),
                    ("LINEBELOW", (0, 1), (-1, -2), 0.25, line),
                    ("BOX", (0, 0), (-1, -1), 0.4, line),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fbfcfd")]),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        # The heading must not orphan itself at the foot of a page, but the table
        # itself has to stay splittable or a long plan would overflow one page.
        story += [KeepTogether(head), table, Spacer(1, 12)]

    if report.notes:
        story += [
            Spacer(1, 4),
            Paragraph("Notes", h2_style),
            ListFlowable(
                [Paragraph(note, footnote_style) for note in report.notes],
                bulletType="1",
                leftIndent=12,
                bulletFontSize=7.5,
            ),
        ]

    doc.build(story, onFirstPage=page_furniture, onLaterPages=page_furniture)
    return buf.getvalue()


def _sheet_name(name: str) -> str:
    """Excel sheet names ban []:*?/\\ and cap at 31 chars."""
    cleaned = "".join("_" if ch in '[]:*?/\\' else ch for ch in (name or "Sheet").strip())
    return (cleaned or "Sheet")[:31]


def render_xlsx(report: Report) -> bytes:
    """One workbook, one sheet per section - ported from the desktop app's
    exports.py _write_xlsx, generalized to Report/Section instead of a single
    ad-hoc table."""
    from openpyxl import Workbook

    wb = Workbook()
    wb.remove(wb.active)

    if report.kpis:
        ws = wb.create_sheet(_sheet_name("Summary"))
        ws.append(["Metric", "Value"])
        for label, value in report.kpis:
            ws.append([_excel_safe(label), _excel_safe(value)])

    used_names: set[str] = set()
    for section in report.sections:
        base = _sheet_name(section.title)
        name, n = base, 2
        while name in used_names:
            name = f"{base[:28]}_{n}"
            n += 1
        used_names.add(name)

        ws = wb.create_sheet(name)
        ws.append(section.columns)
        for row in section.rows:
            ws.append([_excel_safe(cell) for cell in row])

    if not wb.sheetnames:
        wb.create_sheet(_sheet_name(report.title))

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


RENDERERS = {"csv": render_csv, "html": render_html, "pdf": render_pdf, "xlsx": render_xlsx}


def render(report: Report, fmt: str) -> bytes:
    return RENDERERS[fmt](report)
