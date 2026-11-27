"""和 AI 对话润色、无需回复判断、采纳统计、候选知识回流、相似历史问答。"""

import math
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.ai import embedding, reflow
from app.ai.llm import LLMError, SuggestionDraft
from app.config import settings
from app.main import app

CUSTOMER = "wmAbCdEfGhIjKlMnOpQrStUvWxYz012345"
N = 0


def _msg(client, content="报表导出一直转圈怎么办？", group="wrA", name="某银行-有数项目群"):
    global N
    N += 1
    r = client.post("/api/messages", json={
        "channel": "wecom", "external_id": f"e{N}", "group_id": group, "group_name": name,
        "is_external_group": True, "sender_id": CUSTOMER, "content": content,
        "sent_at": datetime(2026, 11, 27, 2, N % 60, tzinfo=timezone.utc).isoformat(),
    })
    assert r.status_code == 201
    return r.json()["id"]


def _sug(client, message_id, group="wrA"):
    items = client.get("/api/conversation", params={"channel": "wecom", "group_id": group}).json()["items"]
    return next(i["suggestion"] for i in items if i["message"]["id"] == message_id)


def _adopt(client, sid, content, via_chat=False):
    r = client.post(f"/api/suggestions/{sid}/adopt", json={"content": content, "via_chat": via_chat})
    assert r.status_code == 200, r.text
    return r.json()["suggestion"]


@pytest.fixture()
def wangwu(client):
    """第二个技术支持「王五」，用来审核李四（client 登录的管理员）提交的知识。"""
    r = client.post("/api/staff", json={"name": "王五", "username": "ww", "password": "secret-123", "role": "support"})
    assert r.status_code == 201, r.text
    c = TestClient(app)
    c.staff_id = r.json()["id"]
    assert c.post("/api/auth/login", json={"username": "ww", "password": "secret-123"}).status_code == 200
    return c


def test_classify():
    ai = "您好，请先清理浏览器缓存再重新导出。"
    assert reflow.classify(ai, "您好，请先清理浏览器缓存再重新导出") == ("exact", 1.0)  # 只差标点
    assert reflow.classify(ai, "您好，请先清理浏览器缓存后再重新导出。")[0] == "minor"
    assert reflow.classify(ai, "您好，导出转圈一般是数据量大，请改成后台导出，导出完成后会通知您下载。")[0] in ("major", "unused")
    assert reflow.classify(ai, "麻烦发一下报表名称，我们查一下日志")[0] == "unused"
    assert reflow.classify(None, "随便写的")[0] == "unused"


def test_no_reply_auto_dismissed_and_reopen(client, fake_llm):
    fake_llm.draft_result = SuggestionDraft(needs_reply=False, can_answer=True, confidence="high", reply="不客气～",
                                            reason="客户在致谢", references=[])
    mid = _msg(client, "好的，谢谢")
    s = _sug(client, mid)
    assert s["status"] == "dismissed" and s["auto_dismissed"] and s["handled_by"] is None
    groups = client.get("/api/groups", params={"view": "todo"}).json()
    assert groups == []
    assert s["knowledge_note"] == "没配置售后数字人知识库"
    r = client.post(f"/api/suggestions/{s['id']}/reopen")
    assert r.status_code == 200 and r.json()["status"] == "ready" and r.json()["reopened_by"] == "李四"
    assert client.post(f"/api/suggestions/{s['id']}/reopen").status_code == 409


def test_no_reply_with_mention_only_hinted(client, fake_llm):
    fake_llm.draft_result = SuggestionDraft(needs_reply=False, can_answer=True, confidence="high", reply="收到",
                                            reason="同步进度", references=[])
    mid = _msg(client, "@李四 今天迁移了 5T 数据，下周继续")
    s = _sug(client, mid)
    assert s["status"] == "ready" and s["needs_reply"] is False and not s["auto_dismissed"]


def test_resume_generating_after_restart(client, fake_llm, monkeypatch):
    import uuid
    from datetime import timedelta

    from app import db
    from app.ai import worker
    from app.models import Message

    pending = []
    monkeypatch.setattr(worker, "submit", lambda fn, *args: pending.append((fn, args)))
    fresh, old = _msg(client), _msg(client, "很久以前的问题")
    assert len(pending) == 2  # 重启前排了队、还没生成，任务随进程丢了
    with db.SessionLocal() as session:
        session.get(Message, uuid.UUID(fresh)).sent_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        session.get(Message, uuid.UUID(old)).sent_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
        session.commit()
    monkeypatch.setattr(settings, "ai_suggest_max_age_minutes", 120)
    monkeypatch.setattr(worker, "submit", lambda fn, *args: fn(*args))
    assert worker.resume_pending() == 1
    assert _sug(client, fresh)["status"] == "ready"
    s = _sug(client, old)
    assert s["status"] == "failed" and "重新生成" in s["error"]


