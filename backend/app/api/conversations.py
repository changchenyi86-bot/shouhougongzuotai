"""按群翻看完整聊天记录：客户和员工的发言、图片文件等都在，客户消息附带 AI 建议。"""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app import send_queue
from app.db import get_session
from app.models import Channel, Message, Suggestion, SuggestionStatus
from app.schemas import ConversationOut, GroupOut, InboxItem, MessageOut, SuggestionOut

router = APIRouter(prefix="/api")

S = SuggestionStatus
TODO = [S.GENERATING, S.READY, S.FAILED]


@router.get("/groups", response_model=list[GroupOut])
def list_groups(
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
                or_(Suggestion.status.in_(TODO), Suggestion.send_status.in_(send_queue.PENDING))
            ).label("pending"),
        )
        .outerjoin(Suggestion, Suggestion.message_id == Message.id)
        .group_by(Message.channel, Message.group_id)
    )
    if channel:
        stats = stats.where(Message.channel == channel)
    stats = stats.subquery()
    # 每个群最新的一条消息（取群名、预览）
    latest = (
        select(Message, stats.c.total, stats.c.pending)
        .join(stats, and_(Message.channel == stats.c.channel, Message.group_id == stats.c.group_id,
                          Message.sent_at == stats.c.last_at))
        .order_by(stats.c.last_at.desc(), Message.id)
    )
    groups: dict[tuple, GroupOut] = {}
    for m, total, pending in session.execute(latest):
        key = (m.channel, m.group_id)
        if key in groups:  # 同一时刻多条消息时只取一条
            continue
        groups[key] = GroupOut(channel=m.channel, group_id=m.group_id, group_name=m.group_name,
                               is_external_group=m.is_external_group, last_message_at=m.sent_at,
                               last_content=m.content, message_count=total, pending_count=pending)
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
    return result[:limit]


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
    """一个群的聊天记录，按时间从早到晚。不传 before/after/anchor 时返回最新的 limit 条。"""
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

    if anchor:
        m = pivot(anchor)
        older, more_before = _page(session, base, "before", m, limit // 2)
        newer, more_after = _page(session, base, "after", m, limit - limit // 2)
        own = session.scalar(select(Suggestion).where(Suggestion.message_id == m.id))
        items = [_item(*r) for r in older] + [_item(m, own)] + [_item(*r) for r in newer]
        return ConversationOut(items=items, has_more_before=more_before, has_more_after=more_after)
    if after:
        rows, more = _page(session, base, "after", pivot(after), limit)
        return ConversationOut(items=[_item(*r) for r in rows], has_more_before=True, has_more_after=more)
    rows, more = _page(session, base, "before", pivot(before) if before else None, limit)
    return ConversationOut(items=[_item(*r) for r in rows], has_more_before=more, has_more_after=bool(before))
