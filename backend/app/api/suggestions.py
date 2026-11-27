"""工作台收件箱与 AI 建议处理：采纳、修改后采纳、转人工、忽略、重新生成。"""

import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import mentions, routing, send_queue
from app.ai import reflow, worker
from app.auth import current_staff
from app.db import get_session
from app.models import OPEN_SUGGESTION, Channel, Message, Staff, Suggestion, SuggestionStatus
from app.schemas import AdoptIn, AdoptOut, HandleIn, InboxItem, MessageOut, SuggestionOut

router = APIRouter(prefix="/api", dependencies=[Depends(current_staff)])

S = SuggestionStatus
VIEWS: dict[str, list[SuggestionStatus] | None] = {
    "todo": [S.GENERATING, S.READY, S.FAILED],
    "escalated": [S.ESCALATED],
    "done": [S.ADOPTED, S.EDITED, S.DISMISSED, S.CLOSED],
    "all": None,
}
OPEN = OPEN_SUGGESTION
# 还能回复的：随会话结束的消息之后补上回复，也记为已回复
REPLYABLE = OPEN | {S.CLOSED, S.MERGED}


@router.get("/inbox", response_model=list[InboxItem])
def inbox(
    view: Literal["todo", "escalated", "done", "all"] = "todo",
    channel: Channel | None = None,
    group_id: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    staff: Staff = Depends(current_staff),
    session: Session = Depends(get_session),
) -> list[InboxItem]:
    """待处理的客户消息及其 AI 建议。view=all 时包含员工发言等没有建议的消息。

    自动发送还没成功的（排队、发送中、失败、取消）也算待处理，直到发出或标记为手动发送。
    """
    send_queue.expire_stale(session)
    stmt = select(Message, Suggestion).outerjoin(Suggestion, Suggestion.message_id == Message.id)
    statuses = VIEWS[view]
    pending_send = Suggestion.send_status.in_(send_queue.PENDING)
    if view == "todo":
        stmt = stmt.where(or_(Suggestion.status.in_(statuses), pending_send))
    elif view == "done":
        stmt = stmt.where(Suggestion.status.in_(statuses), or_(Suggestion.send_status.is_(None), ~pending_send))
    elif statuses is not None:
        stmt = stmt.where(Suggestion.status.in_(statuses))
    if channel:
        stmt = stmt.where(Message.channel == channel)
    if group_id:
        stmt = stmt.where(Message.group_id == group_id)
    scope = routing.message_scope(routing.visible(session, staff))
    if scope is not None:
        stmt = stmt.where(scope)
    stmt = stmt.order_by(Message.sent_at.desc()).limit(limit)
    return [
        InboxItem(message=MessageOut.model_validate(m), suggestion=SuggestionOut.model_validate(s) if s else None)
        for m, s in session.execute(stmt)
    ]


def _get(session: Session, suggestion_id: uuid.UUID, staff: Staff) -> Suggestion:
    # 行锁：两人同时处理同一条建议时，后到的请求会看到已处理状态并返回 409
    sug = session.get(Suggestion, suggestion_id, with_for_update=True)
    if sug is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "建议不存在")
    msg = session.get(Message, sug.message_id)
    routing.ensure_access(session, staff, msg.channel, msg.group_id)
    return sug


def _ensure_open(sug: Suggestion, allowed: set[SuggestionStatus] = OPEN) -> None:
    if sug.status not in allowed:
        raise HTTPException(status.HTTP_409_CONFLICT, f"该建议已处理（{sug.status.value}）")


def _mark(sug: Suggestion, new_status: SuggestionStatus, staff: Staff) -> None:
    sug.status, sug.handled_by, sug.handled_at = new_status, staff.name, datetime.now(timezone.utc)


