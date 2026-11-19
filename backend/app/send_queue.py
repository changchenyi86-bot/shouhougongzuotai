"""自动发送队列。

流程：技术支持在工作台确认发送 → 建一条发送任务排队 → 电脑端发送程序（windows-agent/）领取任务，
操作官方企业微信客户端打开对应的群、核对群名、粘贴并发送 → 回报成功或失败。

安全约束：
- 只有人在工作台点了确认才会建任务；
- 发送端离线、群名未知、有同名群时直接拒绝，提示人工发送，不排队碰运气；
- 两次发送之间有最小间隔，每个群每小时有条数上限；
- 排队太久的任务自动取消（避免发送端恢复后才发出过时的回复）；
- 发送中太久没回报的任务标记为「结果未知」，不自动重发，由人到群里确认，避免重复发送。
"""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import distinct, func, select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Channel, Message, SendAgent, SendStatus, SendTask, Suggestion

S = SendStatus
# 发送还没有结果、需要人跟进的状态：这些建议留在「待处理」里
PENDING = {S.QUEUED, S.SENDING, S.FAILED, S.CANCELLED}
NEEDS_ACTION = {S.FAILED, S.CANCELLED}  # 没发出去，要有人重发或手动处理
SUPPORTED_CHANNELS = {Channel.WECOM}


