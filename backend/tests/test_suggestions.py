import httpx
import pytest

from app.ai.llm import LLMError, OpenAICompatibleProvider, parse_draft

CUSTOMER_MSG = {
    "channel": "feishu",  # 通用渠道：所有文本消息都视为客户消息
    "external_id": "om_1",
    "group_id": "oc_abc",
    "group_name": "某客户-有数项目群",
    "is_external_group": True,
    "sender_id": "ou_1",
    "sender_name": "张三",
    "content": "报表导出一直转圈怎么办？",
    "sent_at": "2026-11-19T08:00:00Z",
}


def _post(client, **kw):
    r = client.post("/api/messages", json={**CUSTOMER_MSG, **kw})
    assert r.status_code == 201
    return r.json()


def _todo(client, view="todo"):
    return client.get("/api/inbox", params={"view": view}).json()


def test_new_message_gets_suggestion(client, fake_llm):
    _post(client, external_id="om_0", content="之前的问题", sent_at="2026-11-19T07:59:00Z")
    _post(client)
    items = _todo(client)
    assert len(items) == 2
    item = items[0]
    assert item["message"]["content"] == CUSTOMER_MSG["content"]
    sug = item["suggestion"]
    assert sug["status"] == "ready" and sug["content"] == "您好，请清理浏览器缓存后重试。"
    assert sug["can_answer"] is True and sug["model"] == "fake-model"
    prompt = fake_llm.calls[-1][1]
    assert "之前的问题" in prompt and "<customer_message>" in prompt and "某客户-有数项目群" in prompt
    # 重复推送不重复生成
    _post(client)
    assert len(fake_llm.calls) == 2


def test_empty_or_non_text_skipped(client, fake_llm):
    _post(client, external_id="img", msg_type="image", content="[图片]")
    _post(client, external_id="blank", content="  ")
    assert fake_llm.calls == [] and _todo(client) == []
    assert len(_todo(client, "all")) == 2


def test_adopt_as_is(client):
    _post(client)
    sid = _todo(client)[0]["suggestion"]["id"]
    r = client.post(f"/api/suggestions/{sid}/adopt", json={"operator": "李四"})
    assert r.status_code == 200
    body = r.json()
    assert body["delivery"] == "manual"
    assert body["suggestion"]["status"] == "adopted" and body["suggestion"]["handled_by"] == "李四"
    assert body["suggestion"]["final_content"] == "您好，请清理浏览器缓存后重试。"
    assert _todo(client) == [] and len(_todo(client, "done")) == 1
    # 已处理的不能再处理
    assert client.post(f"/api/suggestions/{sid}/escalate", json={}).status_code == 409


def test_adopt_edited(client):
    _post(client)
    sid = _todo(client)[0]["suggestion"]["id"]
    r = client.post(f"/api/suggestions/{sid}/adopt", json={"content": "您好，麻烦发一下报错截图。"})
    assert r.json()["suggestion"]["status"] == "edited"
    assert r.json()["suggestion"]["final_content"] == "您好，麻烦发一下报错截图。"
    assert client.post(f"/api/suggestions/{sid}/adopt", json={"content": "x"}).status_code == 409


def test_adopt_empty_rejected(client):
    _post(client)
    sid = _todo(client)[0]["suggestion"]["id"]
    assert client.post(f"/api/suggestions/{sid}/adopt", json={"content": "  "}).status_code == 422


def test_escalate_then_adopt(client):
    _post(client)
    sid = _todo(client)[0]["suggestion"]["id"]
    r = client.post(f"/api/suggestions/{sid}/escalate", json={"note": "涉及数据丢失", "operator": "李四"})
    assert r.json()["status"] == "escalated" and r.json()["note"] == "涉及数据丢失"
    assert _todo(client) == [] and len(_todo(client, "escalated")) == 1
    # 转人工后，专人仍可在工作台修改并回复
    assert client.post(f"/api/suggestions/{sid}/adopt", json={"content": "已修复"}).json()["suggestion"]["status"] == "edited"


def test_dismiss(client):
    _post(client, content="好的谢谢")
    sid = _todo(client)[0]["suggestion"]["id"]
    assert client.post(f"/api/suggestions/{sid}/dismiss", json={}).json()["status"] == "dismissed"
    assert client.post("/api/suggestions/00000000-0000-0000-0000-000000000000/dismiss", json={}).status_code == 404


def test_failure_and_regenerate(client, fake_llm):
    fake_llm.error = LLMError("调用模型接口失败：timeout")
    _post(client)
    sug = _todo(client)[0]["suggestion"]
    assert sug["status"] == "failed" and "timeout" in sug["error"]
    fake_llm.error = None
    r = client.post(f"/api/suggestions/{sug['id']}/regenerate")
    assert r.status_code == 200
    assert _todo(client)[0]["suggestion"]["status"] == "ready"


# ---------- 自有模型网关（OpenAI 兼容协议） ----------

def test_openai_compatible_provider():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"], seen["auth"] = str(request.url), request.headers.get("authorization")
        seen["body"] = request.read().decode()
        content = '```json\n{"can_answer": false, "confidence": "low", "reply": "请提供版本号", "reason": "信息不足"}\n```'
        return httpx.Response(200, json={"model": "digital-human-v2", "choices": [{"message": {"content": content}}]})

    p = OpenAICompatibleProvider("https://llm.example.com/v1/", "sk-test", "dh", client=httpx.Client(
        transport=httpx.MockTransport(handler)))
    draft, model = p.draft("sys", "prompt")
    assert seen["url"] == "https://llm.example.com/v1/chat/completions" and seen["auth"] == "Bearer sk-test"
    assert '"response_format"' in seen["body"]
    assert draft.can_answer is False and draft.reply == "请提供版本号" and model == "digital-human-v2"


def test_openai_compatible_errors():
    p = OpenAICompatibleProvider("https://x", "", "m", client=httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(500, text="boom"))))
    with pytest.raises(LLMError):
        p.draft("s", "p")
    with pytest.raises(LLMError):
        parse_draft("不是 JSON")
    with pytest.raises(LLMError):
        parse_draft('{"can_answer": true}')


def test_claude_provider_request():
    import json

    import anthropic
    import httpx2

    from app.ai.llm import ClaudeProvider

    seen = {}

    def handler(request):
        seen["body"], seen["beta"] = json.loads(request.read()), request.headers.get("anthropic-beta")
        text = json.dumps({"can_answer": True, "confidence": "high", "reply": "您好", "reason": "r"})
        return httpx2.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "stop_reason": "end_turn", "stop_sequence": None, "content": [{"type": "text", "text": text}],
            "usage": {"input_tokens": 1, "output_tokens": 1}})

    p = ClaudeProvider("claude-opus-5-5", "medium")
    p._client = anthropic.Anthropic(api_key="test", http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    draft, model = p.draft("sys", "prompt")
    assert draft.reply == "您好" and model == "claude-opus-5-5"
    assert seen["beta"] == "server-side-fallback-2026-07-01" and seen["body"]["fallbacks"] == "default"
    assert seen["body"]["output_config"]["effort"] == "medium"
    assert seen["body"]["output_config"]["format"]["type"] == "json_schema"
