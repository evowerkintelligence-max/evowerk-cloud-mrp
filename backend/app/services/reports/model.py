"""Report and Section dataclasses, shared constants and value formatting."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Org

# Ported from a desktop app's reports_v2 (ReportSpec + csv/html/pdf renderers).
# Dropped on the way over: the on-disk spec store, the artifact registry, and
# the BI-widget embeds - all of that existed to snapshot a *workflow run*, and
# every source here is recomputed from live org data on request instead. A
# report is therefore a pure function of (org, kind, params) and needs no
# table, no migration and no report_id: build a Report, render it, stream it.
#
# AI narration (ported from report_narrate.py, see narrate_report below) keeps
# that same shape: nothing is stored, a Report is narrated fresh on request
# from whatever data it just computed.
BRAND = "EvoWerk Cloud MRP"

KINDS = ("dataset",)
# Kinds that used to be in KINDS and can still show up in an old
# saved_reports.spec / report_inbox_items.spec captured before removal (see
# _resolve_kind_report_sections). Never add a kind here to bring it back -
# these stay permanently unbuildable, only tolerated in stored specs.
RETIRED_KINDS = ("mrp-plan", "inventory-analytics", "rca", "scenario")


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
