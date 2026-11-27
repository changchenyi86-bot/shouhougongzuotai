"""模型偶尔不按格式输出：重问一次；两次都不对才报错。"""

import json

import httpx
import pytest

from app.ai.llm import LLMError, OpenAICompatibleProvider, SuggestionDraft

GOOD = {"needs_reply": True, "can_answer": True, "confidence": "high", "reply": "您好", "reason": "常见问题", "references": []}


def _provider(replies):
    calls = []

    def handler(request):
        calls.append(request)
        content = replies[min(len(calls), len(replies)) - 1]
        return httpx.Response(200, json={"model": "m", "choices": [{"message": {"content": content}}]})

    p = OpenAICompatibleProvider("http://llm.test/v1", "", "m", client=httpx.Client(transport=httpx.MockTransport(handler)))
    return p, calls


def test_retry_once_when_output_is_not_json():
    p, calls = _provider(["抱歉，我来整理一下", json.dumps(GOOD)])
    draft, _ = p.draft("s", "p")
    assert isinstance(draft, SuggestionDraft) and draft.reply == "您好" and len(calls) == 2


def test_give_up_after_second_bad_output():
    p, calls = _provider(["不是 JSON", "还是不是"])
    with pytest.raises(LLMError, match="没有返回 JSON"):
        p.draft("s", "p")
    assert len(calls) == 2


def test_interface_error_is_not_retried():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(500)

    p = OpenAICompatibleProvider("http://llm.test/v1", "", "m", client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(LLMError, match="调用模型接口失败"):
        p.draft("s", "p")
    assert len(calls) == 1