def test_no_reply_long_question_only_hinted(client, fake_llm):
    fake_llm.draft_result = SuggestionDraft(needs_reply=False, can_answer=False, confidence="low", reply="收到",
                                            reason="像是闲聊", references=[])
    mid = _msg(client, "你们那边今天是不是有人在处理我们的升级？")
    s = _sug(client, mid)
    assert s["status"] == "ready" and s["needs_reply"] is False and not s["auto_dismissed"]


def test_small_talk_note(client, fake_llm, monkeypatch):
    monkeypatch.setattr(settings, "knowledge_mcp_url", "http://mcp.test/messages")
    mid = _msg(client, "收到")
    assert _sug(client, mid)["knowledge_note"] == "寒暄、确认类消息，没查知识库"


def test_chat_refine_then_send(client, fake_llm):
    mid = _msg(client)
    sid = _sug(client, mid)["id"]
    r = client.post(f"/api/suggestions/{sid}/chat", json={"content": "让他清缓存，不行再发日志"})
    assert r.status_code == 200
    turns = r.json()
    assert [t["role"] for t in turns] == ["staff", "ai"] and turns[1]["content"].startswith("您好")
    assert turns[0]["operator"] == "李四"
    schema, prompt = fake_llm.generated[-1]
    assert "让他清缓存" in prompt and "报表导出一直转圈" in prompt and "<ai_first_draft>" in prompt
    # 第二轮带上前面的对话
    fake_llm.results[type(fake_llm.results[schema])] = schema(reply="您好，请先清理缓存；还不行的话麻烦发一下日志。")
    turns = client.post(f"/api/suggestions/{sid}/chat", json={"content": "加上发日志"}).json()
    assert len(turns) == 4 and "让他清缓存" in fake_llm.generated[-1][1]
    assert client.get(f"/api/suggestions/{sid}/chat").json() == turns

    sug = _adopt(client, sid, turns[-1]["content"], via_chat=True)
    assert sug["status"] == "edited" and sug["via_chat"] is True and sug["adoption"] in ("major", "minor")
    assert client.post(f"/api/suggestions/{sid}/chat", json={"content": "再改改"}).status_code == 409


def test_chat_failure_keeps_nothing(client, fake_llm):
    mid = _msg(client)
    sid = _sug(client, mid)["id"]
    fake_llm.error = LLMError("超时")
    r = client.post(f"/api/suggestions/{sid}/chat", json={"content": "说一下"})
    assert r.status_code == 502 and "超时" in r.json()["detail"]
    fake_llm.error = None
    assert client.get(f"/api/suggestions/{sid}/chat").json() == []


def test_via_chat_needs_real_chat(client, fake_llm):
    mid = _msg(client)
    sid = _sug(client, mid)["id"]
    assert _adopt(client, sid, "自己写的回复", via_chat=True)["via_chat"] is False


def test_exact_adoption_not_reflowed(client, fake_llm):
    mid = _msg(client)
    s = _sug(client, mid)
    sug = _adopt(client, s["id"], s["content"])
    assert sug["adoption"] == "exact"
    assert not any(schema is reflow.Judgement for schema, _ in fake_llm.generated)
    assert _sug(client, mid)["candidate"] is None


def _edited_reply(client, content="报表导出一直转圈怎么办？"):
    mid = _msg(client, content)
    sid = _sug(client, mid)["id"]
    _adopt(client, sid, "您好，导出转圈一般是数据量大，请在导出设置里改成后台导出，完成后会在消息中心通知您。")
    cand = _sug(client, mid)["candidate"]
    assert cand and cand["status"] == "proposed"
    return mid, cand["id"]


