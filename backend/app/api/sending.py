"""自动发送：工作台查看发送程序状态、重发/取消/改为手动；电脑端发送程序的心跳、领取任务、回报结果。"""

import asyncio
import hmac
import time
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import routing, send_queue
from app.auth import current_staff
from app.config import settings
from app.db import get_session
from app.models import Attachment, Message, SendTask, Staff, Suggestion
from app.schemas import (
    AgentClaimIn,
    AgentClaimOut,
    AgentHeartbeatIn,
    AgentOut,
    AgentResultIn,
    AgentTask,
    AttachmentOut,
    MentionIn,
    SendStatusOut,
    SendTaskOut,
    SuggestionOut,
)

router = APIRouter(prefix="/api", dependencies=[Depends(current_staff)])


# ---------- 工作台 ----------


@router.get("/send/status", response_model=SendStatusOut)
def send_status(session: Session = Depends(get_session)) -> SendStatusOut:
    agents = send_queue.online_agents(session)
    return SendStatusOut(
        enabled=send_queue.enabled(),
        ready=send_queue.enabled() and any(a.wecom_ready for a in agents),
        agents=[AgentOut.model_validate(a) for a in agents],
    )


def _suggestion(session: Session, suggestion_id: uuid.UUID, staff: Staff) -> Suggestion:
    sug = session.get(Suggestion, suggestion_id, with_for_update=True)
    if sug is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "建议不存在")
    msg = session.get(Message, sug.message_id)
    routing.ensure_access(session, staff, msg.channel, msg.group_id)
    return sug


@router.post("/suggestions/{suggestion_id}/resend", response_model=SuggestionOut)
def resend(suggestion_id: uuid.UUID, staff: Staff = Depends(current_staff),
           session: Session = Depends(get_session)) -> Suggestion:
    """自动发送失败或取消后，确认再发一次。"""
    sug = _suggestion(session, suggestion_id, staff)
    try:
        send_queue.resend(session, sug, staff.name)
    except (send_queue.SendRejected, send_queue.ReportConflict) as exc:
        session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    session.commit()
    return sug


@router.post("/suggestions/{suggestion_id}/cancel-send", response_model=SuggestionOut)
def cancel_send(suggestion_id: uuid.UUID, staff: Staff = Depends(current_staff),
                session: Session = Depends(get_session)) -> Suggestion:
    """取消还在排队的自动发送。"""
    sug = _suggestion(session, suggestion_id, staff)
    try:
        send_queue.cancel(session, sug)
    except send_queue.ReportConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    session.commit()
    return sug


@router.post("/suggestions/{suggestion_id}/mark-manual-sent", response_model=SuggestionOut)
def mark_manual_sent(suggestion_id: uuid.UUID, staff: Staff = Depends(current_staff),
                     session: Session = Depends(get_session)) -> Suggestion:
    """自动发送失败或取消后，技术支持已复制到企业微信手动发出。"""
    sug = _suggestion(session, suggestion_id, staff)
    try:
        send_queue.mark_manual(session, sug)
    except send_queue.ReportConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    session.commit()
    return sug


def attachments_of(session: Session, task: SendTask) -> list[Attachment]:
    ids = [uuid.UUID(a) for a in task.attachment_ids or []]
    found = {a.id: a for a in session.scalars(select(Attachment).where(Attachment.id.in_(ids)))} if ids else {}
    return [found[i] for i in ids if i in found]


def task_out(session: Session, task: SendTask) -> SendTaskOut:
    return SendTaskOut(
        id=task.id, suggestion_id=task.suggestion_id, content=task.content,
        attachments=[AttachmentOut.model_validate(a) for a in attachments_of(session, task)],
        mentions=[MentionIn.model_validate(m) for m in task.mentions or []],
        status=task.status, error=task.error, warning=task.warning, created_by=task.created_by,
        created_at=task.created_at, finished_at=task.finished_at,
    )


def _task(session: Session, task_id: uuid.UUID, staff: Staff) -> SendTask:
    task = session.get(SendTask, task_id, with_for_update=True)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "发送记录不存在")
    routing.ensure_access(session, staff, task.channel, task.group_id)
    return task


@router.post("/send-tasks/{task_id}/resend", response_model=SendTaskOut)
def resend_task(task_id: uuid.UUID, staff: Staff = Depends(current_staff),
                session: Session = Depends(get_session)) -> SendTaskOut:
    """发送失败或取消后，确认再发一次。"""
    try:
        new = send_queue.resend_task(session, _task(session, task_id, staff), staff.name)
    except (send_queue.SendRejected, send_queue.ReportConflict) as exc:
        session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    session.commit()
    return task_out(session, new)


