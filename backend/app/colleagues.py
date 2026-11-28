"""同事的个人微信：有的同事用个人微信（wm 开头，和客户一样是外部联系人）在客户群里回复。

渠道只按账号类型分客户和员工，这些同事会被当成客户：他们的话会生成 AI 建议，他们的回复也不算「已回」。
管理员把他们加进名单后，名单上的账号按我方处理。名单很短，缓存在内存里，改名单时立即刷新，平时每分钟重读一次。
"""

import threading
import time
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Channel, ColleagueAccount, Message, OPEN_SUGGESTION, Suggestion, SuggestionStatus

REFRESH_SECONDS = 60
# 候选：显示名带这些字的外部账号
NAME_HINTS = ("网易", "技术支持", "数帆", "有数", "知数", "售后")
MIN_GROUPS = 3  # 在这么多个客户群里都说过话的外部账号，也列为候选（客户一般只在自己的群里）

_lock = threading.Lock()
_cache: tuple[float, frozenset[tuple[str, str]]] = (0.0, frozenset())


def _load() -> frozenset[tuple[str, str]]:
    from app import db

    with db.SessionLocal() as session:
        rows = session.execute(select(ColleagueAccount.channel, ColleagueAccount.sender_id)).all()
    return frozenset((Channel(c).value, s) for c, s in rows)


def refresh() -> None:
    global _cache
    with _lock:
        _cache = (time.monotonic(), _load())


def is_colleague(channel, sender_id: str) -> bool:
    global _cache
    if time.monotonic() - _cache[0] > REFRESH_SECONDS:
        try:
            refresh()
        except Exception:  # noqa: BLE001  数据库暂时连不上时沿用上次的名单，过一会儿再读
            _cache = (time.monotonic(), _cache[1])
    return (Channel(channel).value, sender_id) in _cache[1]


def close_open_suggestions(session: Session, channel: Channel, sender_id: str, who: str) -> int:
    """名单上的人以前被当成客户、还没处理的建议：随这次加入名单一起结束（不删，留记录）。"""
    rows = session.scalars(
        select(Suggestion).join(Message, Message.id == Suggestion.message_id)
        .where(Message.channel == channel, Message.sender_id == sender_id, Suggestion.status.in_(OPEN_SUGGESTION))
    ).all()
    now = datetime.now(timezone.utc)
    for sug in rows:
        sug.status, sug.handled_by, sug.handled_at = SuggestionStatus.CLOSED, who, now
        sug.note = "发言人是同事的个人微信，已不按客户处理"
    return len(rows)


def candidates(session: Session, limit: int = 50) -> list[dict]:
    """可能是同事的外部账号：显示名带「网易」「技术支持」等，或在好几个客户群里都说过话。已在名单上的不列。"""
    from app.channels.wecom.archive import is_external_user

    listed = {(Channel(c).value, s) for c, s in session.execute(
        select(ColleagueAccount.channel, ColleagueAccount.sender_id)).all()}
    rows = session.execute(
        select(Message.channel, Message.sender_id, func.max(Message.sender_name),
               func.count(func.distinct(Message.group_id)), func.count(Message.id), func.max(Message.sent_at))
        .where(Message.is_external_group.is_(True))
        .group_by(Message.channel, Message.sender_id)
    ).all()
    out = []
    for channel, sender_id, name, groups, count, last in rows:
        key = (Channel(channel).value, sender_id)
        if key in listed or not is_external_user(sender_id):
            continue
        name = name or ""
        hint = next((h for h in NAME_HINTS if h in name), None)
        if hint is None and groups < MIN_GROUPS:
            continue
        reason = f"名字里有「{hint}」" if hint else f"在 {groups} 个客户群里说过话"
        out.append({"channel": key[0], "sender_id": sender_id, "name": name, "groups": groups, "messages": count,
                    "last_at": last, "reason": reason})
    out.sort(key=lambda r: (-r["groups"], -r["messages"]))
    return out[:limit]
