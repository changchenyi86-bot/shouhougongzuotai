"""整轮建议：延迟触发、范围、检索、合并及评测。"""
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app import db
from app.ai import evaluate, knowledge, similar, suggest, worker
from app.ai.llm import LLMError, SuggestionDraft
from app.config import settings
from app.models import Message, Suggestion, SuggestionStatus as S

BASE = datetime(2026, 11, 27, 2, tzinfo=timezone.utc)
CUSTOMER = "wmAbCdEfGhIjKlMnOpQrStUvWxYz012345"


def post(client, n, text, seconds=0, sender=CUSTOMER, group="wrA"):
    response = client.post("/api/messages", json={
        "channel": "wecom", "external_id": str(n), "group_id": group,
        "group_name": "客户项目-BI-支持群", "is_external_group": True, "sender_id": sender,
        "sender_name": "张三" if sender.startswith("wm") else "李四", "content": text,
        "sent_at": (BASE + timedelta(seconds=seconds)).isoformat(),
    })
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


def snapshot(mid):
    with db.SessionLocal() as session:
        return session.scalar(select(Suggestion).where(Suggestion.message_id == mid))


def collect(monkeypatch):
    pending = []
    monkeypatch.setattr(worker, "schedule", lambda delay, fn, *args: pending.append((delay, fn, args)))
    return pending


def run_pending(pending):
    for _, fn, args in pending[:]:
        fn(*args)


def test_three_messages_one_draft_and_two_customers(client, fake_llm, monkeypatch):
    pending = collect(monkeypatch)
    ids = [post(client, 1, "报表导出转圈？"), post(client, 2, "清理缓存仍不行", 1),
           post(client, 3, "另外登录报错？", 2, sender="wmOtherCustomer0123456789abcdef")]
    assert len(pending) == 3 and all(p[0] == 8 for p in pending)
    assert all(snapshot(mid).status == S.GENERATING for mid in ids)
    run_pending(pending)
    assert len(fake_llm.calls) == 1
    last = snapshot(ids[-1])
    assert last.status == S.READY and last.covers == list(map(str, ids))
    for mid in ids[:-1]:
        previous = snapshot(mid)
        assert previous.status == S.MERGED and previous.merged_into == last.id
    assert "<current_round>" in fake_llm.calls[0][1]


def test_ready_followup_merges_and_legacy_schema(client):
    first = post(client, 1, "导出报错？")
    old = snapshot(first)
    second = post(client, 2, "还是不行", 1)
    new = snapshot(second)
    assert new.covers == [str(first), str(second)] and snapshot(first).merged_into == new.id
    assert snapshot(first).content == old.content and snapshot(first).status == S.MERGED
    with db.SessionLocal() as session:
        sug = session.get(Suggestion, new.id)
        sug.covers = None
        session.commit()
        from app.schemas import SuggestionOut
        assert SuggestionOut.model_validate(sug).covers == [second]


def test_support_window_handled_and_status_history(client, fake_llm, monkeypatch):
    first = post(client, 1, "旧问题？", 0)
    assert client.post(f"/api/suggestions/{snapshot(first).id}/dismiss", json={}).status_code == 200
    post(client, 2, "先发截图", 1, sender="lisi")
    old = post(client, 3, "超过时间窗？", 2)
    recent = post(client, 4, "新问题？", 1900)
    latest = post(client, 5, "补充情况", 1901)
    current = snapshot(latest)
    assert current.covers == [str(recent), str(latest)]
    assert snapshot(first).status == S.DISMISSED and snapshot(old).status == S.READY
    prompt = fake_llm.calls[-1][1]
    assert "旧问题？（无需回复）" in prompt and "技术支持 李四" in prompt
    assert "还没回复" in prompt and "<current_round>" in prompt


def test_query_retrieval_failure_and_single(client, fake_llm, monkeypatch):
    seen = []
    monkeypatch.setattr(knowledge, "skip_reason", lambda text: None)
    monkeypatch.setattr(knowledge, "retrieve", lambda text, products=None: seen.append(("knowledge", text)) or [])
    monkeypatch.setattr(similar, "search", lambda session, query, exclude, **kw: seen.append(("similar", query, exclude)) or [])
    one = post(client, 1, "导出报错？")
    assert not any(schema == suggest.RoundQuery for schema, _ in fake_llm.generated)
    assert seen[0][1] == "导出报错？"
    two = post(client, 2, "还是不行", 1)
    assert any(schema == suggest.RoundQuery for schema, _ in fake_llm.generated)
    assert seen[-2][1] == seen[-1][1] == "补全后的客户问题"
    assert {one, two} <= seen[-1][2]
    original = fake_llm.generate
    def failing(system, prompt, schema):
        if schema is suggest.RoundQuery:
            raise LLMError("query failed")
        return original(system, prompt, schema)
    monkeypatch.setattr(fake_llm, "generate", failing)
    three = post(client, 3, "换浏览器也失败", 2)
    assert snapshot(three).status == S.READY
    assert seen[-2][1] == seen[-1][1] == "导出报错？\n还是不行\n换浏览器也失败"


