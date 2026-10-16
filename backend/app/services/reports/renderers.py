"""CSV / HTML / PDF / XLSX renderers."""

from __future__ import annotations

import csv
import html
import io


from app.services.reports.model import (
    BRAND,
    _excel_safe,
    Section,
    Report,
    _numeric_columns,
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
.badge.b-low, .badge.b-excess, .badge.b-slow-moving, .badge.b-dead-stock { background: var(--warn-bg); color: var(--warn); }
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
