"""会话分流和可见范围。

- 新会话先给群负责人；负责人不在线，给同组在线、手上会话最少的人；同组都不在线，还是给负责人，
  负责人上线时在工作台里提醒。没指定负责人的群等管理员分配。
- 管理员看全部群；其他人只看自己负责的群，和正在替别人处理的群。
"""

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import auth, send_queue
from app.models import (OPEN_SUGGESTION, Channel, Conversation, GroupOwner, Message, Role, SendTask, Staff,
                        Suggestion)

GroupKey = tuple[Channel, str]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def current(session: Session, channel: Channel, group_id: str, lock: bool = False) -> Conversation | None:
    """这个群进行中的会话。"""
    stmt = select(Conversation).where(Conversation.channel == channel, Conversation.group_id == group_id,
                                      Conversation.ended_at.is_(None))
    if lock:
        stmt = stmt.with_for_update()
    return session.scalar(stmt)


def owner_of(session: Session, channel: Channel, group_id: str) -> Staff | None:
    row = session.get(GroupOwner, (channel, group_id))
    return row.owner if row and row.owner.active else None


def open_counts(session: Session) -> dict[uuid.UUID, int]:
    """每人手上进行中的会话数。"""
    rows = session.execute(
        select(Conversation.assignee_id, func.count(Conversation.id))
        .where(Conversation.ended_at.is_(None), Conversation.assignee_id.is_not(None))
        .group_by(Conversation.assignee_id)
    )
    return dict(rows.all())


def pick(session: Session, channel: Channel, group_id: str) -> Staff | None:
    """按规则选人。没有负责人时返回 None（等管理员分配）。"""
    owner = owner_of(session, channel, group_id)
    if owner is None:
        return None
    if auth.is_online(owner):
        return owner
    if owner.team_id:
        mates = [s for s in session.scalars(
            select(Staff).where(Staff.team_id == owner.team_id, Staff.active.is_(True), Staff.id != owner.id)
        ) if auth.is_online(s)]
        if mates:
            counts = open_counts(session)
            since = _now() - timedelta(hours=24)
            recent = dict(session.execute(
                select(Conversation.assignee_id, func.count(Conversation.id))
                .where(Conversation.opened_at >= since).group_by(Conversation.assignee_id)
            ).all())
            # 手上会话最少的；一样多时给最近一天接得少的
            return min(mates, key=lambda s: (counts.get(s.id, 0), recent.get(s.id, 0), s.name))
    return owner


def assign(conv: Conversation, staff: Staff | None) -> None:
    conv.assignee_id = staff.id if staff else None
    conv.assigned_at = _now() if staff else None


def open_conversation(session: Session, channel: Channel, group_id: str, at: datetime | None = None) -> Conversation:
    """客户发来要处理的消息时调用：群里没有进行中的会话就开一个，并按规则分配。"""
    conv = current(session, channel, group_id)
    if conv is not None:
        return conv
    conv = Conversation(channel=channel, group_id=group_id, opened_at=at or _now())
    assign(conv, pick(session, channel, group_id))
    session.add(conv)
    try:
        session.commit()
    except IntegrityError:
        # 同一个群的两条消息同时进来：另一边已经开了会话
        session.rollback()
        conv = current(session, channel, group_id)
    return conv


def backfill(session: Session) -> int:
    """还有待处理的消息、却没有进行中会话的群（如分流上线前就在待处理里的），补开会话。"""
    pending = select(Message.channel, Message.group_id, func.min(Message.sent_at)).join(
        Suggestion, Suggestion.message_id == Message.id
    ).where(Suggestion.status.in_(OPEN_SUGGESTION)).group_by(Message.channel, Message.group_id)
    groups: dict[GroupKey, datetime | None] = {(c, g): at for c, g, at in session.execute(pending)}
    for c, g in session.execute(
        select(SendTask.channel, SendTask.group_id).where(
            SendTask.replaced_by.is_(None), SendTask.status.in_(send_queue.NEEDS_ACTION))
    ):
        groups.setdefault((c, g), None)
    opened = 0
    for (c, g), at in groups.items():
        if current(session, c, g) is None:
            open_conversation(session, c, g, at)
            opened += 1
    return opened


# ---------- 可见范围 ----------


def visible(session: Session, staff: Staff) -> set[GroupKey] | None:
    """这个人能看的群；管理员返回 None 表示全部。"""
    if staff.role == Role.ADMIN:
        return None
    owned = session.execute(select(GroupOwner.channel, GroupOwner.group_id).where(GroupOwner.owner_id == staff.id))
    handling = session.execute(
        select(Conversation.channel, Conversation.group_id)
        .where(Conversation.assignee_id == staff.id, Conversation.ended_at.is_(None))
    )
    return {(c, g) for c, g in owned} | {(c, g) for c, g in handling}


def ensure_access(session: Session, staff: Staff, channel: Channel, group_id: str) -> None:
    keys = visible(session, staff)
    if keys is not None and (channel, group_id) not in keys:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "这个群没有分给你")


def can_reassign(session: Session, staff: Staff, channel: Channel, group_id: str, conv: Conversation) -> bool:
    """管理员、群负责人、正在处理的人可以把会话转给别人。"""
    if staff.role == Role.ADMIN or conv.assignee_id == staff.id:
        return True
    owner = owner_of(session, channel, group_id)
    return owner is not None and owner.id == staff.id


def release(session: Session, staff: Staff) -> None:
    """成员被停用：手上进行中的会话按规则重新分配（调用方先把停用写进库，再负责 commit）。"""
    for conv in session.scalars(
        select(Conversation).where(Conversation.assignee_id == staff.id, Conversation.ended_at.is_(None))
    ).all():
        assign(conv, pick(session, conv.channel, conv.group_id))


def message_scope(staff_keys: set[GroupKey] | None):
    """把可见范围变成查询条件（用在按消息查的地方）。"""
    if staff_keys is None:
        return None
    if not staff_keys:
        return Message.id.is_(None)  # 一个都看不到
    return or_(*[(Message.channel == c) & (Message.group_id == g) for c, g in staff_keys])
