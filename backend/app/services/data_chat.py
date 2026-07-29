"""AI Data Chat: natural-language questions answered against the org's
uploaded datasets. Builds a compact per-dataset column-profile context and
asks Gemini for a structured JSON answer (text + optional chart/table +
follow-ups), falling back to a deterministic heuristic (column stats, date
ranges) when no Gemini key is configured. Ported from the desktop app's
data_chat.py.

ponytail: the desktop version also has an SSE streaming endpoint and a
tier-gated daily usage counter - both dropped here. Streaming is a pure UX
nicety on top of the same answer this blocking endpoint already returns, and
usage limits only make sense against the desktop's plan-tier system (already
out of scope for this port, see tier.py). Add streaming if the chat UI proves
too slow for long answers.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import datasets as ds_service
from app.services import llm

_ANALYTICAL_PATTERNS = re.compile(
    r"\b(average|avg|mean|total|sum|count|trend|compare|max|min|highest|lowest|"
    r"overall|rate|percentage|percent|ratio|distribution|breakdown|over time)\b",
    re.IGNORECASE,
)
_RECORD_PATTERNS = re.compile(
    r"\b(show|list|find|get|fetch|display|which|what are the|give me|"
    r"specific|detail|example|sample|last \d+|first \d+|recent)\b",
    re.IGNORECASE,
)

_SYSTEM = """You are EvoWerk AI, a manufacturing intelligence assistant embedded in a factory analytics platform.
You have access to real manufacturing datasets and answer questions from plant managers, supervisors, and operators.

Rules:
- Base every answer ONLY on the data provided - never invent numbers
- Quote specific figures from the data; name machines, lines, shifts, dates exactly as they appear
- Keep answers concise (2-5 sentences unless a detailed breakdown is requested)
- When asked to "show", "compare", "chart", or "visualise", include chart or table output
- Use correct manufacturing terminology (OEE, Availability, Performance, Quality, MTBF, MTTR, planned downtime, unplanned downtime)

