import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import Channel, MessageType


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
