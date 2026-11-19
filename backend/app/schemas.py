import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import Channel, MessageType, SuggestionStatus


class MessageIn(BaseModel):
    """渠道适配器归一后的消息，也可由外部系统直接推送。"""

    channel: Channel
    external_id: str = Field(max_length=128)
    group_id: str = Field(max_length=128)
    group_name: str | None = None
    is_external_group: bool = False
    sender_id: str = Field(max_length=128)
    sender_name: str | None = None
    msg_type: MessageType = MessageType.TEXT
    content: str
    sent_at: datetime
    raw: dict | None = None


class MessageOut(MessageIn):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    received_at: datetime
    raw: dict | None = Field(default=None, exclude=True)


class SuggestionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    message_id: uuid.UUID
    status: SuggestionStatus
    content: str | None
    can_answer: bool | None
    confidence: str | None
    reason: str | None
    model: str | None
    error: str | None
    final_content: str | None
    delivery: str | None
    note: str | None
    handled_by: str | None
    handled_at: datetime | None
    created_at: datetime
    updated_at: datetime


class InboxItem(BaseModel):
    message: MessageOut
    suggestion: SuggestionOut | None


class AdoptIn(BaseModel):
    content: str | None = Field(default=None, description="修改后的回复；不传则原样采纳 AI 建议")
    operator: str | None = Field(default=None, max_length=64)


class HandleIn(BaseModel):
    note: str | None = None
    operator: str | None = Field(default=None, max_length=64)


class AdoptOut(BaseModel):
    suggestion: SuggestionOut
    delivery: str = Field(description="manual：需复制后在客户端发送；其他值表示已由系统直接发出")
    detail: str = ""
