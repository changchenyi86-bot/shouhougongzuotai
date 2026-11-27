"""消息入库的统一入口：去重写入，新消息触发 AI 回复建议。"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import GroupName, Message
from app.schemas import MessageIn


def _find(session: Session, data: MessageIn) -> Message | None:
    return session.scalar(
        select(Message).where(Message.channel == data.channel, Message.external_id == data.external_id)
    )


def save_message(session: Session, data: MessageIn) -> tuple[Message, bool]:
    """写入消息，返回 (消息, 是否新建)。同一 channel + external_id 只入库一次。"""
    existing = _find(session, data)
    if existing:
        return existing, False
    if not data.group_name:
        # 渠道查不到群名时，用技术支持在工作台里填的
        manual = session.get(GroupName, (data.channel, data.group_id))
        if manual:
            data = data.model_copy(update={"group_name": manual.name})
    msg = Message(**data.model_dump())
    session.add(msg)
    try:
        session.commit()
    except IntegrityError:
        # 渠道并发重试回调：另一请求已先写入同一条消息
        session.rollback()
        existing = _find(session, data)
        if existing is None:
            raise
        return existing, False
    return msg, True


def _is_recent(msg: Message) -> bool:
    """首次接入时会导入历史消息，只为近期消息生成建议，避免大量调用模型、塞满收件箱。"""
    sent = msg.sent_at if msg.sent_at.tzinfo else msg.sent_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - sent <= timedelta(minutes=settings.ai_suggest_max_age_minutes)


def ingest(session: Session, data: MessageIn) -> tuple[Message, bool]:
    """入库，并对需要回复的新消息异步生成 AI 建议。返回 (消息, 是否新建)。"""
    from app.ai import worker
    from app.channels import ADAPTERS

    msg, created = save_message(session, data)
    if created and _is_recent(msg) and ADAPTERS[msg.channel].needs_reply(msg):
        worker.enqueue(msg.id)
    return msg, created