def test_failed_generation_merges_and_no_auto_dismiss_for_question(client, fake_llm, monkeypatch):
    pending = collect(monkeypatch)
    first = post(client, 1, "有问题？")
    second = post(client, 2, "谢谢", 1)
    fake_llm.error = LLMError("failed")
    run_pending(pending)
    assert snapshot(second).status == S.FAILED and snapshot(first).status == S.MERGED
    assert snapshot(second).covers == [str(first), str(second)]
    fake_llm.error = None
    fake_llm.draft_result = SuggestionDraft(needs_reply=False, can_answer=True, confidence="high", reply="收到", reason="", references=[])
    pending.clear()
    third = post(client, 3, "还有问题？", 10, group="wrB")
    fourth = post(client, 4, "谢谢", 11, group="wrB")
    run_pending(pending)
    assert snapshot(fourth).status == S.READY and snapshot(third).status == S.MERGED


def test_merged_reply_accepted_and_target_unchanged(client, monkeypatch):
    first = post(client, 1, "旧问题？")
    second = post(client, 2, "追问？", 1)
    old, new = snapshot(first), snapshot(second)
    monkeypatch.setattr(settings, "send_agent_token", "test-token")
    assert client.post("/api/agent/heartbeat", headers={"Authorization": "Bearer test-token"},
                       json={"agent_id": "pc1", "wecom_ready": True}).status_code == 200
    response = client.post("/api/conversations/send", json={"channel": "wecom", "group_id": "wrA",
                           "suggestion_id": str(old.id), "content": old.content})
    assert response.status_code == 201, response.text
    assert snapshot(first).status == S.ADOPTED and snapshot(first).adoption == "exact"
    assert snapshot(second).status == S.READY and snapshot(second).id == new.id


def test_regenerate_and_resume_schedule_immediately(client, monkeypatch):
    first = post(client, 1, "问题？")
    pending = collect(monkeypatch)
    assert client.post(f"/api/suggestions/{snapshot(first).id}/regenerate").status_code == 200
    assert pending[-1][0] == 0
    with db.SessionLocal() as session:
        session.get(Message, first).sent_at = datetime.now(timezone.utc)
        session.commit()
    pending.clear()
    assert worker.resume_pending() == 1
    assert pending[0][0] == 0


def test_evaluation_round_with_labels_and_question(client, fake_llm, monkeypatch):
    monkeypatch.setattr(evaluate, "submit", lambda fn, *args: fn(*args))
    monkeypatch.setattr(evaluate, "_map", lambda fn, items: [fn(x) for x in items])
    fake_llm.results[evaluate.Verdict] = evaluate.Verdict(verdict="对", reason="回复正确")
    fake_llm.results[evaluate.QuestionVerdict] = evaluate.QuestionVerdict(reason="补全准确")
    case = {"id": "R1", "kind": "确认答对", "question": "还是不行", "expected": "检查日志",
            "context": "客户 张三：导出转圈（已回复）\n技术支持 李四：清理缓存",
            "round": ["客户 张三：还是不行", "客户 王五：登录也报错"], "expected_question": "导出和登录问题"}
    response = client.post("/api/eval/sets/round/import", files={"file": ("r.jsonl", json.dumps(case, ensure_ascii=False).encode())})
    assert response.status_code == 200, response.text
    response = client.post("/api/eval/runs", json={"set_name": "round"})
    assert response.status_code == 201, response.text
    result = client.get(f"/api/eval/runs/{response.json()['id']}/results").json()[0]
    assert result["question"] == "还是不行" and result["understood_question"] == "补全后的客户问题"
    assert "补全准确" in result["verdict_reason"]
    prompt = fake_llm.calls[-1][1]
    assert "客户 张三：导出转圈（已回复）" in prompt and "技术支持 李四" in prompt
    assert "客户 王五：登录也报错" in prompt and "<current_round>" in prompt