class SendRejected(Exception):
    """不能自动发送（给人看的原因）。"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    # SQLite 读回的时间不带时区，统一按 UTC 处理
    return dt.replace(tzinfo=timezone.utc) if dt and dt.tzinfo is None else dt


def enabled() -> bool:
    return bool(settings.send_agent_token)


def online_agents(session: Session) -> list[SendAgent]:
    cutoff = _now() - timedelta(seconds=settings.send_agent_offline_seconds)
    return list(session.scalars(select(SendAgent).where(SendAgent.last_seen >= cutoff).order_by(SendAgent.agent_id)))


def ready_agents(session: Session) -> list[SendAgent]:
    return [a for a in online_agents(session) if a.wecom_ready]


def target_group_name(session: Session, channel: Channel, group_id: str) -> str:
    """群在企业微信客户端里显示的名字。发送程序靠它搜索并核对，不唯一时拒绝，宁可不发也不发错群。"""
    name = session.scalar(
        select(Message.group_name)
        .where(Message.channel == channel, Message.group_id == group_id,
               Message.group_name.is_not(None), Message.group_name != "")
        .order_by(Message.sent_at.desc())
        .limit(1)
    )
    if not name:
        raise SendRejected("还不知道这个群在企业微信里的名字，无法自动发送，请复制后手动发送")
    others = session.scalar(
        select(func.count(distinct(Message.group_id))).where(
            Message.channel == channel, Message.group_name == name, Message.group_id != group_id
        )
    )
    if others:
        raise SendRejected(f"有不止一个群叫「{name}」，为避免发错群，请复制后手动发送（或在企业微信里把群名改成不同的）")
    return name


def _check_group_rate(session: Session, group_id: str) -> None:
    since = _now() - timedelta(hours=1)
    count = session.scalar(
        select(func.count(SendTask.id)).where(
            SendTask.group_id == group_id, SendTask.created_at >= since,
            SendTask.status.in_([S.QUEUED, S.SENDING, S.SENT]),
        )
    )
    if count >= settings.send_group_max_per_hour:
        raise SendRejected(f"这个群 1 小时内已自动发送 {count} 条，达到上限，请复制后手动发送")


def latest_task(session: Session, suggestion_id: uuid.UUID) -> SendTask | None:
    return session.scalar(
        select(SendTask).where(SendTask.suggestion_id == suggestion_id).order_by(SendTask.created_at.desc()).limit(1)
    )


def _sync(session: Session, task: SendTask) -> None:
    """把任务状态同步到建议上（只同步该建议最近的一次任务）。"""
    if task.suggestion_id is None:
        return
    latest = latest_task(session, task.suggestion_id)
    if latest is not None and latest.id != task.id:
        return
    sug = session.get(Suggestion, task.suggestion_id)
    sug.send_status = task.status
    sug.send_error = task.error if task.status == S.FAILED else None
    sug.sent_at = task.finished_at if task.status == S.SENT else None


def enqueue(session: Session, channel: Channel, group_id: str, content: str, attachment_ids: list[uuid.UUID],
            operator: str | None, sug: Suggestion | None = None) -> SendTask:
    """建发送任务（调用方负责 commit）。不能自动发送时抛 SendRejected。"""
    if channel not in SUPPORTED_CHANNELS:
        raise SendRejected("这个渠道暂不支持自动发送，请复制后手动发送")
    if not enabled():
        raise SendRejected("自动发送未启用，请复制后手动发送")
    if not ready_agents(session):
        raise SendRejected("电脑端发送程序不在线或企业微信未登录，请复制后手动发送")
    if not content.strip() and not attachment_ids:
        raise SendRejected("没有要发送的内容")
    name = target_group_name(session, channel, group_id)
    _check_group_rate(session, group_id)
    task = SendTask(suggestion_id=sug.id if sug else None, channel=channel, group_id=group_id, group_name=name,
                    content=content, attachment_ids=[str(a) for a in attachment_ids] or None,
                    created_by=operator, created_at=_now())
    session.add(task)
    session.flush()
    _sync(session, task)
    if sug is not None:
        sug.delivery = "agent"
    return task


def expire_stale(session: Session) -> None:
    """取消排队太久的任务；发送中太久没回报的标记为结果未知。"""
    now = _now()
    queued_cutoff = now - timedelta(minutes=settings.send_queue_timeout_minutes)
    stale = select(SendTask).where(SendTask.status == S.QUEUED, SendTask.created_at < queued_cutoff)
    for task in session.scalars(stale).all():
        task.status, task.finished_at = S.FAILED, now
        task.error = (f"排队超过 {settings.send_queue_timeout_minutes} 分钟没有发出（发送程序可能离线），已取消，消息没有发出。"
                      "请重新发送或复制后手动发送")
        _sync(session, task)
    sending_cutoff = now - timedelta(minutes=settings.send_result_timeout_minutes)
    for task in session.scalars(
        select(SendTask).where(SendTask.status == S.SENDING, SendTask.claimed_at < sending_cutoff)
    ).all():
        task.status, task.finished_at, task.timed_out = S.FAILED, now, True
        task.error = "发送程序一直没有回报结果，可能发出了也可能没有。请先到企业微信群里看一下，确认没发出再重新发送，避免重复"
        _sync(session, task)
    session.commit()


def claim(session: Session, agent_id: str) -> tuple[SendTask | None, float]:
    """发送程序领取下一条任务。返回 (任务或 None, 建议多少秒后再来)。"""
    expire_stale(session)
    last = _aware(session.scalar(select(func.max(SendTask.claimed_at))))
    if last is not None:
        wait = settings.send_min_interval_seconds - (_now() - last).total_seconds()
        if wait > 0:
            return None, wait
    task = session.scalar(
        select(SendTask).where(SendTask.status == S.QUEUED).order_by(SendTask.created_at)
        .limit(1).with_for_update(skip_locked=True)
    )
    if task is None:
        return None, 0
    # 条件更新：多个发送程序同时领取时只有一个能拿到
    claimed = session.execute(
        update(SendTask).where(SendTask.id == task.id, SendTask.status == S.QUEUED)
        .values(status=S.SENDING, agent_id=agent_id, claimed_at=_now())
    ).rowcount
    if not claimed:
        session.rollback()
        return None, 0
    session.refresh(task)
    _sync(session, task)
    session.commit()
    return task, 0


class ReportConflict(Exception):
    pass


def report(session: Session, task_id: uuid.UUID, agent_id: str, ok: bool, error: str | None) -> SendTask | None:
    """发送程序回报结果。重复回报同一结果是幂等的。"""
    task = session.get(SendTask, task_id, with_for_update=True)
    if task is None:
        return None
    if task.agent_id != agent_id:
        raise ReportConflict("这条任务不是由该发送程序领取的")
    new_status = S.SENT if ok else S.FAILED
    # 超时被标记为「结果未知」后，迟到的真实结果仍然接受
    if task.status == S.SENDING or (task.status == S.FAILED and task.timed_out):
        task.status, task.finished_at, task.timed_out = new_status, _now(), False
        task.error = None if ok else (error or "发送失败")
        _sync(session, task)
        session.commit()
    elif task.status != new_status:
        raise ReportConflict(f"任务当前状态为 {task.status.value}，不能改为 {new_status.value}")
    return task


def heartbeat(session: Session, agent_id: str, wecom_ready: bool, detail: str | None, version: str | None) -> None:
    agent = session.get(SendAgent, agent_id) or SendAgent(agent_id=agent_id)
    agent.last_seen, agent.wecom_ready, agent.detail, agent.version = _now(), wecom_ready, detail, version
    session.add(agent)
    session.commit()


# ---------- 失败或取消后的处理（按任务） ----------


def resend_task(session: Session, task: SendTask, operator: str | None) -> SendTask:
    if task.status not in (S.FAILED, S.CANCELLED):
        raise ReportConflict("只有发送失败或已取消的可以重新发送")
    if task.replaced_by is not None:
        raise ReportConflict("已经重新发送过了")
    sug = session.get(Suggestion, task.suggestion_id) if task.suggestion_id else None
    new = enqueue(session, task.channel, task.group_id, task.content,
                  [uuid.UUID(a) for a in task.attachment_ids or []], operator, sug)
    task.replaced_by = new.id  # 原任务由新任务接替，不再算待处理
    return new


def cancel_task(session: Session, task: SendTask) -> None:
    if task.status != S.QUEUED:
        raise ReportConflict("只能取消还在排队的发送（已经在发送中的无法撤回）")
    claimed = session.execute(
        update(SendTask).where(SendTask.id == task.id, SendTask.status == S.QUEUED)
        .values(status=S.CANCELLED, finished_at=_now())
    ).rowcount
    if not claimed:
        session.rollback()
        raise ReportConflict("发送程序刚刚开始发送，无法取消")
    session.refresh(task)
    _sync(session, task)


def dismiss_task(session: Session, task: SendTask) -> None:
    """发送失败或取消后，技术支持已自己在企业微信里发出（或不再需要发）。"""
    if task.status not in (S.FAILED, S.CANCELLED):
        raise ReportConflict("只有发送失败或已取消的可以标记为已手动处理")
    task.status = S.MANUAL
    _sync(session, task)
    if task.suggestion_id:
        session.get(Suggestion, task.suggestion_id).delivery = "manual"


# ---------- 按建议（取该建议最近一次任务） ----------


def _latest_or_conflict(session: Session, sug: Suggestion) -> SendTask:
    task = latest_task(session, sug.id)
    if task is None:
        raise ReportConflict("这条回复没有自动发送过")
    return task


def resend(session: Session, sug: Suggestion, operator: str | None) -> SendTask:
    return resend_task(session, _latest_or_conflict(session, sug), operator)


def cancel(session: Session, sug: Suggestion) -> None:
    cancel_task(session, _latest_or_conflict(session, sug))


def mark_manual(session: Session, sug: Suggestion) -> None:
    dismiss_task(session, _latest_or_conflict(session, sug))
