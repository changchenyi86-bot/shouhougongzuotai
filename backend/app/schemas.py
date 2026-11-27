import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from app.models import Channel, MessageType, Role, SendStatus, StaffStatus, SuggestionStatus


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


class KnowledgeRef(BaseModel):
    """生成建议时从售后数字人查到的一条参考知识。"""

    ref: str = Field(description="提示词里的编号，如 K1")
    kind: str = Field(description="card 纠错卡 / manual 产品手册 / faq / ticket 历史工单 / bug 缺陷单 等")
    branch: str = Field(description="适用分支：有数 / 知数 / 通用")
    title: str
    source_id: str = Field(description="纠错卡的 card_id，或数字人知识库里的文件名")
    url: str | None = Field(default=None, description="原文链接（产品手册、FAQ 是帮助中心，工单是 Overmind）")
    card_status: str | None = Field(default=None, description="纠错卡状态，draft 表示技术支持还没确认")
    snippet: str = ""
    cited: bool = Field(description="建议是否实际引用了这条")


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
    knowledge: list[KnowledgeRef] = Field(default=[], description="参考知识，建议引用的排在前面")
    knowledge_error: str | None = None
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

    @field_validator("knowledge", mode="before")
    @classmethod
    def _cited_first(cls, v):
        return sorted(v or [], key=lambda k: not (k.get("cited") if isinstance(k, dict) else getattr(k, "cited", False)))


class InboxItem(BaseModel):
    message: MessageOut
    suggestion: SuggestionOut | None


class AdoptIn(BaseModel):
    content: str | None = Field(default=None, description="修改后的回复；不传则原样采纳 AI 建议")
    send: bool = Field(default=False, description="true：交给电脑端发送程序自动发到群里；false：复制后人工发送")


class HandleIn(BaseModel):
    note: str | None = None


class AdoptOut(BaseModel):
    suggestion: SuggestionOut
    delivery: str = Field(description="manual：需复制后在客户端发送；agent：已加入自动发送队列")
    detail: str = ""


# ---------- 自动发送 ----------


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
    wait: float = Field(default=0, ge=0, le=30, description="没有任务时最多等多少秒再返回；0 表示立刻返回")


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


class ConversationEndOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ended_by: str | None
    ended_at: datetime
    closed_count: int = Field(description="随会话结束的未回复客户消息数")


class ConversationOut(BaseModel):
    items: list[InboxItem] = Field(description="按时间从早到晚")
    has_more_before: bool
    has_more_after: bool
    outbox: list[SendTaskOut] = Field(default=[], description="工作台发出、还没出现在聊天记录里的消息（排队、发送中、失败，及刚发出的）")
    ends: list[ConversationEndOut] = Field(default=[], description="这个群最近的会话结束记录，按时间从早到晚")


class EndIn(BaseModel):
    channel: Channel
    group_id: str = Field(min_length=1, max_length=128)


class GroupNameIn(BaseModel):
    channel: Channel
    group_id: str = Field(min_length=1, max_length=128)
    name: str = Field(max_length=256, description="和群在企业微信客户端里显示的名字一致")

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("群名不能为空")
        return v


class GroupNameOut(BaseModel):
    channel: Channel
    group_id: str
    name: str
    duplicate: bool = Field(description="还有别的群也叫这个名字（这些群不能自动发送）")


class SendIn(BaseModel):
    channel: Channel
    group_id: str = Field(max_length=128)
    content: str = ""
    attachment_ids: list[uuid.UUID] = Field(default=[], max_length=9)
    suggestion_id: uuid.UUID | None = Field(default=None, description="回复的是哪条 AI 建议，发送后该建议记为已采纳")


# ---------- 登录、成员 ----------


class AuthState(BaseModel):
    needs_setup: bool = Field(description="还没有任何账号，要先设管理员")


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


def _password_ok(v: str) -> str:
    if len(v) < 8:
        raise ValueError("密码至少 8 位")
    return v


def _strip_required(v: str) -> str:
    v = v.strip()
    if not v:
        raise ValueError("不能为空")
    return v


class SetupIn(BaseModel):
    """第一次使用：设管理员账号。"""

    name: str = Field(max_length=32)
    username: str = Field(max_length=64)
    password: str = Field(max_length=128)

    _name = field_validator("name", "username")(_strip_required)
    _pw = field_validator("password")(_password_ok)


class TeamOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str


class StaffOut(BaseModel):
    id: uuid.UUID
    name: str
    username: str
    role: Role
    team: TeamOut | None
    active: bool
    status: StaffStatus
    online: bool = Field(description="设成在线，并且最近打开着工作台")
    last_seen: datetime | None


class StaffIn(BaseModel):
    name: str = Field(max_length=32)
    username: str = Field(max_length=64)
    password: str = Field(max_length=128)
    role: Role = Role.SUPPORT
    team_id: uuid.UUID | None = None

    _name = field_validator("name", "username")(_strip_required)
    _pw = field_validator("password")(_password_ok)


class StaffPatch(BaseModel):
    """管理员改成员：只传要改的字段。"""

    name: str | None = Field(default=None, max_length=32)
    role: Role | None = None
    team_id: uuid.UUID | None = None
    clear_team: bool = Field(default=False, description="设为不分组")
    active: bool | None = None
    password: str | None = Field(default=None, max_length=128, description="重设密码")

    @field_validator("name")
    @classmethod
    def _name(cls, v: str | None) -> str | None:
        return None if v is None else _strip_required(v)

    @field_validator("password")
    @classmethod
    def _pw(cls, v: str | None) -> str | None:
        return None if v is None else _password_ok(v)


class StatusIn(BaseModel):
    status: StaffStatus


class PasswordIn(BaseModel):
    old_password: str = Field(max_length=128)
    new_password: str = Field(max_length=128)

    _pw = field_validator("new_password")(_password_ok)