def test_pending_before_support_is_not_left_generating(client, monkeypatch):
    pending = collect(monkeypatch)
    first = post(client, 1, "第一个问题？")
    post(client, 2, "收到，正在处理", 1, sender="lisi")
    last = post(client, 3, "新的问题？", 2)
    run_pending(pending)
    assert snapshot(first).status == S.READY and snapshot(first).covers == [str(first)]
    assert snapshot(last).status == S.READY and snapshot(last).covers == [str(last)]


def test_equal_sent_time_uses_ingest_order(client, fake_llm, monkeypatch):
    pending = collect(monkeypatch)
    ids = [post(client, n, f"问题{n}？") for n in range(3)]
    run_pending(pending)
    assert len(fake_llm.calls) == 1
    assert snapshot(ids[-1]).covers == list(map(str, ids))
    assert all(snapshot(mid).status == S.MERGED for mid in ids[:-1])


def test_reflow_uses_round_original_and_index_uses_question(client, fake_llm, monkeypatch):
    from app.ai import reflow
    from app.ai.reflow import Judgement
    first = post(client, 1, "导出报错？")
    last = post(client, 2, "换浏览器不行", 1)
    sug = snapshot(last)
    recorded = []
    monkeypatch.setattr(similar, "_embed", lambda queries: recorded.extend(queries) or [[0.1]])
    monkeypatch.setattr(similar, "_upsert", lambda *args, **kwargs: recorded.append(kwargs))
    with db.SessionLocal() as session:
        sug = session.get(Suggestion, sug.id)
        sug.status, sug.final_content = S.ADOPTED, "请提供截图"
        assert similar.index_reply(session, sug)
        candidate = reflow.judge(session, sug)
        assert candidate.customer_question == "导出报错？\n换浏览器不行"
        prompt = next(p for schema, p in fake_llm.generated if schema == Judgement)
        assert "导出报错？\n换浏览器不行" in prompt and sug.question in prompt
        assert recorded[0] == sug.question and recorded[1]["question"] == sug.question
    assert snapshot(first).status == S.MERGED


def test_merged_coverage_survives_regenerate_and_followup(client, fake_llm, monkeypatch):
    pending = collect(monkeypatch)
    ids = [post(client, n, f"第{n}条问题？", n) for n in range(1, 4)]
    run_pending(pending)
    last = snapshot(ids[-1])
    assert last.covers == list(map(str, ids))
    pending.clear()
    response = client.post(f"/api/suggestions/{last.id}/regenerate")
    assert response.status_code == 200 and pending[0][0] == 0
    run_pending(pending)
    assert snapshot(ids[-1]).covers == list(map(str, ids))
    pending.clear()
    fourth = post(client, 4, "还有补充", 4)
    run_pending(pending)
    final = snapshot(fourth)
    assert final.covers == list(map(str, ids + [fourth]))
    for mid in ids:
        assert snapshot(mid).status == S.MERGED and snapshot(mid).merged_into == final.id
    prompt = fake_llm.calls[-1][1]
    current = prompt.split("<current_round>", 1)[1].split("</current_round>", 1)[0]
    assert all(f"第{n}条问题？" in current for n in range(1, 4))


@pytest.mark.parametrize("adoption", ["adopted", "edited"])
def test_merged_excluded_after_target_reply(client, adoption):
    first = post(client, 1, "旧问题？")
    second = post(client, 2, "旧追问？", 1)
    target = snapshot(second)
    body = {} if adoption == "adopted" else {"content": "新的回复内容"}
    response = client.post(f"/api/suggestions/{target.id}/adopt", json=body)
    assert response.status_code == 200 and response.json()["suggestion"]["status"] == adoption
    third = post(client, 3, "新的问题？", 2)
    assert snapshot(third).covers == [str(third)]
    assert snapshot(first).status == S.MERGED and snapshot(first).merged_into == target.id


def test_handled_newest_resumes_earlier_generation(client, fake_llm, monkeypatch):
    pending = collect(monkeypatch)
    first = post(client, 1, "第一个问题？", 1)
    middle = post(client, 2, "第二个问题？", 2)
    latest = post(client, 3, "最后一条问题？", 3)
    assert len(pending) == 3
    pending[0][1](*pending[0][2])
    pending[1][1](*pending[1][2])
    assert len(fake_llm.calls) == 0
    response = client.post(f"/api/suggestions/{snapshot(latest).id}/dismiss", json={})
    assert response.status_code == 200
    pending[2][1](*pending[2][2])
    assert len(pending) == 4 and pending[3][0] == 0 and pending[3][2] == (middle,)
    pending[3][1](*pending[3][2])
    assert len(fake_llm.calls) == 1
    assert snapshot(middle).status == S.READY and snapshot(middle).covers == list(map(str, [first, middle]))
    assert snapshot(first).status == S.MERGED and snapshot(first).merged_into == snapshot(middle).id
    assert snapshot(latest).status == S.DISMISSED


