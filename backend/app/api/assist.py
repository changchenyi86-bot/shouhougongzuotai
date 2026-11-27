"""和 AI 对话润色回复：只给技术支持看，满意后填进输入框，走原来的确认发送。"""

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import routing
from app.ai import refine
from app.ai.llm import LLMError
from app.auth import current_staff
from app.db import get_session
from app.models import Message, Staff, Suggestion, SuggestionChat, SuggestionStatus
from app.schemas import ChatIn, ChatTurnOut

router = APIRouter(prefix="/api", dependencies=[Depends(current_staff)])

# 回复已经发出的建议不能再对话
DONE = {SuggestionStatus.ADOPTED, SuggestionStatus.EDITED}


def _turns(session: Session, suggestion_id: uuid.UUID) -> list[SuggestionChat]:
    return list(session.scalars(
        select(SuggestionChat).where(SuggestionChat.suggestion_id == suggestion_id)
        .order_by(SuggestionChat.created_at)
    ))


def _suggestion(session: Session, suggestion_id: uuid.UUID, staff: Staff) -> Suggestion:
    sug = session.get(Suggestion, suggestion_id)
    if sug is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "建议不存在")
    msg = session.get(Message, sug.message_id)
    routing.ensure_access(session, staff, msg.channel, msg.group_id)  # 只有能处理这个群的人能对话
    return sug


@router.get("/suggestions/{suggestion_id}/chat", response_model=list[ChatTurnOut])
def get_chat(suggestion_id: uuid.UUID, staff: Staff = Depends(current_staff),
             session: Session = Depends(get_session)) -> list[SuggestionChat]:
    _suggestion(session, suggestion_id, staff)
    return _turns(session, suggestion_id)


@router.post("/suggestions/{suggestion_id}/chat", response_model=list[ChatTurnOut])
def chat(suggestion_id: uuid.UUID, body: ChatIn, staff: Staff = Depends(current_staff),
         session: Session = Depends(get_session)) -> list[SuggestionChat]:
    """技术支持说方案或修改意见，AI 写出新的回复。返回这条建议的全部对话。"""
    sug = _suggestion(session, suggestion_id, staff)
    if sug.status in DONE:
        raise HTTPException(status.HTTP_409_CONFLICT, "这条消息已经回复过了")
    turns = _turns(session, suggestion_id)
    if len(turns) >= refine.MAX_TURNS:
        raise HTTPException(status.HTTP_409_CONFLICT, "这条对话已经很长了，请直接在输入框里改")
    session.rollback()  # 调模型要几秒到几十秒，不占着数据库事务
    try:
        result, model = refine.refine(session, sug, turns, body.content)
    except LLMError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"AI 没有回应：{exc}") from exc
    now = datetime.now(timezone.utc)
    session.add_all([
        SuggestionChat(suggestion_id=sug.id, role="staff", content=body.content, operator=staff.name,
                       created_at=now),
        # AI 的回复排在同一轮技术支持的话后面
        SuggestionChat(suggestion_id=sug.id, role="ai", content=result.reply.strip(),
                       note=result.note.strip() or None, operator=staff.name, model=model,
                       created_at=now + timedelta(milliseconds=1)),
    ])
    session.commit()
    return _turns(session, suggestion_id)
