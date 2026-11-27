"""记录工作台自己调用模型消耗的 token（回复建议、问题补全、识图、和 AI 对话、回流判断、评测、向量）。

每次调用模型接口后 record() 写一行 token_usage。用途按输出结构自动认（见 PURPOSE_BY_SCHEMA），
所属群、是否评测由调用方用 scope() 标上：

    with usage.scope(is_eval=True):
        ...  # 这期间的模型调用都记为评测

记录失败只写日志，不影响模型调用本身。
"""

import contextvars
import functools
import logging
from contextlib import contextmanager
from typing import Any

from app import db

log = logging.getLogger(__name__)

# 用途 key → 页面上的名字
PURPOSES = {
    "suggest": "回复建议",
    "round_query": "问题补全",
    "vision": "识图",
    "refine": "和 AI 对话改写",
    "reflow": "知识回流判断",
    "eval_judge": "评测打分",
    "eval_question": "评测补全打分",
    "embedding": "向量检索",
    "other": "其他",
}
PURPOSE_BY_SCHEMA = {
    "SuggestionDraft": "suggest",
    "RoundQuery": "round_query",
    "RefinedReply": "refine",
    "Judgement": "reflow",
    "Verdict": "eval_judge",
    "QuestionVerdict": "eval_question",
}

_scope: contextvars.ContextVar[dict] = contextvars.ContextVar("usage_scope", default={})


@contextmanager
def scope(**fields: Any):
    """给这段代码里的模型调用标上 is_eval / channel / group_id / group_name，可嵌套。"""
    token = _scope.set({**_scope.get(), **{k: v for k, v in fields.items() if v is not None}})
    try:
        yield
    finally:
        _scope.reset(token)


def tagged(**fields: Any):
    """装饰器版的 scope()。"""
    def wrap(fn):
        @functools.wraps(fn)
        def inner(*args, **kwargs):
            with scope(**fields):
                return fn(*args, **kwargs)
        return inner
    return wrap


def for_message(message) -> dict:
    """群消息相关的调用按群归类。"""
    if message is None:
        return {}
    channel = getattr(message.channel, "value", message.channel)
    return {"channel": channel, "group_id": message.group_id, "group_name": message.group_name}


def purpose_for(schema: type | None) -> str:
    return PURPOSE_BY_SCHEMA.get(getattr(schema, "__name__", ""), "other")


def _int(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def openai_tokens(usage: dict | None) -> tuple[int, int, int] | None:
    """OpenAI 兼容接口返回的 usage → (输入, 输出, 其中命中缓存的输入)。没有 usage 返回 None。"""
    if not isinstance(usage, dict) or not usage:
        return None
    details = usage.get("prompt_tokens_details") or {}
    cached = _int(details.get("cached_tokens") if isinstance(details, dict) else 0) or _int(
        usage.get("prompt_cache_hit_tokens"))  # DeepSeek 的写法
    prompt = _int(usage.get("prompt_tokens"))
    completion = _int(usage.get("completion_tokens"))
    if not completion and usage.get("total_tokens"):
        completion = max(0, _int(usage.get("total_tokens")) - prompt)
    return prompt, completion, min(cached, prompt)


def estimate(text: str) -> int:
    """接口没返回 usage 时按字数粗估：中文约 1 字 1 token，英文约 4 字母 1 token。"""
    if not text:
        return 0
    ascii_chars = sum(1 for ch in text if ord(ch) < 128)
    return (len(text) - ascii_chars) + (ascii_chars + 3) // 4


def record(purpose: str | None, model: str | None, input_tokens: int = 0, output_tokens: int = 0,
           cached_tokens: int = 0, *, ok: bool = True, estimated: bool = False, duration_ms: int | None = None,
           error: str | None = None) -> None:
    from app.models import TokenUsage

    ctx = _scope.get()
    try:
        with db.SessionLocal() as session:
            session.add(TokenUsage(
                purpose=purpose or "other", model=(model or "未知")[:128],
                input_tokens=_int(input_tokens), output_tokens=_int(output_tokens),
                cached_tokens=_int(cached_tokens), ok=ok, estimated=estimated, duration_ms=duration_ms,
                error=(error or None) and error[:300], is_eval=bool(ctx.get("is_eval")),
                channel=ctx.get("channel"), group_id=ctx.get("group_id"),
                group_name=(ctx.get("group_name") or None) and str(ctx.get("group_name"))[:255]))
            session.commit()
    except Exception:  # noqa: BLE001  统计不能影响模型调用
        log.exception("记录 token 用量失败")
