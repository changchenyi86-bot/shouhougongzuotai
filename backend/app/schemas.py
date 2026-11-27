import uuid
from datetime import datetime

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from app.models import CandidateStatus, Channel, MessageType, Role, SendStatus, StaffStatus, SuggestionStatus


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

    @computed_field
    @property
    def sender_kind(self) -> Literal["internal", "wechat", "wecom", "external"]:
        """发送人来自哪里：本企业员工 / 微信用户 / 其他企业的企业微信用户 / 渠道分不出细类的外部人员。"""
        from app.channels import ADAPTERS

        return ADAPTERS[self.channel].sender_kind(self)


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
    author: str | None = Field(default=None, description="相似问答：写这条回复或知识的技术支持")
    group_name: str | None = Field(default=None, description="历史回复：当时在哪个群")
    score: float | None = Field(default=None, description="相似问答：和这条消息的相似度")


class CandidateBrief(BaseModel):
    """建议对应的候选知识（回复发出后 AI 判断要不要沉淀）。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: CandidateStatus
    question: str


class SuggestionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    message_id: uuid.UUID
    status: SuggestionStatus
    covers: list[uuid.UUID] | None = None
    merged_into: uuid.UUID | None = None
    question: str | None = None
    content: str | None
    can_answer: bool | None
    confidence: str | None
    reason: str | None
    model: str | None
    error: str | None
    knowledge: list[KnowledgeRef] = Field(default=[], description="参考知识，建议引用的排在前面")
    knowledge_error: str | None = None
    knowledge_note: str | None = Field(default=None, description="没查售后数字人的原因（寒暄、没配置）")
    needs_reply: bool | None = Field(default=None, description="AI 判断是否需要回复")
    auto_dismissed: bool = Field(default=False, description="AI 判为无需回复、自动标记的")
    reopened_by: str | None = None
    adoption: Literal["exact", "minor", "major", "unused"] | None = Field(
        default=None, description="采纳情况：原样采纳 / 小改 / 大改 / 未用")
    adoption_score: float | None = None
    via_chat: bool = Field(default=False, description="回复是和 AI 对话润色出来的")
    profile: dict | None = Field(default=None, description="生成时带给 AI 的客户档案：{customer, summary}")
    candidate: CandidateBrief | None = None
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

    @field_validator("covers", mode="before")
    @classmethod
    def _covers(cls, value, info):
        return value or [info.data["message_id"]]

    @field_validator("knowledge", mode="before")
    @classmethod
    def _cited_first(cls, v):
        return sorted(v or [], key=lambda k: not (k.get("cited") if isinstance(k, dict) else getattr(k, "cited", False)))


class InboxItem(BaseModel):
    message: MessageOut
    suggestion: SuggestionOut | None


class MentionIn(BaseModel):
    """回复里要真正 @ 的一个群成员。回复文字里要有「@名字」。"""

    model_config = ConfigDict(from_attributes=True)

    user_id: str = Field(default="", max_length=128)
    name: str = Field(min_length=1, max_length=128, description="群里显示的名字，和企业微信 @ 列表里的一致")
    kind: str = Field(default="", max_length=16, description="internal / wechat / wecom，同名时发送程序靠它区分")


class AdoptIn(BaseModel):
    content: str | None = Field(default=None, description="修改后的回复；不传则原样采纳 AI 建议")
    send: bool = Field(default=False, description="true：交给电脑端发送程序自动发到群里；false：复制后人工发送")
    via_chat: bool = Field(default=False, description="回复是从和 AI 的对话里填入的")
    mentions: list[MentionIn] = Field(default=[], max_length=20, description="回复里 @ 的群成员（比较采纳情况时去掉）")


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


class MemberOut(BaseModel):
    id: str
    name: str
    kind: Literal["internal", "wechat", "wecom", "external"]
    customer: bool
    last_spoke_at: datetime | None = Field(description="最近一次在这个群里说话的时间")
    duplicate: bool = Field(description="群里还有别人也叫这个名字（发送程序可能分不清，分不清时按普通文字发出）")


class AgentHeartbeatIn(BaseModel):
    agent_id: str = Field(min_length=1, max_length=64)
    wecom_ready: bool
    detail: str | None = Field(default=None, max_length=1000)
    version: str | None = Field(default=None, max_length=32)


class AgentClaimIn(BaseModel):
    agent_id: str = Field(min_length=1, max_length=64)
    wait: float = Field(default=0, ge=0, le=30, description="没有任务时最多等多少秒再返回；0 表示立刻返回")
    features: list[str] = Field(default=[], max_length=20,
                                description="发送程序会做的事。mentions：能在企业微信里真正 @ 人（旧版本没有）")


class AgentTask(BaseModel):
    id: uuid.UUID
    channel: Channel
    group_id: str
    group_name: str
    content: str
    attachments: list[AttachmentOut] = Field(description="先逐个发出附件，再发文字")
    mentions: list[MentionIn] = Field(default=[], description="文字里要真正 @ 的人，按出现的先后")
    created_at: datetime


class AgentClaimOut(BaseModel):
    task: AgentTask | None
    retry_after: float = Field(description="没有任务或需要限速时，建议多少秒后再领取")


class AgentResultIn(BaseModel):
    agent_id: str = Field(min_length=1, max_length=64)
    ok: bool
    error: str | None = Field(default=None, max_length=2000)
    warning: str | None = Field(default=None, max_length=2000, description="发出去了，但有没做到的（如有人没 @ 上）")


# ---------- 群聊记录 ----------


class StaffBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str


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
    owner: StaffBrief | None = Field(default=None, description="群负责人")
    assignee: StaffBrief | None = Field(default=None, description="进行中的会话分给了谁")
    conversation_open: bool = Field(default=False, description="有进行中的会话")


class SendTaskOut(BaseModel):
    id: uuid.UUID
    suggestion_id: uuid.UUID | None
    content: str
    attachments: list[AttachmentOut]
    mentions: list[MentionIn] = []
    status: SendStatus
    error: str | None
    warning: str | None = None
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
    owner: StaffBrief | None = Field(default=None, description="群负责人")
    assignee: StaffBrief | None = Field(default=None, description="进行中的会话分给了谁")
    conversation_open: bool = Field(default=False, description="有进行中的会话")


class OwnerIn(BaseModel):
    channel: Channel
    group_id: str = Field(min_length=1, max_length=128)
    owner_id: uuid.UUID | None = Field(description="为空表示取消负责人")


class AssignIn(BaseModel):
    channel: Channel
    group_id: str = Field(min_length=1, max_length=128)
    staff_id: uuid.UUID


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
    via_chat: bool = Field(default=False, description="回复是从和 AI 的对话里填入的")
    mentions: list[MentionIn] = Field(default=[], max_length=20,
                                      description="文字里要真正 @ 的群成员，按在文字里出现的先后（文字里写「@名字」）")


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
    open_count: int = Field(default=0, description="手上进行中的会话数")
    has_password: bool = Field(default=True, description="设过密码；批量导入的成员要管理员设了密码才能登录")


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


# ---------- 和 AI 对话润色 ----------


class ChatIn(BaseModel):
    content: str = Field(min_length=1, max_length=2000, description="技术支持的方案、要点或修改意见")

    @field_validator("content")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("内容不能为空")
        return v


class ChatTurnOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: Literal["staff", "ai"]
    content: str
    note: str | None = None
    operator: str | None = None
    created_at: datetime


# ---------- 候选知识 ----------

Branch = Literal["有数", "知数", "通用"]
Kind = Literal["how_to", "fact", "troubleshoot", "not_supported"]


class CandidateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    suggestion_id: uuid.UUID
    status: CandidateStatus
    title: str
    question: str
    answer: str
    branch: str
    kind: str
    module: str
    reason: str | None
    customer_question: str
    customer_reply: str
    ai_suggestion: str | None
    group_name: str | None
    author: str | None
    confirmed_by: str | None
    confirmed_at: datetime | None
    reviewer: str | None
    reviewed_at: datetime | None
    review_note: str | None
    updated_by: str | None
    card_id: str | None
    synced_version: str | None
    synced_status: str | None
    synced_at: datetime | None
    created_at: datetime
    # 回到会话里的那条消息
    channel: Channel | None = None
    group_id: str | None = None
    message_id: uuid.UUID | None = None


class CandidateEdit(BaseModel):
    """确认或审核时可以改的问答内容；不传的字段保持不变。"""

    title: str | None = Field(default=None, max_length=256)
    question: str | None = Field(default=None, max_length=2000)
    answer: str | None = Field(default=None, max_length=8000)
    branch: Branch | None = None
    kind: Kind | None = None
    module: str | None = Field(default=None, max_length=64)
    note: str | None = Field(default=None, max_length=1000, description="审核意见")


class CandidateCounts(BaseModel):
    mine: int = Field(description="AI 建议沉淀、等我（当前登录的人）确认的")
    pending: int = Field(description="等审核的（不含我自己提交的）")
    by_status: dict[str, int]


class CardsOut(BaseModel):
    """写入售后数字人的 BI 纠错卡（审核通过的，和通过后又停用的）。"""

    cards: list[dict]
    unsynced: int = Field(description="还没写入或有变化、待同步的张数")


class CardsSyncedIn(BaseModel):
    version: str = Field(min_length=1, max_length=64, description="写入的卡库文件版本")
    card_ids: list[str]


# ---------- 采纳统计 ----------


class AdoptionRow(BaseModel):
    operator: str = Field(description="处理人；AI 自动标记的为「AI」，早期没登录时没填名字的为「未填写」")
    replied: int = Field(description="发出的回复（回复了客户消息的）")
    exact: int
    minor: int
    major: int
    unused: int
    via_chat: int = Field(description="其中经 AI 对话润色的")
    escalated: int = Field(description="转人工")
    dismissed: int = Field(description="标为无需回复")
    knowledge_submitted: int = Field(description="确认加入候选知识的")
    knowledge_approved: int = Field(description="自己的回复整理成的知识审核通过的")
    reviews: int = Field(description="审核过的候选知识")


class AdoptionWeek(BaseModel):
    week: str = Field(description="这周周一，YYYY-MM-DD（北京时间）")
    total: AdoptionRow
    people: list[AdoptionRow]


class AdoptionStats(BaseModel):
    weeks: list[AdoptionWeek] = Field(description="从近到远")


# ---------- 回复建议评测 ----------


class EvalSetOut(BaseModel):
    name: str
    total: int
    areas: dict[str, int]
    kinds: dict[str, int]
    updated_at: datetime | None
    reviewing: int = 0  # 标了待复核、暂不参与评测的题


class EvalImportResult(BaseModel):
    name: str
    added: int
    updated: int
    retired: int
    reviewing: int = 0


class EvalStartIn(BaseModel):
    set_name: str = Field(min_length=1, max_length=32)
    areas: list[str] | None = None  # 只跑哪几块；不传跑全部
    note: str | None = Field(default=None, max_length=500)


class EvalBucket(BaseModel):
    area: str
    kind: str | None = None
    total: int
    done: int
    passed: int
    errors: int
    unjudged: int
    retrieval_total: int
    retrieval_hit: int
    verdicts: dict[str, int]


class EvalRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    set_name: str
    areas: list[str] | None
    status: str
    total: int
    done: int = 0
    started_by: str | None
    started_at: datetime
    finished_at: datetime | None
    model: str | None
    note: str | None
    summary: list[EvalBucket] = []  # 按块


class EvalCompare(BaseModel):
    run_id: uuid.UUID
    started_at: datetime
    summary: list[EvalBucket]
    better: int  # 上一轮没通过、这一轮通过的
    worse: int  # 上一轮通过、这一轮没通过的


class EvalRunDetail(EvalRunOut):
    kinds: list[EvalBucket] = []  # 按块 × 题型
    previous: EvalCompare | None = None


class EvalResultOut(BaseModel):
    understood_question: str | None = None
    case_id: uuid.UUID
    code: str
    kind: str
    area: str
    branch: str
    question: str
    context: str
    expected: str
    must_not: str
    reference: str
    expect_reply: bool | None
    data: dict | None
    done: bool
    error: str | None = None
    reply: str | None = None
    can_answer: bool | None = None
    confidence: str | None = None
    needs_reply: bool | None = None
    reason: str | None = None
    knowledge: list[dict] | None = None
    knowledge_error: str | None = None
    retrieval_rank: int | None = None
    has_expected_docs: bool
    verdict: str | None = None
    verdict_reason: str | None = None
    passed: bool | None = None
    previous_passed: bool | None = None  # 上一轮这道题过没过；上一轮没跑这道题为空
    seconds: float | None = None


# ---- 客户档案 ----


def _strip_text(v: str) -> str:
    v = v.strip()
    if not v:
        raise ValueError("内容不能为空")
    return v


class CustomerEnvInstance(BaseModel):
    env: str = Field(description="生产、测试，或运维门户里起的环境名")
    version: str | None = None
    confirmed_at: str | None = Field(default=None, description="运维门户里确认版本的日期")


class CustomerEnv(BaseModel):
    product: str = Field(description="有数BI / 知数 / 指标平台 / 数据中台")
    tech_support: str | None = None
    ops_support: str | None = None
    instances: list[CustomerEnvInstance] = []


class CustomerMaintenance(BaseModel):
    line: str = Field(description="BI / BD")
    label: str = Field(description="有数BI / 数据中台")
    status: str | None = Field(default=None, description="在保 / 半过期 / 过期 / 流失 / 交付")
    structure: str | None = Field(default=None, description="如 1在保+2过期")
    next_expire: str | None = None
    expire: str | None = None


class CustomerProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    companies: list[str] = Field(default=[], description="签约公司")
    level: str | None = None
    industry: str | None = None
    sales: str | None = None
    purchased: list[str] = []
    business_status: str | None = None
    maintenance: list[CustomerMaintenance] = []
    environments: list[CustomerEnv] = []
    cluster_type: str | None = None
    synced_at: datetime

    @field_validator("companies", "purchased", "maintenance", "environments", mode="before")
    @classmethod
    def _none_as_empty(cls, v):
        return v or []


class CustomerNoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: Literal["note", "error"]
    content: str
    author: str
    author_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class ServiceRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    served_on: str
    category: str | None
    description: str | None
    solution: str | None
    resolved: int | None = Field(description="0 待跟进 / 1 已解决 / 2 部分解决 / 空 未评定")
    staff_name: str | None


class CustomerLinkOut(BaseModel):
    customer: str | None = None
    source: str | None = Field(default=None, description="bind 售后系统群绑定 / tasks 任务表群对照 / manual 人工选")
    candidates: list[str] = []
    updated_by: str | None = None


class CustomerSyncOut(BaseModel):
    configured: bool
    synced_at: datetime | None = None
    error: str | None = None
    maintenance_updated_on: str | None = Field(default=None, description="售后系统维保总览最后一次计算的日期")


class CustomerCardOut(BaseModel):
    group_name: str | None = None
    link: CustomerLinkOut
    profile: CustomerProfileOut | None = None
    alerts: list[str] = Field(default=[], description="只给内部看的提醒，如这个群对应产品线的维保已过期")
    notes: list[CustomerNoteOut] = []
    records: list[ServiceRecordOut] = Field(default=[], description="这个群最近的服务记录")
    record_total: int = 0
    sync: CustomerSyncOut
    can_link: bool = Field(description="当前的人能不能改这个群对应的客户（管理员、群负责人）")


class CustomerBrief(BaseModel):
    name: str
    companies: list[str] = []


class CustomerLinkIn(BaseModel):
    channel: Channel
    group_id: str = Field(min_length=1, max_length=128)
    customer: str | None = Field(default=None, max_length=100, description="客户简称；为空表示这个群不对应任何客户")


class CustomerNoteIn(BaseModel):
    channel: Channel
    group_id: str = Field(min_length=1, max_length=128)
    kind: Literal["note", "error"] = "note"
    content: str = Field(max_length=1000)

    @field_validator("content")
    @classmethod
    def _strip(cls, v: str) -> str:
        return _strip_text(v)


class CustomerNoteEdit(BaseModel):
    content: str = Field(max_length=1000)

    @field_validator("content")
    @classmethod
    def _strip(cls, v: str) -> str:
        return _strip_text(v)
