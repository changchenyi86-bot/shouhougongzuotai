"""统一消息模型：企业微信、钉钉、飞书的群消息都归一成 Message。"""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Enum, ForeignKey, String, Text, UniqueConstraint
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


class SuggestionStatus(str, enum.Enum):
    GENERATING = "generating"  # AI 生成中
    READY = "ready"  # 已生成，待技术支持处理
    FAILED = "failed"  # 生成失败
    ADOPTED = "adopted"  # 原样采纳
    EDITED = "edited"  # 修改后采纳
    ESCALATED = "escalated"  # 转人工
    DISMISSED = "dismissed"  # 忽略（无需回复）


class SendStatus(str, enum.Enum):
    QUEUED = "queued"  # 已确认发送，等待电脑端发送程序领取
    SENDING = "sending"  # 发送程序正在操作企业微信
    SENT = "sent"  # 已发出
    FAILED = "failed"  # 发送失败（或结果未知），需要人工处理
    CANCELLED = "cancelled"  # 排队时被取消
    MANUAL = "manual"  # 自动发送失败/取消后，技术支持已手动发送


class Suggestion(Base):
    """AI 对一条客户消息给出的回复建议，以及技术支持的处理结果。"""

    __tablename__ = "suggestions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), unique=True)
    status: Mapped[SuggestionStatus] = mapped_column(
        Enum(SuggestionStatus, native_enum=False, length=16), default=SuggestionStatus.GENERATING, index=True
    )

    content: Mapped[str | None] = mapped_column(Text, comment="AI 建议回复")
    can_answer: Mapped[bool | None] = mapped_column(Boolean, comment="AI 是否有把握回答")
    confidence: Mapped[str | None] = mapped_column(String(16), comment="high / medium / low")
    reason: Mapped[str | None] = mapped_column(Text, comment="AI 的判断依据或需要补充的信息")
    model: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text)

    final_content: Mapped[str | None] = mapped_column(Text, comment="实际发出的回复")
    delivery: Mapped[str | None] = mapped_column(String(16), comment="manual：复制后人工发送；agent：由电脑端发送程序自动发送")
    # 自动发送进度（取最近一次发送任务的状态，或 manual：改为人工发送）
    send_status: Mapped[SendStatus | None] = mapped_column(Enum(SendStatus, native_enum=False, length=16), index=True)
    send_error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text, comment="转人工备注")
    handled_by: Mapped[str | None] = mapped_column(String(64))
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class SendTask(Base):
    """一次自动发送：由电脑端发送程序操作官方企业微信客户端，把回复发到对应的群。"""

    __tablename__ = "send_tasks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    suggestion_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("suggestions.id", ondelete="CASCADE"), index=True)
    channel: Mapped[Channel] = mapped_column(Enum(Channel, native_enum=False, length=16))
    group_id: Mapped[str] = mapped_column(String(128), index=True)
    group_name: Mapped[str] = mapped_column(String(256), comment="群在客户端里显示的名字，发送前用它核对")
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[SendStatus] = mapped_column(
        Enum(SendStatus, native_enum=False, length=16), default=SendStatus.QUEUED, index=True
    )
    agent_id: Mapped[str | None] = mapped_column(String(64), comment="领取任务的发送程序")
    error: Mapped[str | None] = mapped_column(Text)
    timed_out: Mapped[bool] = mapped_column(Boolean, default=False, comment="发送中超时、结果未知")
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SendAgent(Base):
    """电脑端发送程序的在线状态（心跳）。"""

    __tablename__ = "send_agents"

    agent_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    wecom_ready: Mapped[bool] = mapped_column(Boolean, default=False, comment="企业微信已登录且窗口可用")
    detail: Mapped[str | None] = mapped_column(Text)
    version: Mapped[str | None] = mapped_column(String(32))


class SyncCursor(Base):
    """渠道拉取进度（如企业微信会话存档的 seq）。"""

    __tablename__ = "sync_cursors"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(128))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