def test_reflow_confirm_review_and_cards(client, fake_llm, wangwu, monkeypatch):
    monkeypatch.setattr(settings, "cards_sync_token", "sync-secret")
    sync = {"Authorization": "Bearer sync-secret"}
    mid, cid = _edited_reply(client)
    _, prompt = next((s, p) for s, p in fake_llm.generated if s is reflow.Judgement)
    assert "<actual_reply" in prompt and "后台导出" in prompt

    mine = client.get("/api/candidates/counts").json()
    assert mine["mine"] == 1 and mine["pending"] == 0
    listed = client.get("/api/candidates", params={"view": "proposed"}).json()
    assert listed[0]["customer_reply"].startswith("您好，导出转圈") and listed[0]["message_id"] == mid
    assert listed[0]["customer_question"] == "报表导出一直转圈怎么办？" and listed[0]["group_id"] == "wrA"

    # 答案不能清空；回复的人确认时可以改答案
    assert client.post(f"/api/candidates/{cid}/confirm", json={"answer": " "}).status_code == 422
    r = client.post(f"/api/candidates/{cid}/confirm", json={"answer": "改成后台导出，完成后到消息中心下载。"})
    assert r.status_code == 200 and r.json()["status"] == "pending" and r.json()["updated_by"] == "李四"
    assert wangwu.get("/api/candidates/counts").json()["pending"] == 1
    assert client.get("/api/candidates/counts").json()["pending"] == 0  # 自己提交的不算等我审核

    # 不能审核自己提交的
    assert client.post(f"/api/candidates/{cid}/approve", json={}).status_code == 403
    r = wangwu.post(f"/api/candidates/{cid}/approve", json={"branch": "通用"})
    out = r.json()
    assert r.status_code == 200 and out["status"] == "approved" and out["reviewer"] == "王五"
    assert out["card_id"].startswith("bi-verified-wb-")

    # 同步脚本凭口令取卡，不用登录
    assert TestClient(app).get("/api/digital-human/cards").status_code == 401
    cards = TestClient(app).get("/api/digital-human/cards", headers=sync).json()
    assert cards["unsynced"] == 1
    card = cards["cards"][0]
    required = ["card_id", "product", "module", "kind", "title", "question", "phrasings", "answer", "internal_note",
                "not_applicable", "must_not", "sources", "deprecate_docs", "versions", "visibility", "gaps", "status",
                "confidence", "author", "reviewer", "created", "review_by"]
    assert all(k in card for k in required)
    assert card["status"] == "confirmed" and card["reviewer"] == "王五" and card["author"] == "李四"
    assert card["answer"] == "改成后台导出，完成后到消息中心下载。" and card["sources"]

    r = client.post("/api/digital-human/cards/synced", headers=sync,
                    json={"version": "v0.1.1.wb1", "card_ids": [card["card_id"]]})
    assert r.json()["unsynced"] == 0
    wangwu.post(f"/api/candidates/{cid}/retire", json={"note": "版本变了"})
    cards = client.get("/api/digital-human/cards", headers=sync).json()
    assert cards["unsynced"] == 1 and cards["cards"][0]["status"] == "retired"


def test_zhishu_knowledge_not_written_to_cards(client, fake_llm, wangwu, monkeypatch):
    monkeypatch.setattr(settings, "cards_sync_token", "sync-secret")
    _, cid = _edited_reply(client)
    client.post(f"/api/candidates/{cid}/confirm", json={"branch": "知数"})
    out = wangwu.post(f"/api/candidates/{cid}/approve", json={}).json()
    assert out["status"] == "approved" and out["card_id"] is None
    cards = client.get("/api/digital-human/cards", headers={"Authorization": "Bearer sync-secret"}).json()
    assert cards == {"cards": [], "unsynced": 0}


def test_cards_sync_needs_token(client):
    assert client.get("/api/digital-human/cards").status_code == 403  # 没配口令时不开放


def test_decline_and_not_general(client, fake_llm):
    _, cid = _edited_reply(client)
    assert client.post(f"/api/candidates/{cid}/decline").json()["status"] == "declined"
    fake_llm.results[reflow.Judgement] = reflow.Judgement(general=False, reason="只针对这个客户的任务")
    mid = _msg(client, "我们那个同步任务帮忙重启下")
    _adopt(client, _sug(client, mid)["id"], "已经帮您重启了")
    cand = _sug(client, mid)["candidate"]
    assert cand["status"] == "not_general"
    skipped = client.get("/api/candidates", params={"view": "skipped"}).json()
    assert {c["status"] for c in skipped} == {"declined", "not_general"}
    # AI 判断不收录的可以改为收录
    r = client.post(f"/api/candidates/{cand['id']}/confirm",
                    json={"question": "同步任务怎么重启", "answer": "在任务列表里点重启"})
    assert r.json()["status"] == "pending"