Respond ONLY with a JSON object - no markdown fences, no commentary outside the JSON:
{
  "answer": "Natural language answer with specific figures",
  "chart": null or {"title": "...", "type": "bar"|"line"|"pie", "x_labels": ["..."], "series": [{"name": "...", "data": [1.0, 2.0]}]},
  "table": null or {"columns": ["..."], "rows": [["..."]]},
  "follow_ups": ["Short follow-up question 1?", "Short follow-up question 2?", "Short follow-up question 3?"]
}"""


@dataclass
class ChatMessage:
    role: str
    content: str


@dataclass
class ChartSpec:
    title: str = ""
    type: str = "bar"
    x_labels: list[str] = field(default_factory=list)
    series: list[dict] = field(default_factory=list)


@dataclass
class TableData:
    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)


@dataclass
class ChatResult:
    answer: str
    chart: ChartSpec | None = None
    table: TableData | None = None
    follow_ups: list[str] = field(default_factory=list)
    datasets_used: list[str] = field(default_factory=list)


def _is_numeric(v: object) -> bool:
    try:
        float(str(v).replace(",", ""))
        return True
    except (TypeError, ValueError):
        return False


def _infer_col_type(values: list) -> str:
    non_null = [v for v in values if v is not None and str(v).strip()][:20]
    if not non_null:
        return "text"
    for v in non_null[:5]:
        try:
            pd.to_datetime(str(v))
            return "date"
        except (ValueError, TypeError):
            continue
    numeric = sum(1 for v in non_null if _is_numeric(v))
    return "number" if numeric / len(non_null) > 0.8 else "text"


def _needs_raw_rows(question: str) -> bool:
    return bool(_RECORD_PATTERNS.search(question)) and not _ANALYTICAL_PATTERNS.search(question)


def _build_dataset_context(name: str, rows: list[dict], question: str) -> str:
    if not rows:
        return f"Dataset '{name}': no data available.\n"

    columns = list(rows[0].keys())
    sample = rows[:50]
    col_lines = []
    for col in columns:
        values = [r.get(col) for r in sample]
        ctype = _infer_col_type(values)
        non_null = [v for v in values if v is not None and str(v).strip()]

        if ctype == "date":
            try:
                dates = sorted(pd.to_datetime(str(v)) for v in non_null if v)
                col_lines.append(f"  {col} [date] range: {dates[0].date()} - {dates[-1].date()}")
            except (ValueError, TypeError):
                col_lines.append(f"  {col} [date]")
        elif ctype == "number":
            try:
                nums = [float(str(v).replace(",", "")) for v in non_null]
                col_lines.append(f"  {col} [number] min={min(nums):.2f}  max={max(nums):.2f}  avg={sum(nums) / len(nums):.2f}")
            except (ValueError, ZeroDivisionError):
                col_lines.append(f"  {col} [number]")
        else:
            unique = list(dict.fromkeys(str(v) for v in non_null if v))
            col_lines.append(
                f"  {col} [text] values: {', '.join(unique)}" if len(unique) <= 25
                else f"  {col} [text] {len(unique)} unique values"
            )

    ctx = f"=== Dataset: {name} ({len(rows)} rows) ===\nColumns:\n" + "\n".join(col_lines)

    if _needs_raw_rows(question):
        display = sample[:20]
        header = " | ".join(columns)
        data_lines = [" | ".join(str(r.get(c, "")) for c in columns) for r in display]
        ctx += f"\n\nSample rows ({len(display)}):\n{header}\n{'-' * min(len(header), 120)}\n" + "\n".join(data_lines)

    return ctx


def _build_prompt(question: str, history: list[ChatMessage], data_ctx: str) -> str:
    today = datetime.now(timezone.utc).strftime("%A, %d %B %Y")
    hist = ""
    if history:
        lines = [f"{'User' if m.role == 'user' else 'EvoWerk AI'}: {m.content}" for m in history[-6:]]
        hist = "Conversation so far:\n" + "\n".join(lines) + "\n\n"
    return f"Today: {today}\n\nManufacturing data available:\n{data_ctx}\n\n{hist}User: {question}"


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        parts = t.split("```")
        if len(parts) >= 3:
            inner = parts[1]
            return (inner[4:] if inner.startswith("json") else inner).strip()
    return t


def _parse_ai_response(raw: str, datasets_used: list[str]) -> ChatResult:
    try:
        parsed = json.loads(_strip_fences(raw))
    except json.JSONDecodeError:
        return ChatResult(answer=raw, datasets_used=datasets_used)

    chart = None
    if isinstance(parsed.get("chart"), dict):
        c = parsed["chart"]
        chart = ChartSpec(
            title=str(c.get("title") or ""), type=str(c.get("type") or "bar"),
            x_labels=[str(x) for x in (c.get("x_labels") or [])], series=c.get("series") or [],
        )
    table = None
    if isinstance(parsed.get("table"), dict):
        t = parsed["table"]
        table = TableData(columns=[str(c) for c in (t.get("columns") or [])], rows=t.get("rows") or [])

    return ChatResult(
        answer=str(parsed.get("answer") or raw), chart=chart, table=table,
        follow_ups=[str(f) for f in (parsed.get("follow_ups") or [])[:3]], datasets_used=datasets_used,
    )


_NO_AI_NOTE = (
    "AI is not configured for this workspace, so this is a summary of the data's structure, "
    "not a direct answer to your question."
)
_AI_FAILED_NOTE = (
    "The AI service did not respond, so this is a summary of the data's structure, "
    "not a direct answer to your question."
)


def _mentions(q: str, *words: str) -> bool:
    # Whole words only: substring matching made "worst performing machine" hit "min".
    return bool(re.search(r"\b(" + "|".join(words) + r")\b", q))


def _heuristic_answer(question: str, data_ctx: str, datasets_used: list[str], note: str) -> ChatResult:
    q = question.lower()
    col_stats: list[dict] = []
    for line in data_ctx.split("\n"):
        stripped = line.strip()
        for tag in ("[number]", "[date]", "[text]"):
            if tag in stripped:
                name, _, rest = stripped.partition(tag)
                col_stats.append({"name": name.strip(), "type": tag[1:-1], "info": rest.strip()})

    number_cols = [c for c in col_stats if c["type"] == "number"]
    date_cols = [c for c in col_stats if c["type"] == "date"]

    if _mentions(q, "columns?", "what data", "what do i have", "schema", "fields?"):
        summary = ", ".join(f"{c['name']} ({c['type']})" for c in col_stats[:20])
        return ChatResult(answer=f"{note}\n\nYour datasets contain these columns: {summary}", datasets_used=datasets_used,
                           follow_ups=["Show me the numeric columns", "What date range does the data cover?"])

    if _mentions(q, "average", "avg", "mean", "min", "max", "range") and number_cols:
        summaries = [f"{c['name']}: {c['info']}" for c in number_cols[:8] if c["info"]]
        if summaries:
            return ChatResult(answer=f"{note}\n\nHere are the numeric column statistics:\n" + "\n".join(summaries),
                               datasets_used=datasets_used, follow_ups=["Which column has the highest values?"])

    if _mentions(q, "trend", "over time", "date range", "when") and date_cols:
        summaries = [f"{c['name']}: {c['info']}" for c in date_cols]
        return ChatResult(answer=f"{note}\n\nDate columns in your data:\n" + "\n".join(summaries), datasets_used=datasets_used)

    all_cols = ", ".join(c["name"] for c in col_stats[:15])
    return ChatResult(
        answer=f"{note}\n\nI can see these columns: {all_cols}.",
        datasets_used=datasets_used,
        follow_ups=["What columns do I have?", "Show numeric column stats", "What date range does my data cover?"],
    )


async def answer_question(
    db: AsyncSession, org_id: uuid.UUID, question: str, history: list[ChatMessage], dataset_ids: list[uuid.UUID] | None,
) -> ChatResult:
    all_datasets = await ds_service.list_datasets(db, org_id)
    if dataset_ids:
        wanted = {str(d) for d in dataset_ids}
        all_datasets = [d for d in all_datasets if str(d.id) in wanted]

    datasets_info: list[tuple[str, list[dict]]] = []
    datasets_used: list[str] = []
    for dataset in all_datasets[:5]:
        rows = ds_service.preview_rows(dataset, limit=50)
        if rows:
            datasets_info.append((dataset.name, rows))
            datasets_used.append(dataset.name)

    if not datasets_info:
        return ChatResult(answer="No datasets are available yet. Please upload a dataset first.",
                           follow_ups=["How do I upload a dataset?"])

    data_ctx = "\n\n".join(_build_dataset_context(name, rows, question) for name, rows in datasets_info)

    if not llm.is_configured():
        return _heuristic_answer(question, data_ctx, datasets_used, _NO_AI_NOTE)

    try:
        raw = await llm.generate_text(
            _build_prompt(question, history, data_ctx), system=_SYSTEM, max_output_tokens=1500
        )
    except Exception:  # transient LLM error - fall back to the deterministic path instead of a 500
        return _heuristic_answer(question, data_ctx, datasets_used, _AI_FAILED_NOTE)
    return _parse_ai_response(raw, datasets_used)
