"""AI narration of a built Report."""

from __future__ import annotations


from pydantic import BaseModel

from app.services import llm
from app.services.reports.model import (
    Section,
    Report,
)

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
        return _fallback_narration(report, error="AI narration is not configured (no GEMINI_API_KEY).")

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
