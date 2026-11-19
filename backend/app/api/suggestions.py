"""工作台收件箱与 AI 建议处理：采纳、修改后采纳、转人工、忽略、重新生成。"""

import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import worker
from app.db import get_session
from app.delivery import DeliveryError, deliver
from app.models import Channel, Message, Suggestion, SuggestionStatus
from app.schemas import AdoptIn, AdoptOut, HandleIn, InboxItem, MessageOut, SuggestionOut

router = APIRouter(prefix="/api")

S = SuggestionStatus
VIEWS: dict[str, list[SuggestionStatus] | None] = {
    "todo": [S.GENERATING, S.READY, S.FAILED],
    "escalated": [S.ESCALATED],
    "done": [S.ADOPTED, S.EDITED, S.DISMISSED],
    "all": None,
}
# 已处理完的建议不能再改状态
OPEN = {S.GENERATING, S.READY, S.FAILED, S.ESCALATED}


@router.get("/inbox", response_model=list[InboxItem])
def inbox(
    view: Literal["todo", "escalated", "done", "all"] = "todo",
    channel: Channel | None = None,
    group_id: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    session: Session = Depends(get_session),
) -> list[InboxItem]:
    """待处理的客户消息及其 AI 建议。view=all 时包含员工发言等没有建议的消息。"""
    stmt = select(Message, Suggestion).outerjoin(Suggestion, Suggestion.message_id == Message.id)
    statuses = VIEWS[view]
    if statuses is not None:
        stmt = stmt.where(Suggestion.status.in_(statuses))
    if channel:
        stmt = stmt.where(Message.channel == channel)
    if group_id:
        stmt = stmt.where(Message.group_id == group_id)
    stmt = stmt.order_by(Message.sent_at.desc()).limit(limit)
    return [
        InboxItem(message=MessageOut.model_validate(m), suggestion=SuggestionOut.model_validate(s) if s else None)
        for m, s in session.execute(stmt)
    ]


def _get(session: Session, suggestion_id: uuid.UUID) -> Suggestion:
    # 行锁：两人同时处理同一条建议时，后到的请求会看到已处理状态并返回 409
    sug = session.get(Suggestion, suggestion_id, with_for_update=True)
    if sug is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "建议不存在")
    return sug


def _ensure_open(sug: Suggestion) -> None:
    if sug.status not in OPEN:
        raise HTTPException(status.HTTP_409_CONFLICT, f"该建议已处理（{sug.status.value}）")


def _mark(sug: Suggestion, new_status: SuggestionStatus, operator: str | None) -> None:
    sug.status, sug.handled_by, sug.handled_at = new_status, operator, datetime.now(timezone.utc)


@router.post("/suggestions/{suggestion_id}/adopt", response_model=AdoptOut)
def adopt(suggestion_id: uuid.UUID, body: AdoptIn, session: Session = Depends(get_session)) -> AdoptOut:
    """采纳建议。传入 content 且与 AI 建议不同时记为「修改后采纳」。"""
    sug = _get(session, suggestion_id)
    _ensure_open(sug)
    content = (body.content if body.content is not None else sug.content or "").strip()
    if not content:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "回复内容为空")
    message = session.get(Message, sug.message_id)
    try:
        result = deliver(session, message, content)
    except DeliveryError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    edited = content != (sug.content or "").strip()
    _mark(sug, S.EDITED if edited else S.ADOPTED, body.operator)
    sug.final_content, sug.delivery = content, result.mode
    session.commit()
    return AdoptOut(suggestion=SuggestionOut.model_validate(sug), delivery=result.mode, detail=result.detail)


@router.post("/suggestions/{suggestion_id}/escalate", response_model=SuggestionOut)
def escalate(suggestion_id: uuid.UUID, body: HandleIn, session: Session = Depends(get_session)) -> Suggestion:
    """转人工：AI 答不了，交给技术支持专人处理。"""
    sug = _get(session, suggestion_id)
    _ensure_open(sug)
    _mark(sug, S.ESCALATED, body.operator)
    sug.note = body.note
    session.commit()
    return sug


@router.post("/suggestions/{suggestion_id}/dismiss", response_model=SuggestionOut)
def dismiss(suggestion_id: uuid.UUID, body: HandleIn, session: Session = Depends(get_session)) -> Suggestion:
    """忽略：这条消息无需回复（如“好的”“谢谢”）或已在群里回复过。"""
    sug = _get(session, suggestion_id)
    _ensure_open(sug)
    _mark(sug, S.DISMISSED, body.operator)
    sug.note = body.note
    session.commit()
    return sug


@router.post("/suggestions/{suggestion_id}/regenerate", response_model=SuggestionOut)
def regenerate(suggestion_id: uuid.UUID, session: Session = Depends(get_session)) -> Suggestion:
    sug = _get(session, suggestion_id)
    if sug.status not in (S.READY, S.FAILED):
        raise HTTPException(status.HTTP_409_CONFLICT, f"当前状态不能重新生成（{sug.status.value}）")
    session.rollback()  # 释放行锁，enqueue 用独立会话更新状态
    worker.enqueue(sug.message_id)
    session.refresh(sug)
    return sug