@router.post("/suggestions/{suggestion_id}/adopt", response_model=AdoptOut)
def adopt(suggestion_id: uuid.UUID, body: AdoptIn, staff: Staff = Depends(current_staff),
          session: Session = Depends(get_session)) -> AdoptOut:
    """采纳建议。传入 content 且与 AI 建议不同时记为「修改后采纳」。

    send=true 时交给电脑端发送程序自动发到群里（不满足条件时返回 409 和原因，建议保持未处理）；
    否则由技术支持复制后在客户端手动发送。
    """
    sug = _get(session, suggestion_id, staff)
    _ensure_open(sug, REPLYABLE)
    content = (body.content if body.content is not None else sug.content or "").strip()
    if not content:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "回复内容为空")
    message = session.get(Message, sug.message_id)
    try:
        at = mentions.check(message.channel, content, body.mentions)
    except mentions.MentionError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    plain = mentions.without_mentions(content, [m.name for m in body.mentions])  # 自动加上的「@客户」不算改动
    edited = plain != (sug.content or "").strip()
    _mark(sug, S.EDITED if edited else S.ADOPTED, staff)
    sug.final_content, sug.delivery = content, "manual"
    reflow.record(session, sug, plain, body.via_chat)
    detail = "请复制后在客户端发送"
    if body.send:
        try:
            send_queue.enqueue(session, message.channel, message.group_id, content, [], staff.name, sug, at)
        except send_queue.SendRejected as exc:
            session.rollback()
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        detail = "已加入发送队列"
    session.commit()
    worker.submit(reflow.after_reply, sug.id)
    return AdoptOut(suggestion=SuggestionOut.model_validate(sug), delivery=sug.delivery, detail=detail)


@router.post("/suggestions/{suggestion_id}/escalate", response_model=SuggestionOut)
def escalate(suggestion_id: uuid.UUID, body: HandleIn, staff: Staff = Depends(current_staff),
             session: Session = Depends(get_session)) -> Suggestion:
    """转人工：AI 答不了，交给技术支持专人处理。"""
    sug = _get(session, suggestion_id, staff)
    _ensure_open(sug)
    _mark(sug, S.ESCALATED, staff)
    sug.note = body.note
    session.commit()
    return sug


@router.post("/suggestions/{suggestion_id}/dismiss", response_model=SuggestionOut)
def dismiss(suggestion_id: uuid.UUID, body: HandleIn, staff: Staff = Depends(current_staff),
            session: Session = Depends(get_session)) -> Suggestion:
    """忽略：这条消息无需回复（如“好的”“谢谢”）或已在群里回复过。"""
    sug = _get(session, suggestion_id, staff)
    _ensure_open(sug)
    _mark(sug, S.DISMISSED, staff)
    sug.note = body.note
    session.commit()
    return sug


@router.post("/suggestions/{suggestion_id}/regenerate", response_model=SuggestionOut)
def regenerate(suggestion_id: uuid.UUID, staff: Staff = Depends(current_staff),
               session: Session = Depends(get_session)) -> Suggestion:
    sug = _get(session, suggestion_id, staff)
    if sug.status not in (S.READY, S.FAILED):
        raise HTTPException(status.HTTP_409_CONFLICT, f"当前状态不能重新生成（{sug.status.value}）")
    session.rollback()  # 释放行锁，enqueue 用独立会话更新状态
    worker.enqueue(sug.message_id, wait=0)
    session.refresh(sug)
    return sug


@router.post("/suggestions/{suggestion_id}/reopen", response_model=SuggestionOut)
def reopen(suggestion_id: uuid.UUID, staff: Staff = Depends(current_staff),
           session: Session = Depends(get_session)) -> Suggestion:
    """把「无需回复」改回待回复（AI 自动标记错了，或技术支持点错了）。"""
    sug = _get(session, suggestion_id, staff)
    if sug.status != S.DISMISSED:
        raise HTTPException(status.HTTP_409_CONFLICT, f"只有标为无需回复的才能改回（{sug.status.value}）")
    sug.status = S.READY if sug.content else S.FAILED
    if not sug.content:
        sug.error = "AI 没有写回复，可以重新生成"
    sug.auto_dismissed, sug.reopened_by = False, staff.name
    sug.handled_by = sug.handled_at = sug.note = None
    session.commit()
    return sug
