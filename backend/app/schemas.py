import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.models import Channel, MessageType, SendStatus, SuggestionStatus


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


class AttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    mime: str
    size: int


class MessageOut(MessageIn):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    received_at: datetime
    raw: dict | None = Field(default=None, exclude=True)
    attachment: AttachmentOut | None = None
    media_error: str | None = None

    @computed_field
    @property
    def from_customer(self) -> bool:
        """发送人是客户（否则是本公司员工）。"""
        from app.channels import ADAPTERS

        return ADAPTERS[self.channel].is_customer(self)


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
    send_status: SendStatus | None = None
    send_error: str | None = None
    sent_at: datetime | None = None
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
    send: bool = Field(default=False, description="true：交给电脑端发送程序自动发到群里；false：复制后人工发送")


class HandleIn(BaseModel):
    note: str | None = None
    operator: str | None = Field(default=None, max_length=64)


class AdoptOut(BaseModel):
    suggestion: SuggestionOut
    delivery: str = Field(description="manual：需复制后在客户端发送；agent：已加入自动发送队列")
    detail: str = ""


# ---------- 自动发送 ----------


class OperatorIn(BaseModel):
    operator: str | None = Field(default=None, max_length=64)


class AgentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    agent_id: str
    last_seen: datetime
    wecom_ready: bool
    detail: str | None
    version: str | None


class SendStatusOut(BaseModel):
    enabled: bool = Field(description="服务端是否配置了发送程序口令")
    ready: bool = Field(description="是否有在线且企业微信已登录的发送程序")
    agents: list[AgentOut]


class AgentHeartbeatIn(BaseModel):
    agent_id: str = Field(min_length=1, max_length=64)
    wecom_ready: bool
    detail: str | None = Field(default=None, max_length=1000)
    version: str | None = Field(default=None, max_length=32)


class AgentClaimIn(BaseModel):
    agent_id: str = Field(min_length=1, max_length=64)


class AgentTask(BaseModel):
    id: uuid.UUID
    channel: Channel
    group_id: str
    group_name: str
    content: str
    attachments: list[AttachmentOut] = Field(description="先逐个发出附件，再发文字")
    created_at: datetime


class AgentClaimOut(BaseModel):
    task: AgentTask | None
    retry_after: float = Field(description="没有任务或需要限速时，建议多少秒后再领取")


class AgentResultIn(BaseModel):
    agent_id: str = Field(min_length=1, max_length=64)
    ok: bool
    error: str | None = Field(default=None, max_length=2000)


# ---------- 群聊记录 ----------


class GroupOut(BaseModel):
    channel: Channel
    group_id: str
    group_name: str | None
    is_external_group: bool
    last_message_at: datetime
    last_content: str
    message_count: int
    pending_count: int = Field(description="待处理数：待回复的客户消息、还没发出或发送失败的回复")
    escalated_count: int = Field(description="已转人工、还没回复的客户消息数")


class SendTaskOut(BaseModel):
    id: uuid.UUID
    suggestion_id: uuid.UUID | None
    content: str
    attachments: list[AttachmentOut]
    status: SendStatus
    error: str | None
    created_by: str | None
    created_at: datetime
    finished_at: datetime | None


class ConversationOut(BaseModel):
    items: list[InboxItem] = Field(description="按时间从早到晚")
    has_more_before: bool
    has_more_after: bool
    outbox: list[SendTaskOut] = Field(default=[], description="工作台发出、还没出现在聊天记录里的消息（排队、发送中、失败，及刚发出的）")


class SendIn(BaseModel):
    channel: Channel
    group_id: str = Field(max_length=128)
    content: str = ""
    attachment_ids: list[uuid.UUID] = Field(default=[], max_length=9)
    suggestion_id: uuid.UUID | None = Field(default=None, description="回复的是哪条 AI 建议，发送后该建议记为已采纳")
    operator: str | None = Field(default=None, max_length=64)
