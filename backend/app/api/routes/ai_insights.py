from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.services.ai_insights import answer_question

router = APIRouter(prefix="/ai", tags=["ai-insights"])


class AskRequest(BaseModel):
    question: str


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
    """Ask a free-text question about this org's inventory. Rules-first intent
    detection (compare/forecast/health/scenario) plus NLQ-to-filter over the
    same rows compute_inventory_analytics already produces, narrated in plain
    text - see app.services.ai_insights."""
    if not body.question.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "question is required")
    result = await answer_question(db, current.org_id, body.question)
    return AskResponse(
        question=result.question, intents=result.intents, narrative=result.narrative, warnings=result.warnings
    )
