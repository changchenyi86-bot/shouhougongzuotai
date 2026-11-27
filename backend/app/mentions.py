"""回复里 @ 群成员：群成员名单（给工作台输入框选人）、发送前核对、比较采纳情况时去掉 @。

工作台发出的回复里写的是「@名字」，另外带上要 @ 的人（按在回复里出现的先后）。电脑端发送程序在企业微信里
打 @、输入名字、从弹出的成员列表里点中这个人，对方收到的才是真正的 @ 提醒；选不中时按普通文字发出并回报原因。
"""

import re
from collections import Counter
from datetime import timedelta

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models import Channel, Message, SendStatus, SendTask
from app.schemas import MemberOut, MentionIn

MAX_MENTIONS = 20


class MentionError(ValueError):
    pass


def _kind(channel: Channel, user_id: str) -> str:
    if channel == Channel.WECOM:
        from app.channels.wecom.archive import sender_kind

        return sender_kind(user_id)
    return "external"


def _directory_members(channel: Channel, group_id: str) -> dict[str, str]:
    if channel != Channel.WECOM:
        return {}
    from app.channels.wecom.archive import get_syncer

    syncer = get_syncer()
    if syncer is None or syncer.directory is None:
        return {}
    return syncer.directory.members(group_id)


def agent_accounts(session: Session, channel: Channel) -> set[str]:
    """发送电脑上登录的客服账号：工作台自动发出的回复，在会话存档里是谁说的。

    它在企业微信里 @ 不了自己，不列进成员名单。按「发出后 10 分钟内、同一个群里、内容一字不差」对上。
    """
    rows = session.execute(
        select(Message.sender_id, Message.sent_at, SendTask.created_at).join(SendTask, and_(
            SendTask.channel == Message.channel, SendTask.group_id == Message.group_id,
            SendTask.content == Message.content, Message.sent_at >= SendTask.created_at))
        .where(Message.channel == channel, SendTask.status == SendStatus.SENT, SendTask.content != "")
        .order_by(SendTask.created_at.desc()).limit(200)
    )
    return {sender for sender, sent, created in rows if sent - created <= timedelta(minutes=10)}


def group_members(session: Session, channel: Channel, group_id: str,
                  directory: dict[str, str] | None = None) -> list[MemberOut]:
    """群里能 @ 的人：企业微信外部群用群成员接口的名单（群昵称优先，和企业微信 @ 列表里显示的一致）；
    名单拿不到时（内部群接口不给名字、没配客户联系 Secret）用在群里说过话、知道名字的人。

    最近说过话的排在前面，其次客户在员工前面。没有名字的人没法在企业微信里选中，不列出来；
    发送电脑上的客服账号自己也不列出来。
    """
    names = _directory_members(channel, group_id) if directory is None else directory
    spoke = session.execute(
        select(Message.sender_id, func.max(Message.sender_name), func.max(Message.sent_at))
        .where(Message.channel == channel, Message.group_id == group_id)
        .group_by(Message.sender_id)
    ).all()
    last = {sender: at for sender, _, at in spoke}
    if not names:
        names = {sender: name for sender, name, _ in spoke if name}
    me = agent_accounts(session, channel)
    names = {user_id: name for user_id, name in names.items() if user_id not in me}
    counts = Counter(name for name in names.values())
    members = []
    for user_id, name in names.items():
        kind = _kind(channel, user_id)
        members.append(MemberOut(id=user_id, name=name, kind=kind, customer=kind != "internal",
                                 last_spoke_at=last.get(user_id), duplicate=counts[name] > 1))
    members.sort(key=lambda m: (m.last_spoke_at is None, -(m.last_spoke_at.timestamp() if m.last_spoke_at else 0),
                                not m.customer, m.name))
    return members


def check(channel: Channel, content: str, mentions: list[MentionIn]) -> list[dict]:
    """核对要 @ 的人都按顺序出现在回复里（「@名字」），返回存进发送任务的样子。"""
    if len(mentions) > MAX_MENTIONS:
        raise MentionError(f"一条回复最多 @ {MAX_MENTIONS} 个人")
    out, pos = [], 0
    for m in mentions:
        at = content.find("@" + m.name, pos)
        if at < 0:
            raise MentionError(f"回复里找不到「@{m.name}」，请重新选择要 @ 的人")
        out.append({"user_id": m.user_id, "name": m.name, "kind": _kind(channel, m.user_id) if m.user_id else ""})
        pos = at + 1 + len(m.name)
    return out


def without_mentions(content: str, names: list[str]) -> str:
    """去掉回复里的「@名字」，用来和 AI 建议比较采纳情况（填建议时自动加上的 @ 不算改动）。"""
    for name in sorted({n for n in names if n}, key=len, reverse=True):
        content = re.sub(re.escape("@" + name) + r"\s*", "", content)
    return content.strip()
