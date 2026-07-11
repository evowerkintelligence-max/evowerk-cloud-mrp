from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.services.ai_insights import answer_question, answer_question_over_dataset

router = APIRouter(prefix="/ai", tags=["ai-insights"])


class AskRequest(BaseModel):
    question: str
    # None (default) asks over this org's live production data; set to run the
    # same question over one uploaded dataset instead (see answer_question_over_dataset).
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
    """Ask a free-text question about this org's inventory, or - if dataset_id
    is set - about one uploaded dataset instead. Live-data mode is rules-first
    intent detection (compare/forecast/health/scenario) over
    compute_inventory_analytics; dataset mode is aggregate-keyword +
    column-name matching over services.dataset_query. See app.services.ai_insights."""
    if not body.question.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "question is required")
    if body.dataset_id:
        try:
            dataset_id = uuid.UUID(body.dataset_id)
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid dataset_id") from None
        result = await answer_question_over_dataset(db, current.org_id, dataset_id, body.question)
    else:
        result = await answer_question(db, current.org_id, body.question)
    return AskResponse(
        question=result.question, intents=result.intents, narrative=result.narrative, warnings=result.warnings
    )
