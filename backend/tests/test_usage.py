"""模型用量：每次调用记下 token，按天、用途、模型、线上 / 评测统计，管理员可填单价估算费用。"""

import json
from datetime import datetime, timedelta, timezone

import httpx
from fastapi.testclient import TestClient

from app import db
from app.ai import usage
from app.ai.embedding import EmbeddingClient
from app.ai.llm import OpenAICompatibleProvider, SuggestionDraft
from app.ai.suggest import RoundQuery
from app.main import app
from app.models import TokenUsage

GOOD = {"needs_reply": True, "can_answer": True, "confidence": "high", "reply": "您好", "reason": "常见问题", "references": []}


def _provider(replies, usage_body=None):
    def handler(request):
        body = {"model": "deepseek-flash", "choices": [{"message": {"content": replies.pop(0)}}]}
        if usage_body is not None:
            body["usage"] = usage_body
        return httpx.Response(200, json=body)

    return OpenAICompatibleProvider("http://llm.test/v1", "", "m", client=httpx.Client(transport=httpx.MockTransport(handler)))


def _rows():
    with db.SessionLocal() as s:
        return s.query(TokenUsage).order_by(TokenUsage.id).all()


def test_records_usage_with_purpose_scope_and_retry(client):
    u = {"prompt_tokens": 1200, "completion_tokens": 300, "prompt_cache_hit_tokens": 1000}
    p = _provider([json.dumps(GOOD)], u)
    with usage.scope(channel="wecom", group_id="g1", group_name="BI-测试群"):
        p.draft("s", "p")
    p = _provider(["不是 JSON", json.dumps({"has_question": True, "question": "怎么导出"})], u)
    with usage.scope(is_eval=True):
        p.generate("s", "p", RoundQuery)
    rows = _rows()
    assert [(r.purpose, r.is_eval) for r in rows] == [("suggest", False), ("round_query", True), ("round_query", True)]
    first = rows[0]
    assert (first.model, first.input_tokens, first.output_tokens, first.cached_tokens) == ("deepseek-flash", 1200, 300, 1000)
    assert (first.group_name, first.group_id, first.estimated) == ("BI-测试群", "g1", False)
    assert rows[1].group_name is None  # 不同 scope 不串


def test_estimates_when_gateway_returns_no_usage(client):
    _provider([json.dumps(GOOD)]).draft("系统提示", "客户问报表导出")
    (row,) = _rows()
    assert row.estimated and row.input_tokens > 0 and row.output_tokens > 0


def test_failed_call_is_counted(client):
    def handler(request):
        return httpx.Response(500, json={"error": "boom"})

    p = OpenAICompatibleProvider("http://llm.test/v1", "", "m", client=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        p.draft("s", "p")
    except Exception:
        pass
    (row,) = _rows()
    assert not row.ok and row.input_tokens == 0 and row.error


def test_embedding_usage(client):
    def handler(request):
        return httpx.Response(200, json={"model": "bge-m3", "data": [{"index": 0, "embedding": [0.1] * 4}],
                                         "usage": {"prompt_tokens": 7, "total_tokens": 7}})

    EmbeddingClient("http://emb.test/v1", "bge-m3", dim=4,
                    client=httpx.Client(transport=httpx.MockTransport(handler))).embed(["报表导出"])
    (row,) = _rows()
    assert (row.purpose, row.model, row.input_tokens, row.output_tokens) == ("embedding", "bge-m3", 7, 0)


def _add(**kw):
    with db.SessionLocal() as s:
        s.add(TokenUsage(**kw))
        s.commit()


def test_summary_by_day_purpose_model_and_cost(client):
    now = datetime.now(timezone.utc)
    _add(purpose="suggest", model="deepseek-flash", input_tokens=1_000_000, output_tokens=200_000,
         cached_tokens=500_000, group_name="BI-客户A", created_at=now)
    _add(purpose="vision", model="deepseek-flash", input_tokens=100_000, output_tokens=50_000, created_at=now)
    _add(purpose="suggest", model="deepseek-flash", input_tokens=300_000, output_tokens=0, is_eval=True, created_at=now)
    _add(purpose="embedding", model="bge-m3", input_tokens=5_000, created_at=now)
    _add(purpose="suggest", model="deepseek-flash", input_tokens=10, output_tokens=10, ok=False,
         created_at=now - timedelta(days=2))

    r = client.get("/api/usage")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["online"]["total_tokens"] == 1_355_000 and data["online"]["calls"] == 3
    assert data["eval"]["total_tokens"] == 300_000
    assert data["online"]["cost"] is None and data["online"]["unpriced_tokens"] == 1_355_000
    suggest = next(p for p in data["purposes"] if p["key"] == "suggest")
    assert suggest["label"] == "回复建议" and suggest["online"]["total_tokens"] == 1_200_000
    assert suggest["eval"]["total_tokens"] == 300_000
    assert [m["model"] for m in data["models"]] == ["deepseek-flash", "bge-m3"]
    assert data["groups"] == [{"group_name": "BI-客户A", "totals": data["groups"][0]["totals"]}]
    assert len(data["days"]) == 7 and data["days"][-1]["online"]["total_tokens"] == 1_355_000
    assert data["days"][-3]["online"]["failed"] == 1
    assert {p["model"] for p in data["prices"]} == {"deepseek-flash", "bge-m3"}  # 用过的模型都能填单价

    r = client.put("/api/usage/prices/deepseek-flash", json={"input_price": 2, "output_price": 8, "cached_price": 0.5})
    assert r.status_code == 200, r.text
    data = client.get("/api/usage").json()
    # 建议：50 万未命中×2 + 50 万命中×0.5 + 20 万输出×8 = 1+0.25+1.6；识图：10 万×2 + 5 万×8 = 0.2+0.4
    assert data["online"]["cost"] == 3.45 and data["online"]["unpriced_tokens"] == 5_000
    assert data["eval"]["cost"] == 0.6

    past = data["days"][-3]["date"]
    old = client.get("/api/usage", params={"day": past}).json()
    assert old["online"]["calls"] == 1 and old["online"]["failed"] == 1


def test_only_admin_sees_usage(client):
    r = client.post("/api/staff", json={"name": "王五", "username": "ww", "password": "secret-123", "role": "support"})
    assert r.status_code == 201
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"username": "ww", "password": "secret-123"}).status_code == 200
    assert c.get("/api/usage").status_code == 403
    assert c.put("/api/usage/prices/x", json={"input_price": 1, "output_price": 1}).status_code == 403
