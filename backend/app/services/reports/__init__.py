"""Report building, narration and rendering. Split into submodules; every name
callers import from app.services.reports is re-exported here."""

from app.services.reports.builders import (
    ReportSectionSpec,
    _flatten_response_to_sections,
    build_custom_report,
    build_dataset_report,
    build_report_by_kind,
    get_saved_report_or_404,
    list_saved_reports,
    reject_retired_kind_sections,
)
from app.services.reports.model import (
    BRAND,
    EM_DASH,
    FORMATS,
    KINDS,
    RETIRED_KINDS,
    Report,
    Section,
    _excel_safe,
)
from app.services.reports.narration import (
    NARRATION_STYLES,
    ReportNarration,
    SectionNarrative,
    narrate_report,
)
from app.services.reports.renderers import (
    RENDERERS,
    render,
    render_csv,
    render_html,
    render_pdf,
    render_xlsx,
)

__all__ = [
    "BRAND",
    "EM_DASH",
    "FORMATS",
    "KINDS",
    "NARRATION_STYLES",
    "RENDERERS",
    "RETIRED_KINDS",
    "Report",
    "ReportNarration",
    "ReportSectionSpec",
    "Section",
    "SectionNarrative",
    "_excel_safe",
    "_flatten_response_to_sections",
    "build_custom_report",
    "build_dataset_report",
    "build_report_by_kind",
    "get_saved_report_or_404",
    "list_saved_reports",
    "narrate_report",
    "reject_retired_kind_sections",
    "render",
    "render_csv",
    "render_html",
    "render_pdf",
    "render_xlsx",
]
