"""消息入库的统一入口：去重写入，新消息触发 AI 回复建议。"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Message
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


def ingest(session: Session, data: MessageIn) -> tuple[Message, bool]:
    """入库，并对需要回复的新消息异步生成 AI 建议。返回 (消息, 是否新建)。"""
    from app.ai import worker
    from app.channels import ADAPTERS

    msg, created = save_message(session, data)
    if created and ADAPTERS[msg.channel].needs_reply(msg):
        worker.enqueue(msg.id)
    return msg, created