class FakeEmbedding:
    """按字符计数的假向量：字差不多的句子相似度高。"""

    def __init__(self):
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        out = []
        for t in texts:
            v = [0.0] * 64
            for ch in t:
                if not ch.isspace():
                    v[ord(ch) % 64] += 1
            n = math.sqrt(sum(x * x for x in v)) or 1
            out.append([x / n for x in v])
        return out


@pytest.fixture()
def fake_embedding(monkeypatch):
    fake = FakeEmbedding()
    monkeypatch.setattr(embedding, "_client", fake)
    monkeypatch.setattr(settings, "similar_qa_min_score", 0.8)
    return fake


def test_similar_history_and_workbench_knowledge(client, fake_llm, fake_embedding, wangwu, monkeypatch):
    # 本用例按一条消息一张建议测相似问答，不测一轮合并。
    monkeypatch.setattr(settings, "ai_round_window_minutes", 0)
    _, cid = _edited_reply(client, content="报表导出一直转圈怎么办？")
    # 别的群里同样的问题：带上历史回复
    mid = _msg(client, "报表导出一直在转圈怎么办", group="wrB", name="某券商-有数群")
    prompt = fake_llm.calls[-1][1]
    assert "<similar_qa>" in prompt and "[H1] 历史回复" in prompt and "后台导出" in prompt
    s = _sug(client, mid, group="wrB")
    h = next(k for k in s["knowledge"] if k["kind"] == "history")
    assert h["author"] == "李四" and h["group_name"] == "某银行-有数项目群" and h["score"] >= 0.8

    # 审核通过后，工作台知识排在前面
    client.post(f"/api/candidates/{cid}/confirm", json={})
    wangwu.post(f"/api/candidates/{cid}/approve", json={})
    fake_llm.draft_result = SuggestionDraft(can_answer=True, confidence="high", reply="改成后台导出即可。", reason="工作台知识",
                                            references=["W1"])
    mid = _msg(client, "报表导出总是转圈怎么办", group="wrC", name="某保险-有数群")
    prompt = fake_llm.calls[-1][1]
    assert "[W1] 工作台知识｜有数｜技术支持已审核" in prompt
    assert "[H1]" not in prompt  # 整理成工作台知识的那条回复不再重复给
    s = _sug(client, mid, group="wrC")
    assert [k["ref"] for k in s["knowledge"] if k["cited"]] == ["W1"]

    # 寒暄不查
    calls = fake_embedding.calls
    _msg(client, "好的", group="wrC")
    assert fake_embedding.calls == calls


def test_similar_failure_still_suggests(client, fake_llm, monkeypatch):
    class Broken:
        def embed(self, texts):
            raise embedding.EmbeddingError("连不上")

    monkeypatch.setattr(embedding, "_client", Broken())
    mid = _msg(client)
    s = _sug(client, mid)
    assert s["status"] == "ready" and "相似历史问答没查成" in s["knowledge_error"]


def test_adoption_stats(client, fake_llm, wangwu):
    s1 = _sug(client, _msg(client))
    _adopt(client, s1["id"], s1["content"])
    s2 = _sug(client, _msg(client))
    # 会话分流：王五要处理这个群，先把会话转给他
    r = client.post("/api/conversations/assign", json={"channel": "wecom", "group_id": "wrA", "staff_id": wangwu.staff_id})
    assert r.status_code == 200, r.text
    _adopt(wangwu, s2["id"], "完全自己写的另一段回复内容")
    s3 = _sug(client, _msg(client))
    wangwu.post(f"/api/suggestions/{s3['id']}/escalate", json={})
    fake_llm.draft_result = SuggestionDraft(needs_reply=False, can_answer=True, confidence="high", reply="不客气",
                                            reason="致谢", references=[])
    _msg(client, "谢谢")
    week = client.get("/api/stats/adoption", params={"weeks": 2}).json()["weeks"][0]
    people = {p["operator"]: p for p in week["people"]}
    assert people["李四"]["exact"] == 1 and people["李四"]["replied"] == 1
    assert people["王五"]["unused"] == 1 and people["王五"]["escalated"] == 1
    assert people["AI"]["dismissed"] == 1
    assert week["total"]["replied"] == 2 and week["total"]["exact"] == 1
