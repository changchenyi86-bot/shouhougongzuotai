"""按群翻看完整聊天记录：客户和员工的发言、图片文件等都在，客户消息附带 AI 建议。"""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, distinct, func, or_, select, update
from sqlalchemy.orm import Session

from datetime import datetime, timedelta, timezone

from app import send_queue
from app.api.sending import task_out
from app.db import get_session
from app.models import Attachment, Channel, GroupName, Message, SendStatus, SendTask, Suggestion, SuggestionStatus
from app.schemas import (ConversationOut, GroupNameIn, GroupNameOut, GroupOut, InboxItem, MessageOut, SendIn,
                         SendTaskOut, SuggestionOut)

router = APIRouter(prefix="/api")

S = SuggestionStatus
TODO = [S.GENERATING, S.READY, S.FAILED]


def _unlinked_pending(session: Session) -> dict[tuple, int]:
    """直接在会话里发的、发送失败或被取消、还要人处理的消息数（按群）。"""
    rows = session.execute(
        select(SendTask.channel, SendTask.group_id, func.count(SendTask.id))
        .where(SendTask.suggestion_id.is_(None), SendTask.replaced_by.is_(None),
               SendTask.status.in_(send_queue.NEEDS_ACTION))
        .group_by(SendTask.channel, SendTask.group_id)
    )
    return {(c, g): n for c, g, n in rows}


@router.get("/groups", response_model=list[GroupOut])
def list_groups(
    view: Literal["todo", "escalated", "all"] = "all",
    channel: Channel | None = None,
    q: str | None = Query(None, max_length=100, description="按群名搜索"),
    limit: int = Query(200, ge=1, le=500),
    session: Session = Depends(get_session),
) -> list[GroupOut]:
    """有消息的群，最近有消息的在前。"""
    stats = (
        select(
            Message.channel, Message.group_id,
            func.max(Message.sent_at).label("last_at"),
            func.count(Message.id).label("total"),
            func.count(Suggestion.id).filter(
                or_(Suggestion.status.in_(TODO), Suggestion.send_status.in_(send_queue.NEEDS_ACTION))
            ).label("pending"),
            func.count(Suggestion.id).filter(Suggestion.status == S.ESCALATED).label("escalated"),
        )
        .outerjoin(Suggestion, Suggestion.message_id == Message.id)
        .group_by(Message.channel, Message.group_id)
    )
    if channel:
        stats = stats.where(Message.channel == channel)
    stats = stats.subquery()
    # 每个群最新的一条消息（取群名、预览）
    latest = (
        select(Message, stats.c.total, stats.c.pending, stats.c.escalated)
        .join(stats, and_(Message.channel == stats.c.channel, Message.group_id == stats.c.group_id,
                          Message.sent_at == stats.c.last_at))
        .order_by(stats.c.last_at.desc(), Message.id)
    )
    send_queue.expire_stale(session)
    unlinked = _unlinked_pending(session)
    groups: dict[tuple, GroupOut] = {}
    for m, total, pending, escalated in session.execute(latest):
        key = (m.channel, m.group_id)
        if key in groups:  # 同一时刻多条消息时只取一条
            continue
        groups[key] = GroupOut(channel=m.channel, group_id=m.group_id, group_name=m.group_name,
                               is_external_group=m.is_external_group, last_message_at=m.sent_at,
                               last_content=m.content, message_count=total,
                               pending_count=pending + unlinked.get(key, 0), escalated_count=escalated)
    result = list(groups.values())
    # 最新一条消息可能没带群名（如群名查询失败），用历史上的群名补上
    missing = [g for g in result if not g.group_name]
    for g in missing:
        g.group_name = session.scalar(
            select(Message.group_name)
            .where(Message.channel == g.channel, Message.group_id == g.group_id, Message.group_name.is_not(None))
            .order_by(Message.sent_at.desc()).limit(1)
        )
    if q:
        result = [g for g in result if q.lower() in (g.group_name or g.group_id).lower()]
    if view == "todo":
        result = [g for g in result if g.pending_count]
    elif view == "escalated":
        result = [g for g in result if g.escalated_count]
    return result[:limit]


@router.put("/groups/name", response_model=GroupNameOut)
def set_group_name(body: GroupNameIn, session: Session = Depends(get_session)) -> GroupNameOut:
    """手动填写群名（渠道接口查不到群名时用）。已入库的消息一并改成这个名字，之后进来的消息查不到群名时也用它。"""
    exists = session.scalar(
        select(Message.id).where(Message.channel == body.channel, Message.group_id == body.group_id).limit(1)
    )
    if exists is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "没有这个群的消息")
    row = session.get(GroupName, (body.channel, body.group_id))
    if row is None:
        row = GroupName(channel=body.channel, group_id=body.group_id, name=body.name)
        session.add(row)
    row.name, row.updated_by = body.name, body.operator
    session.execute(
        update(Message).where(Message.channel == body.channel, Message.group_id == body.group_id)
        .values(group_name=body.name)
    )
    session.commit()
    # 同名的群自动发送会拒绝（怕发错群），提前提醒
    same = session.scalar(
        select(func.count(distinct(Message.group_id))).where(
            Message.channel == body.channel, Message.group_name == body.name, Message.group_id != body.group_id
        )
    )
    return GroupNameOut(channel=body.channel, group_id=body.group_id, name=body.name, duplicate=bool(same))


