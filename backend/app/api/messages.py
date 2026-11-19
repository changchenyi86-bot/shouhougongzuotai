from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.channels import ADAPTERS
from app.db import get_session
from app.ingest import ingest
from app.models import Channel, Message
from app.schemas import MessageIn, MessageOut

router = APIRouter()


@router.post("/api/messages", response_model=MessageOut, status_code=status.HTTP_201_CREATED)
def ingest_message(data: MessageIn, session: Session = Depends(get_session)) -> Message:
    """写入一条已归一的消息（重复的 channel + external_id 直接返回已有记录）。"""
    return ingest(session, data)[0]


@router.get("/api/messages", response_model=list[MessageOut])
def list_messages(
    channel: Channel | None = None,
    group_id: str | None = None,
    since: datetime | None = None,
    limit: int = Query(50, ge=1, le=200),
    session: Session = Depends(get_session),
) -> list[Message]:
    stmt = select(Message).order_by(Message.sent_at.desc()).limit(limit)
    if channel:
        stmt = stmt.where(Message.channel == channel)
    if group_id:
        stmt = stmt.where(Message.group_id == group_id)
    if since:
        stmt = stmt.where(Message.sent_at >= since)
    return list(session.scalars(stmt))


@router.post("/webhooks/{channel}")
async def channel_webhook(channel: Channel, request: Request, session: Session = Depends(get_session)) -> dict:
    """各渠道回调入口。"""
    try:
        data = await ADAPTERS[channel].parse(request)
    except NotImplementedError as exc:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, str(exc)) from exc
    if data is None:
        return {"ok": True}
    msg, _ = ingest(session, data)
    return {"ok": True, "id": str(msg.id)}
