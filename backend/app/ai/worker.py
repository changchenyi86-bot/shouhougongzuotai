"""后台生成回复建议：入库后立即返回，建议在线程池里生成。"""

import logging
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from app import db
from app.config import settings
from app.models import Message, Suggestion, SuggestionStatus

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


def enqueue(message_id: uuid.UUID) -> None:
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
    submit(run, message_id)


def run(message_id: uuid.UUID) -> None:
    from app.ai.suggest import SuggestionFailed, generate

    with db.SessionLocal() as session:
        message = session.get(Message, message_id)
        sug = session.query(Suggestion).filter_by(message_id=message_id).one_or_none()
        if message is None or sug is None:
            return
        if sug.status != SuggestionStatus.GENERATING:
            return
        result = error = None
        try:
            result = generate(session, message)
        except SuggestionFailed as exc:
            error = str(exc)
        except Exception as exc:  # noqa: BLE001
            log.exception("生成回复建议失败 message=%s", message_id)
            error = f"内部错误：{exc}"
        # 生成期间可能已被转人工、忽略或采纳，只在仍处于生成中时写入结果
        session.refresh(sug, with_for_update=True)
        if sug.status != SuggestionStatus.GENERATING:
            session.rollback()
            return
        if result is None:
            sug.status, sug.error = SuggestionStatus.FAILED, error
            sug.knowledge = sug.knowledge_error = sug.knowledge_note = None
        else:
            draft = result.draft
            sug.status = SuggestionStatus.READY
            sug.content, sug.can_answer = draft.reply, draft.can_answer
            sug.confidence, sug.reason, sug.model = draft.confidence, draft.reason, result.model
            sug.knowledge = [k.to_record() for k in result.knowledge]
            sug.knowledge_error, sug.knowledge_note = result.knowledge_error, result.knowledge_note
            sug.needs_reply = draft.needs_reply
            if not draft.needs_reply and auto_dismissable(message.content) and not sug.reopened_by:
                # AI 判断无需回复（寒暄、致谢、测试）：直接标记，不算待处理；工作台上可以改回待回复
                sug.status, sug.auto_dismissed = SuggestionStatus.DISMISSED, True
                sug.handled_by, sug.handled_at = None, datetime.now(timezone.utc)
        session.commit()


_STRIP = re.compile(r"@\S+|[\W_]+")


def auto_dismissable(text: str) -> bool:
    """AI 判为无需回复时，只有短、不带问号的消息才自动标记；其余只提示，避免漏回客户的问题。"""
    if "?" in text or "？" in text:
        return False
    return len(_STRIP.sub("", text or "")) <= settings.auto_dismiss_max_chars


def shutdown() -> None:
    if _executor is not None:
        _executor.shutdown(wait=False, cancel_futures=True)