def _page(session: Session, base, direction: Literal["before", "after"], pivot: Message | None, limit: int):
    stmt = base
    if pivot is not None:
        if direction == "before":
            stmt = stmt.where(or_(Message.sent_at < pivot.sent_at,
                                  and_(Message.sent_at == pivot.sent_at, Message.id < pivot.id)))
        else:
            stmt = stmt.where(or_(Message.sent_at > pivot.sent_at,
                                  and_(Message.sent_at == pivot.sent_at, Message.id > pivot.id)))
    if direction == "before":
        stmt = stmt.order_by(Message.sent_at.desc(), Message.id.desc())
    else:
        stmt = stmt.order_by(Message.sent_at, Message.id)
    rows = list(session.execute(stmt.limit(limit + 1)))
    more = len(rows) > limit
    rows = rows[:limit]
    if direction == "before":
        rows.reverse()
    return rows, more


def _item(m: Message, s: Suggestion | None) -> InboxItem:
    return InboxItem(message=MessageOut.model_validate(m), suggestion=SuggestionOut.model_validate(s) if s else None)


@router.get("/conversation", response_model=ConversationOut)
def conversation(
    channel: Channel,
    group_id: str,
    before: uuid.UUID | None = Query(None, description="加载这条消息之前的更早消息"),
    after: uuid.UUID | None = Query(None, description="加载这条消息之后的更新消息"),
    anchor: uuid.UUID | None = Query(None, description="定位到这条消息，返回它前后的消息"),
    limit: int = Query(50, ge=1, le=200),
    session: Session = Depends(get_session),
) -> ConversationOut:
    """一个群的聊天记录，按时间从早到晚。不传 before/after/anchor 时返回最新的 limit 条。

    outbox 是工作台发出、还没出现在聊天记录里的消息：排队、发送中、失败或取消的，以及 3 分钟内刚发出的
    （发出的消息要等会话存档拉回来才会出现在记录里）。
    """
    base = (
        select(Message, Suggestion)
        .outerjoin(Suggestion, Suggestion.message_id == Message.id)
        .where(Message.channel == channel, Message.group_id == group_id)
    )

    def pivot(message_id: uuid.UUID) -> Message:
        m = session.get(Message, message_id)
        if m is None or m.channel != channel or m.group_id != group_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "消息不存在或不属于这个群")
        return m

    send_queue.expire_stale(session)
    outbox = _outbox(session, channel, group_id)

    if anchor:
        m = pivot(anchor)
        older, more_before = _page(session, base, "before", m, limit // 2)
        newer, more_after = _page(session, base, "after", m, limit - limit // 2)
        own = session.scalar(select(Suggestion).where(Suggestion.message_id == m.id))
        items = [_item(*r) for r in older] + [_item(m, own)] + [_item(*r) for r in newer]
        return ConversationOut(items=items, has_more_before=more_before, has_more_after=more_after, outbox=outbox)
    if after:
        rows, more = _page(session, base, "after", pivot(after), limit)
        return ConversationOut(items=[_item(*r) for r in rows], has_more_before=True, has_more_after=more,
                               outbox=outbox)
    rows, more = _page(session, base, "before", pivot(before) if before else None, limit)
    return ConversationOut(items=[_item(*r) for r in rows], has_more_before=more, has_more_after=bool(before),
                           outbox=outbox)


def _outbox(session: Session, channel: Channel, group_id: str) -> list[SendTaskOut]:
    recent = datetime.now(timezone.utc) - timedelta(minutes=3)
    tasks = session.scalars(
        select(SendTask)
        .where(SendTask.channel == channel, SendTask.group_id == group_id, SendTask.replaced_by.is_(None),
               or_(SendTask.status.in_(send_queue.PENDING),
                   and_(SendTask.status == SendStatus.SENT, SendTask.finished_at >= recent)))
        .order_by(SendTask.created_at)
    )
    return [task_out(session, t) for t in tasks]


@router.post("/conversations/send", response_model=SendTaskOut, status_code=status.HTTP_201_CREATED)
def send(body: SendIn, session: Session = Depends(get_session)) -> SendTaskOut:
    """在会话里发消息（文字、图片、文件），由电脑端发送程序自动发到群里。

    带 suggestion_id 时表示回复那条客户消息：该建议记为已采纳（内容和 AI 建议不同时记为修改后采纳）。
    """
    content = body.content.strip()
    if not content and not body.attachment_ids:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "没有要发送的内容")
    found = set(session.scalars(select(Attachment.id).where(Attachment.id.in_(body.attachment_ids))))
    if missing := [a for a in body.attachment_ids if a not in found]:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"附件不存在：{missing[0]}")
    sug = None
    if body.suggestion_id:
        sug = session.get(Suggestion, body.suggestion_id, with_for_update=True)
        msg = session.get(Message, sug.message_id) if sug else None
        if msg is None or msg.channel != body.channel or msg.group_id != body.group_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "回复的消息不存在或不属于这个群")
        if sug.status not in (S.GENERATING, S.READY, S.FAILED, S.ESCALATED):
            raise HTTPException(status.HTTP_409_CONFLICT, f"这条消息已经处理过了（{sug.status.value}）")
        edited = content != (sug.content or "").strip()
        sug.status = S.EDITED if edited else S.ADOPTED
        sug.handled_by, sug.handled_at = body.operator, datetime.now(timezone.utc)
        sug.final_content = content
    try:
        task = send_queue.enqueue(session, body.channel, body.group_id, content, body.attachment_ids,
                                  body.operator, sug)
    except send_queue.SendRejected as exc:
        session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    session.commit()
    return task_out(session, task)


@router.post("/messages/{message_id}/fetch-media", response_model=MessageOut)
def fetch_media(message_id: uuid.UUID, session: Session = Depends(get_session)) -> Message:
    """重新下载客户发的图片或文件（之前下载失败时）。"""
    from app.channels.wecom import media

    msg = session.get(Message, message_id)
    if msg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "消息不存在")
    if msg.channel != Channel.WECOM or not media.wants_media(msg):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "这条消息没有可下载的图片或文件")
    media.fetch(msg.id)
    session.expire_all()
    return session.get(Message, message_id)
