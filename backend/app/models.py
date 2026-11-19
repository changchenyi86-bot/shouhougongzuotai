"""统一消息模型：企业微信、钉钉、飞书的群消息都归一成 Message。"""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Enum, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Channel(str, enum.Enum):
    WECOM = "wecom"  # 企业微信
    DINGTALK = "dingtalk"  # 钉钉
    FEISHU = "feishu"  # 飞书


class MessageType(str, enum.Enum):
    TEXT = "text"
    IMAGE = "image"
    FILE = "file"
    OTHER = "other"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        # 同一渠道的同一条消息只入库一次（回调重试去重）
        UniqueConstraint("channel", "external_id", name="uq_message_channel_external_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    channel: Mapped[Channel] = mapped_column(Enum(Channel, native_enum=False, length=16), index=True)
    external_id: Mapped[str] = mapped_column(String(128), comment="渠道侧消息 ID")

    group_id: Mapped[str] = mapped_column(String(128), index=True, comment="渠道侧群 ID")
    group_name: Mapped[str | None] = mapped_column(String(256))
    is_external_group: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否外部群（客户群）")

    sender_id: Mapped[str] = mapped_column(String(128), comment="渠道侧发送人 ID")
    sender_name: Mapped[str | None] = mapped_column(String(128))

    msg_type: Mapped[MessageType] = mapped_column(
        Enum(MessageType, native_enum=False, length=16), default=MessageType.TEXT
    )
    content: Mapped[str] = mapped_column(Text, comment="归一后的文本内容")

    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, comment="渠道侧发送时间")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    raw: Mapped[dict | None] = mapped_column(JSON, comment="渠道原始报文，便于排查与回溯")
