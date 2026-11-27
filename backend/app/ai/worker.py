"""后台生成回复建议：入库后立即返回，建议在线程池里生成。"""

import logging
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app import db
from app.ai import usage
from app.config import settings
from app.models import Attachment, Message, Suggestion, SuggestionStatus

log = logging.getLogger(__name__)

_executor: ThreadPoolExecutor | None = None


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=settings.ai_workers, thread_name_prefix="suggest")
    return _executor


def submit(fn, *args) -> None:
    """测试中会被替换为同步执行。"""
    _get_executor().submit(fn, *args)


def schedule(delay: float, fn, *args) -> None:
    if delay <= 0:
        submit(fn, *args)
    else:
        timer = threading.Timer(delay, submit, args=(fn, *args))
        timer.daemon = True
        timer.start()


def enqueue(message_id: uuid.UUID, wait: float | None = None) -> None:
    """为消息创建（或重置）建议记录并排队生成。"""
    if not settings.ai_suggest_enabled:
        return
    with db.SessionLocal() as session:
        sug = session.query(Suggestion).filter_by(message_id=message_id).one_or_none()
        if sug is None:
            sug = Suggestion(message_id=message_id)
            session.add(sug)
        sug.status, sug.error = SuggestionStatus.GENERATING, None
        session.commit()
    schedule(settings.ai_round_wait_seconds if wait is None else wait, run, message_id)


def resume_pending() -> int:
    """后台启动时调用：重启前还在生成中的建议（生成任务随进程丢了）重新排队，返回排队条数。

    太久以前的消息不再生成（和入库时的规则一样），标为生成失败，工作台上可以点「重新生成」。
    """
    from app.ingest import _is_recent

    if not settings.ai_suggest_enabled:
        return 0
    with db.SessionLocal() as session:
        stuck = session.scalars(select(Suggestion).where(Suggestion.status == SuggestionStatus.GENERATING)).all()
        again = []
        for sug in stuck:
            message = session.get(Message, sug.message_id)
            if message is not None and _is_recent(message):
                again.append(message.id)
            else:
                sug.status, sug.error = SuggestionStatus.FAILED, "生成时后台重启了，可以点「重新生成」"
        session.commit()
    for message_id in again:
        schedule(0, run, message_id)
    if stuck:
        log.info("重启前没生成完的建议 %d 条，重新生成 %d 条", len(stuck), len(again))
    return len(again)


def _resume_previous(session, message: Message) -> None:
    """被人工处理的任务不能接管前面的消息，唤醒窗口内最新的待生成消息。"""
    from app.ai.suggest import _key, round_messages

    if settings.ai_round_window_minutes <= 0:
        return
    candidates = round_messages(session, message)
    ids = [m.id for m in candidates if _key(m) < _key(message)]
    generating = set(session.scalars(select(Suggestion.message_id).where(
        Suggestion.message_id.in_(ids), Suggestion.status == SuggestionStatus.GENERATING)))
    previous = next((m.id for m in reversed(candidates) if m.id in generating), None)
    if previous:
        # 测试时 schedule 立即执行；生产中也应先释放现有会话和锁。
        session.rollback()
        session.close()
        schedule(0, run, previous)