def test_zero_window_keeps_equal_timestamp_messages_separate(client, fake_llm, monkeypatch):
    monkeypatch.setattr(settings, "ai_round_window_minutes", 0)
    pending = collect(monkeypatch)
    ids = [post(client, n, f"问题{n}？") for n in range(2)]
    run_pending(pending)
    assert len(fake_llm.calls) == 2
    assert all(snapshot(mid).status == S.READY and snapshot(mid).covers == [str(mid)] for mid in ids)


def test_handled_during_generation_resumes_earlier(client, fake_llm, monkeypatch):
    pending = collect(monkeypatch)
    first = post(client, 1, "第一个问题？", 1)
    latest = post(client, 2, "后续问题？", 2)
    pending[0][1](*pending[0][2])  # 首条让给后续任务
    original = suggest.generate
    def generate_then_handle(session, message, **kwargs):
        result = original(session, message, **kwargs)
        with db.SessionLocal() as other:
            sug = other.scalar(select(Suggestion).where(Suggestion.message_id == latest))
            sug.status = S.DISMISSED
            other.commit()
        return result
    monkeypatch.setattr(suggest, "generate", generate_then_handle)
    pending[1][1](*pending[1][2])
    assert len(pending) == 3 and pending[2][0] == 0 and pending[2][2] == (first,)
    monkeypatch.setattr(suggest, "generate", original)
    pending[2][1](*pending[2][2])
    assert snapshot(first).status == S.READY and snapshot(latest).status == S.DISMISSED


def test_merged_coverage_respects_time_window_and_support(client, monkeypatch):
    pending = collect(monkeypatch)
    first = post(client, 1, "窗口外旧问题？", 0)
    second = post(client, 2, "接着问？", 10)
    run_pending(pending)
    pending.clear()
    third = post(client, 3, "窗口内追问？", 1805)
    run_pending(pending)
    assert snapshot(third).covers == [str(second), str(third)]
    pending.clear()
    post(client, 4, "我们已经处理", 1806, sender="lisi")
    fourth = post(client, 5, "我方发言后的新问题？", 1807)
    run_pending(pending)
    assert snapshot(fourth).covers == [str(fourth)]
    assert snapshot(first).status == S.MERGED


def test_no_question_round_skips_lookup(client, fake_llm, monkeypatch):
    """这一轮只是致谢、确认或客户之间商量：不改写成技术问题，也不查知识和相似问答。"""
    seen = []
    monkeypatch.setattr(knowledge, "skip_reason", lambda text: None)
    monkeypatch.setattr(knowledge, "retrieve", lambda text, products=None: seen.append(("knowledge", text)) or [])
    monkeypatch.setattr(similar, "search", lambda session, query, exclude, **kw: seen.append(("similar", query)) or [])
    fake_llm.results[suggest.RoundQuery] = suggest.RoundQuery(has_question=False, question="客户在商量明天谁来升级")
    post(client, 1, "导出报错？")
    seen.clear()
    latest = post(client, 2, "明天你来升级吧，我请假", 1)
    assert seen == []
    sug = snapshot(latest)
    assert sug.status == S.READY
    assert sug.question == "导出报错？\n明天你来升级吧，我请假"
    assert sug.knowledge_note == suggest.NO_QUESTION_NOTE


def test_missed_carries_to_merged_round(client, fake_llm):
    """转给同事求助后客户又补了话、并成新的一轮：这一轮照样记为 AI 没答上，待补知识里只出一条。"""
    from app.ai import reflow

    first = post(client, 1, "导出报错？", 0)
    with db.SessionLocal() as session:
        reflow.mark_missed(session.get(Suggestion, snapshot(first).id), "transfer", "王五")
        session.commit()
    latest = post(client, 2, "补充一下截图", 5)
    assert snapshot(first).status == S.MERGED
    current = snapshot(latest)
    assert current.missed == "transfer" and current.missed_by == "王五"
    gaps = client.get("/api/gaps").json()
    assert [g["suggestion_id"] for g in gaps["items"]] == [str(current.id)]
    assert gaps["items"][0]["customer_text"] == "导出报错？\n补充一下截图"