@router.post("/send-tasks/{task_id}/cancel", response_model=SendTaskOut)
def cancel_task(task_id: uuid.UUID, staff: Staff = Depends(current_staff),
                session: Session = Depends(get_session)) -> SendTaskOut:
    task = _task(session, task_id, staff)
    try:
        send_queue.cancel_task(session, task)
    except send_queue.ReportConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    session.commit()
    return task_out(session, task)


@router.post("/send-tasks/{task_id}/mark-manual-sent", response_model=SendTaskOut)
def dismiss_task(task_id: uuid.UUID, staff: Staff = Depends(current_staff),
                 session: Session = Depends(get_session)) -> SendTaskOut:
    """失败或取消后，已自己在企业微信里发出（或不用再发）。"""
    task = _task(session, task_id, staff)
    try:
        send_queue.dismiss_task(session, task)
    except send_queue.ReportConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    session.commit()
    return task_out(session, task)


# ---------- 电脑端发送程序（需口令） ----------


def require_agent(authorization: str = Header(default="")) -> None:
    if not settings.send_agent_token:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "服务端未启用自动发送（SEND_AGENT_TOKEN 未配置）")
    expected = f"Bearer {settings.send_agent_token}"
    if not hmac.compare_digest(authorization.encode(), expected.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "发送程序口令不正确")


agent_router = APIRouter(prefix="/api/agent", dependencies=[Depends(require_agent)])


@agent_router.post("/heartbeat")
def heartbeat(body: AgentHeartbeatIn, session: Session = Depends(get_session)) -> dict:
    send_queue.heartbeat(session, body.agent_id, body.wecom_ready, body.detail, body.version)
    return {"ok": True}


CLAIM_POLL_SECONDS = 0.1  # 发送程序等任务时，服务端多久看一次有没有新任务
CLAIM_SWEEP_SECONDS = 5  # 等任务期间多久清理一次过期任务


async def _nap(seconds: float) -> None:
    await asyncio.sleep(seconds)


@agent_router.post("/claim", response_model=AgentClaimOut)
async def claim(body: AgentClaimIn, request: Request, session: Session = Depends(get_session)) -> AgentClaimOut:
    """领取下一条任务。wait > 0 时没有任务不立刻返回，最多等 wait 秒：一有任务（并且限速允许）马上交给发送程序。

    这样确认发送后不用等发送程序下一次来问（原来每 3 秒问一次，平均要多等 1.5 秒）。
    等待期间发送程序要是断开了（比如被关掉），就不再领任务，免得把任务交给一个已经不在的程序。
    """

    def attempt() -> tuple[AgentTask | None, float]:
        task, retry_after = send_queue.claim(session, body.agent_id, body.features)
        if task is None:
            session.rollback()  # 等待期间不占着数据库事务
            return None, retry_after
        return AgentTask(id=task.id, channel=task.channel, group_id=task.group_id, group_name=task.group_name,
                         content=task.content, created_at=task.created_at,
                         attachments=[AttachmentOut.model_validate(a) for a in attachments_of(session, task)],
                         mentions=[MentionIn.model_validate(m) for m in task.mentions or []]), 0

    def has_queued() -> bool:
        found = send_queue.has_queued(session)
        session.rollback()
        return found

    deadline = time.monotonic() + body.wait
    swept = time.monotonic()
    while True:
        out, retry_after = await run_in_threadpool(attempt)
        if out is not None:
            return AgentClaimOut(task=out, retry_after=0)
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                return AgentClaimOut(task=None, retry_after=retry_after)
            await _nap(min(left, retry_after if retry_after > 0 else CLAIM_POLL_SECONDS))
            if await request.is_disconnected():
                return AgentClaimOut(task=None, retry_after=0)
            # 限速的时间到了、来了新任务、或者该清理过期任务了，才去认真领一次
            if (retry_after > 0 or time.monotonic() - swept >= CLAIM_SWEEP_SECONDS
                    or await run_in_threadpool(has_queued)):
                swept = time.monotonic()
                break


@agent_router.post("/tasks/{task_id}/result")
def result(task_id: uuid.UUID, body: AgentResultIn, session: Session = Depends(get_session)) -> dict:
    try:
        task = send_queue.report(session, task_id, body.agent_id, body.ok, body.error, body.warning)
    except send_queue.ReportConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "任务不存在")
    return {"ok": True, "status": task.status.value}