def run(message_id: uuid.UUID) -> None:
    from app.ai.suggest import SuggestionFailed, _key, generate, round_messages
    from app.channels import ADAPTERS

    with db.SessionLocal() as session:
        message = session.get(Message, message_id)
        sug = session.query(Suggestion).filter_by(message_id=message_id).one_or_none()
        if message is None or sug is None:
            return
        if sug.status != SuggestionStatus.GENERATING:
            _resume_previous(session, message)
            return
        newer = session.scalars(select(Message).join(Suggestion, Suggestion.message_id == Message.id).where(
            Message.channel == message.channel, Message.group_id == message.group_id,
            Message.sent_at >= message.sent_at,
            Message.sent_at <= message.sent_at + timedelta(minutes=settings.ai_round_window_minutes),
            Suggestion.status == SuggestionStatus.GENERATING)).all()
        newer = [m for m in newer if _key(m) > _key(message) and ADAPTERS[m.channel].is_customer(m)]
        if newer and settings.ai_round_window_minutes > 0:
            # 中间有我方发言时，新消息不能合并本条，因此本条不能让出生成任务。
            intervening = session.scalars(select(Message).where(
                Message.channel == message.channel, Message.group_id == message.group_id,
                Message.sent_at >= message.sent_at, Message.sent_at <= max(m.sent_at for m in newer))).all()
            support = [m for m in intervening if not ADAPTERS[m.channel].is_customer(m)]
            if any(not any(_key(message) < _key(m) < _key(candidate) for m in support) for candidate in newer):
                return
        round = round_messages(session, message) or [message]
        result = error = None
        try:
            with usage.scope(**usage.for_message(message)):
                result = generate(session, message, round=round, product_override=sug.product_override)
        except SuggestionFailed as exc:
            error = str(exc)
        except Exception as exc:  # noqa: BLE001
            log.exception("生成回复建议失败 message=%s", message_id)
            error = f"内部错误：{exc}"
        # 生成期间可能已被转人工、忽略或采纳，只在仍处于生成中时写入结果
        session.refresh(sug, with_for_update=True)
        if sug.status != SuggestionStatus.GENERATING:
            _resume_previous(session, message)
            return
        round_ids = result.round_ids if result is not None else [m.id for m in round]
        sug.covers = [str(mid) for mid in round_ids]
        sug.question = result.question if result is not None else "\n".join(m.content for m in round)
        sug.merged_into = None
        others = session.scalars(select(Suggestion).where(
            Suggestion.message_id.in_([mid for mid in round_ids if mid != message_id])
        ).with_for_update()).all()
        for other in others:
            session.refresh(other)
            if other.status in (SuggestionStatus.GENERATING, SuggestionStatus.READY, SuggestionStatus.FAILED,
                                 SuggestionStatus.MERGED):
                other.status, other.merged_into = SuggestionStatus.MERGED, sug.id
        if result is None:
            sug.status, sug.error = SuggestionStatus.FAILED, error
            sug.knowledge = sug.knowledge_error = sug.knowledge_note = sug.profile = None
        else:
            draft = result.draft
            sug.status = SuggestionStatus.READY
            sug.content, sug.can_answer = draft.reply, draft.can_answer
            sug.confidence, sug.reason, sug.model = draft.confidence, draft.reason, result.model
            sug.knowledge = [k.to_record() for k in result.knowledge]
            sug.knowledge_error, sug.knowledge_note = result.knowledge_error, result.knowledge_note
            sug.profile = result.profile
            if result.product is not None:
                sug.products, sug.product_source = result.product.codes, result.product.source
                sug.product_reason = result.product.reason
            sug.needs_reply = draft.needs_reply
            if not draft.needs_reply and all(auto_dismissable(m.content) for m in round) and not sug.reopened_by:
                # AI 判断无需回复（寒暄、致谢、测试）：直接标记，不算待处理；工作台上可以改回待回复
                sug.status, sug.auto_dismissed = SuggestionStatus.DISMISSED, True
                sug.handled_by, sug.handled_at = None, datetime.now(timezone.utc)
        session.commit()
        checked = result.media_checked_at if result is not None else None
        if checked is not None:
            # 生成期间才识别完的图片、文件没进提示词：整轮再生成一次（识别结果已在库里，不会再等）
            late = session.scalars(select(Attachment.id).join(Message, Message.attachment_id == Attachment.id).where(
                Message.id.in_(round_ids), Attachment.extract_status == "done", Attachment.extracted_at > checked,
                Attachment.extracted_at <= datetime.now(timezone.utc))).first()
            if late is not None:
                log.info("附件在生成建议期间识别完，重新生成 message=%s", message_id)
                enqueue(message_id, wait=0)


ACTIVE = (SuggestionStatus.READY, SuggestionStatus.FAILED)


def after_media(attachment_id: uuid.UUID) -> None:
    """图片、文件在后台识别完（生成建议时没等到）：这一轮的建议还没发出去，就带上识别内容重新生成。"""
    from app.ingest import _is_recent

    if not settings.ai_suggest_enabled:
        return
    with db.SessionLocal() as session:
        attachment = session.get(Attachment, attachment_id)
        if attachment is None or attachment.extract_status != "done" or attachment.extracted_at is None:
            return
        message = session.scalar(select(Message).where(Message.attachment_id == attachment_id))
        if message is None or not _is_recent(message):
            return
        sug = session.scalar(select(Suggestion).where(Suggestion.message_id == message.id))
        seen = set()
        while sug is not None and sug.status == SuggestionStatus.MERGED and sug.merged_into and sug.id not in seen:
            seen.add(sug.id)
            sug = session.get(Suggestion, sug.merged_into)
        if sug is None:
            return
        # 生成中的由 run 结束时检查；同事已经回复、转人工、手动标无需回复的不动
        regenerate = sug.status in ACTIVE or (sug.status == SuggestionStatus.DISMISSED and sug.auto_dismissed
                                               and not sug.handled_by)
        if not regenerate or sug.updated_at >= attachment.extracted_at:
            return
        if sug.status == SuggestionStatus.DISMISSED:
            sug.auto_dismissed = False
        target = sug.message_id
        session.commit()
    log.info("附件识别完，整轮重新生成建议 attachment=%s message=%s", attachment_id, target)
    enqueue(target, wait=0)


_STRIP = re.compile(r"@\S+|[\W_]+")


def auto_dismissable(text: str) -> bool:
    """AI 判为无需回复时，只有短、不带问号、不 @ 人的消息才自动标记；其余只提示，避免漏回客户。

    @ 了技术支持的（如同步进度、交代事情），对方通常在等一句回应。
    """
    if any(c in text for c in "?？@＠"):
        return False
    return len(_STRIP.sub("", text or "")) <= settings.auto_dismiss_max_chars


def shutdown() -> None:
    if _executor is not None:
        _executor.shutdown(wait=False, cancel_futures=True)
