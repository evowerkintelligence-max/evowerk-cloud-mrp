from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.services.ai_insights import answer_question_over_dataset
from app.services.narratives import build_narrative

router = APIRouter(prefix="/ai", tags=["ai-insights"])


class AskRequest(BaseModel):
    question: str
    # Which uploaded dataset to run the question over (see answer_question_over_dataset).
    dataset_id: str | None = None


class AskResponse(BaseModel):
    question: str
    intents: list[str]
    narrative: str
    warnings: list[str]


@router.post("/ask", response_model=AskResponse)
async def post_ask(
    body: AskRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Ask a free-text question about one uploaded dataset: aggregate-keyword +
    column-name matching over services.dataset_query. See app.services.ai_insights."""
    if not body.question.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "question is required")
    if not body.dataset_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "dataset_id is required")
    try:
        dataset_id = uuid.UUID(body.dataset_id)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid dataset_id") from None
    result = await answer_question_over_dataset(db, current.org_id, dataset_id, body.question)
    return AskResponse(
        question=result.question, intents=result.intents, narrative=result.narrative, warnings=result.warnings
    )


class NarrativeRequest(BaseModel):
    """Direct narrative endpoint from structured facts the caller already
    computed - e.g. a dashboard that just ran a few /kpis/compute,
    /kpis/compare, and forecast calls and wants them turned into prose.
    Unlike /ai/ask, this does no data fetching of its own.

    results_by_action_id maps an arbitrary caller-chosen id to
    {"type": "kpi_run"|"compare_runs"|"forecast_kpi", "ok": bool,
    "error": str | None, "payload": {...}}. See app.services.narratives for
    the payload shapes each type expects."""

    question: str
    results_by_action_id: dict[str, dict[str, Any]]
    audience: Literal["exec", "analyst", "engineer"] = "exec"
    style: Literal["bullet", "paragraph", "mixed"] = "mixed"
    source_action_ids: list[str] | None = None


class NarrativeResponse(BaseModel):
    narrative: str


@router.post("/narrative", response_model=NarrativeResponse)
async def post_narrative(body: NarrativeRequest, current: CurrentUser = Depends(get_current_user)):
    if not body.question.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "question is required")
    narrative = build_narrative(
        question=body.question,
        results_by_action_id=body.results_by_action_id,
        audience=body.audience,
        style=body.style,
        source_action_ids=body.source_action_ids,
    )
    return NarrativeResponse(narrative=narrative)
