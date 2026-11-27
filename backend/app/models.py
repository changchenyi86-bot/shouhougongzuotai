"""统一消息模型：企业微信、钉钉、飞书的群消息都归一成 Message。"""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Enum, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

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


class Attachment(Base):
    """图片、文件：技术支持上传准备发出的，或从渠道下载的客户发来的。文件存在 settings.attachment_dir 下。"""

    __tablename__ = "attachments"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    filename: Mapped[str] = mapped_column(String(255))
    mime: Mapped[str] = mapped_column(String(128))
    size: Mapped[int]
    sha256: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(16), comment="upload：工作台上传；channel：从渠道下载")
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    @property
    def is_image(self) -> bool:
        return self.mime.startswith("image/")


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
    attachment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("attachments.id", ondelete="SET NULL"), comment="图片、文件等的本地副本"
    )
    media_error: Mapped[str | None] = mapped_column(Text, comment="图片、文件下载失败的原因")
    attachment: Mapped[Attachment | None] = relationship(lazy="selectin")


class SuggestionStatus(str, enum.Enum):
    GENERATING = "generating"  # AI 生成中
    READY = "ready"  # 已生成，待技术支持处理
    FAILED = "failed"  # 生成失败
    ADOPTED = "adopted"  # 原样采纳
    EDITED = "edited"  # 修改后采纳
    ESCALATED = "escalated"  # 转人工
    DISMISSED = "dismissed"  # 忽略（无需回复）
    CLOSED = "closed"  # 结束会话时还没回复，随会话一起结束


# 还没处理完的建议；结束会话时这些随会话结束
OPEN_SUGGESTION = {SuggestionStatus.GENERATING, SuggestionStatus.READY, SuggestionStatus.FAILED,
                   SuggestionStatus.ESCALATED}


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
    knowledge: Mapped[list | None] = mapped_column(
        JSON, comment="生成时从售后数字人查到的参考知识（不含全文），cited 标出建议实际引用的"
    )
    knowledge_error: Mapped[str | None] = mapped_column(Text, comment="知识库没查成的原因")

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
    suggestion_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("suggestions.id", ondelete="CASCADE"), index=True, comment="回复的是哪条 AI 建议；直接在会话里发的为空"
    )
    channel: Mapped[Channel] = mapped_column(Enum(Channel, native_enum=False, length=16))
    group_id: Mapped[str] = mapped_column(String(128), index=True)
    group_name: Mapped[str] = mapped_column(String(256), comment="群在客户端里显示的名字，发送前用它核对")
    content: Mapped[str] = mapped_column(Text, default="")
    attachment_ids: Mapped[list | None] = mapped_column(JSON, comment="要一起发出的图片、文件，按顺序")
    status: Mapped[SendStatus] = mapped_column(
        Enum(SendStatus, native_enum=False, length=16), default=SendStatus.QUEUED, index=True
    )
    agent_id: Mapped[str | None] = mapped_column(String(64), comment="领取任务的发送程序")
    error: Mapped[str | None] = mapped_column(Text)
    timed_out: Mapped[bool] = mapped_column(Boolean, default=False, comment="发送中超时、结果未知")
    replaced_by: Mapped[uuid.UUID | None] = mapped_column(comment="失败后重新发送时，接替它的新任务")
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


class GroupName(Base):
    """技术支持在工作台里手动填写的群名。渠道接口查不到群名时（如外部群没有查询权限）用它，
    要和群在客户端里显示的名字一致，自动发送靠它找群。"""

    __tablename__ = "group_names"

    channel: Mapped[Channel] = mapped_column(Enum(Channel, native_enum=False, length=16), primary_key=True)
    group_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    updated_by: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class Role(str, enum.Enum):
    ADMIN = "admin"  # 管理员：看全部会话，管成员、群负责人和设置
    SUPPORT = "support"  # 技术支持：处理分给自己的会话


class StaffStatus(str, enum.Enum):
    ONLINE = "online"  # 在线：可以接新会话
    AWAY = "away"  # 离开：不接新会话


class Team(Base):
    """分组，如 BI 组、中台组（含运维）。负责人不在线时，会话分给同组的人。"""

    __tablename__ = "teams"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(32), unique=True)
    sort: Mapped[int] = mapped_column(default=0)


class Staff(Base):
    """工作台成员（技术支持、管理员），用账号密码登录。"""

    __tablename__ = "staff"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(32), unique=True, comment="姓名，显示在处理记录里")
    username: Mapped[str] = mapped_column(String(64), unique=True, comment="登录账号")
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(Enum(Role, native_enum=False, length=16), default=Role.SUPPORT)
    team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teams.id", ondelete="SET NULL"))
    active: Mapped[bool] = mapped_column(Boolean, default=True, comment="停用后不能登录")
    status: Mapped[StaffStatus] = mapped_column(
        Enum(StaffStatus, native_enum=False, length=16), default=StaffStatus.ONLINE, comment="自己切换的在线状态"
    )
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="最近一次打开工作台")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    team: Mapped[Team | None] = relationship(lazy="selectin")


class LoginToken(Base):
    """登录凭证：浏览器 Cookie 里放原文，这里只存它的 SHA-256。"""

    __tablename__ = "login_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    staff_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("staff.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Conversation(Base):
    """群里的一次会话。

    客户发来要处理的消息、这个群又没有进行中的会话时开始，按分流规则分给一个人处理；
    技术支持结束会话时结束，群里还没回复的客户消息随会话结束。之后客户再发消息，就是新的一次会话。
    """

    __tablename__ = "conversations"
    __table_args__ = (
        Index("ix_conversations_group", "channel", "group_id", "ended_at"),
        # 一个群同时只有一个进行中的会话
        Index("uq_conversations_open", "channel", "group_id", unique=True,
              postgresql_where=text("ended_at IS NULL"), sqlite_where=text("ended_at IS NULL")),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    channel: Mapped[Channel] = mapped_column(Enum(Channel, native_enum=False, length=16))
    group_id: Mapped[str] = mapped_column(String(128))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("staff.id", ondelete="SET NULL"), index=True, comment="分给谁处理；为空表示等管理员分配"
    )
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="为空表示进行中")
    ended_by: Mapped[str | None] = mapped_column(String(64), comment="谁结束的")
    closed_count: Mapped[int] = mapped_column(default=0, comment="随会话结束的未回复客户消息数")
    assignee: Mapped["Staff | None"] = relationship(lazy="selectin")


class GroupOwner(Base):
    """群负责人：这个群的新会话先分给负责人；负责人不在线时分给同组的人。"""

    __tablename__ = "group_owners"

    channel: Mapped[Channel] = mapped_column(Enum(Channel, native_enum=False, length=16), primary_key=True)
    group_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("staff.id", ondelete="CASCADE"), index=True)
    updated_by: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
    owner: Mapped["Staff"] = relationship(lazy="selectin")


class SyncCursor(Base):
    """渠道拉取进度（如企业微信会话存档的 seq）。"""

    __tablename__ = "sync_cursors"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(128))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

