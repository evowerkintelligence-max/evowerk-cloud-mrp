from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.services import data_chat as service

router = APIRouter(prefix="/data-chat", tags=["data-chat"])


class ChatMessageRequest(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1)
    history: list[ChatMessageRequest] = Field(default_factory=list)
    dataset_ids: list[uuid.UUID] | None = None


class ChartSpecResponse(BaseModel):
    title: str
    type: str
    x_labels: list[str]
    series: list[dict]


class TableDataResponse(BaseModel):
    columns: list[str]
    rows: list[list]


class ChatResponse(BaseModel):
    answer: str
    chart: ChartSpecResponse | None
    table: TableDataResponse | None
    follow_ups: list[str]
    datasets_used: list[str]


@router.post("/message", response_model=ChatResponse)
async def chat_message(
    req: ChatRequest,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ChatResponse:
    history = [service.ChatMessage(role=m.role, content=m.content) for m in req.history]
    result = await service.answer_question(db, current.org_id, req.question, history, req.dataset_ids)
    return ChatResponse(
        answer=result.answer,
        chart=ChartSpecResponse(**vars(result.chart)) if result.chart else None,
        table=TableDataResponse(**vars(result.table)) if result.table else None,
        follow_ups=result.follow_ups, datasets_used=result.datasets_used,
    )
