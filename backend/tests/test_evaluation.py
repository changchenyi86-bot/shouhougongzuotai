"""回复建议评测：导入题库、跑一轮、打分、按块统计、和上一轮对比；全程不写消息和建议。"""

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app import db
from app.ai import evaluate, suggest
from app.ai.evaluate import Verdict
from app.ai.knowledge import KnowledgeItem
from app.config import settings
from app.main import app
from app.models import EvalRun, Message, Suggestion

CASES = [
    {"id": "EV-001", "kind": "确认答对", "branch": "有数", "group": "BI+知数", "question": "怎么设置报告的缓存有效期",
     "expected": "在刷新计划里设置缓存有效期", "reference": "可以在报告的刷新计划中设置……",
     "expected_keys": ["lzs08"], "source": "售后数字人问答记录"},
    {"id": "EV-002", "kind": "数字人自认没覆盖", "branch": "数据中台", "group": "数据中台",
     "question": "starrocks版本是多少", "expected": "知识库没有直接答案：如实说明没有覆盖，不编造"},
    {"id": "EV-003", "kind": "是否需要回复", "branch": "通用", "group": "通用", "question": "好的，谢谢",
     "expect_reply": False},
    {"id": "EV-004", "kind": "工作台真实群", "branch": "数据中台", "group": "数据中台",
     "question": "多个任务依赖同一个上游任务的话，会不会重复调起上游任务呢", "expected": "不会",
     "context": "客户：我们的任务现在是这么编排的\n技术支持：上下游任务之间设置依赖呀"},
]


def _jsonl(cases):
    return ("\n".join(json.dumps(c, ensure_ascii=False) for c in cases)).encode()


@pytest.fixture()
def ev(client, fake_llm, monkeypatch):
    """评测改为同步执行；知识库返回一篇固定的 FAQ；打分固定判「对」。"""
    monkeypatch.setattr(evaluate, "submit", lambda fn, *args: fn(*args))
    monkeypatch.setattr(evaluate, "_map", lambda fn, items: [fn(x) for x in items])
    monkeypatch.setattr(settings, "knowledge_mcp_url", "http://digital-human.test/messages")
    faq = KnowledgeItem(kind="faq", branch="有数", title="有数的缓存与刷新", source_id="bi-faq-lzs08.md",
                        url="https://study.sf.163.com/documents/read/privatization/lzs08", snippet="缓存")
    monkeypatch.setattr(suggest, "retrieve_knowledge", lambda session, message, query: [faq.model_copy()])
    fake_llm.results[Verdict] = Verdict(verdict="对", reason="和期望一致")
    r = client.post("/api/eval/sets/v1/import", files={"file": ("v1.jsonl", _jsonl(CASES))})
    assert r.status_code == 200, r.text
    assert r.json() == {"name": "v1", "added": 4, "updated": 0, "retired": 0}
    return client


def _start(client, **body):
    r = client.post("/api/eval/runs", json={"set_name": "v1", **body})
    assert r.status_code == 201, r.text
    return r.json()


def _area(run, name):
    return next(b for b in run["summary"] if b["area"] == name)


def test_import_updates_and_retires(ev):
    sets = ev.get("/api/eval/sets").json()
    assert sets[0]["name"] == "v1" and sets[0]["total"] == 4
    assert sets[0]["areas"] == {"BI+知数": 1, "数据中台": 2, "通用": 1}
    # 重新导入：改一题、去掉一题
    changed = [dict(CASES[0], expected="新的要点"), CASES[1], CASES[2]]
    r = ev.post("/api/eval/sets/v1/import", files={"file": ("v1.jsonl", _jsonl(changed))})
    assert r.json() == {"name": "v1", "added": 0, "updated": 3, "retired": 1}
    assert ev.get("/api/eval/sets").json()[0]["total"] == 3


def test_import_rejects_bad_records(ev):
    bad = ev.post("/api/eval/sets/v2/import", files={"file": ("x.jsonl", _jsonl([{"id": "A", "kind": "乱写", "question": "q"}]))})
    assert bad.status_code == 422 and "题型" in bad.json()["detail"]
    dup = ev.post("/api/eval/sets/v2/import", files={"file": ("x.jsonl", _jsonl([CASES[0], CASES[0]]))})
    assert dup.status_code == 422 and "重复" in dup.json()["detail"]
    assert ev.post("/api/eval/sets/v2/import", files={"file": ("x.jsonl", b"not json")}).status_code == 422


def test_run_scores_by_area_without_touching_conversations(ev, fake_llm):
    run = _start(ev, note="基线")
    detail = ev.get(f"/api/eval/runs/{run['id']}").json()
    assert detail["status"] == "done" and detail["done"] == 4 and detail["total"] == 4
    assert detail["note"] == "基线" and detail["previous"] is None

    bi = _area(detail, "BI+知数")
    assert bi["passed"] == 1 and bi["retrieval_total"] == 1 and bi["retrieval_hit"] == 1
    # 是否需要回复：FakeLLM 默认判「要回复」，题目期望「不用回复」
    general = _area(detail, "通用")
    assert general["passed"] == 0 and general["verdicts"] == {"判断错": 1}
    assert _area(detail, "数据中台")["passed"] == 2

    results = {r["code"]: r for r in ev.get(f"/api/eval/runs/{run['id']}/results").json()}
    assert results["EV-001"]["retrieval_rank"] == 1 and results["EV-001"]["verdict"] == "对"
    assert results["EV-003"]["verdict"] == "判断错" and results["EV-003"]["passed"] is False
    assert results["EV-004"]["has_expected_docs"] is False and results["EV-004"]["retrieval_rank"] is None

    # 起草建议的提示词里有题目自带的上下文；打分的提示词里有期望答案
    drafts = [p for _, p in fake_llm.calls]
    assert any("上下游任务之间设置依赖呀" in p and "会不会重复调起上游任务" in p for p in drafts)
    judged = [p for schema, p in fake_llm.generated if schema is Verdict]
    assert len(judged) == 3 and any("在刷新计划里设置缓存有效期" in p for p in judged)
    # 没有标准答案的题用另一套判分规则
    assert any("没有可靠的标准答案" in p and "starrocks" in p for p in judged)

    with db.SessionLocal() as s:
        assert s.query(Message).count() == 0 and s.query(Suggestion).count() == 0


def test_no_standard_case_counts_honest_reply_as_pass(ev, fake_llm):
    fake_llm.results[Verdict] = Verdict(verdict="没答上", reason="只说需要确认")
    run = _start(ev, areas=["数据中台"])
    detail = ev.get(f"/api/eval/runs/{run['id']}").json()
    assert detail["total"] == 2 and [b["area"] for b in detail["summary"]] == ["数据中台"]
    results = {r["code"]: r for r in ev.get(f"/api/eval/runs/{run['id']}/results").json()}
    assert results["EV-002"]["verdict"] == "对"  # 知识库没有的题，如实说明就算对
    assert results["EV-004"]["verdict"] == "没答上" and results["EV-004"]["passed"] is False


def test_compare_with_previous_run(ev, fake_llm):
    first = _start(ev)
    fake_llm.results[Verdict] = Verdict(verdict="错", reason="答成了别的模块")
    second = _start(ev)
    detail = ev.get(f"/api/eval/runs/{second['id']}").json()
    assert detail["previous"]["run_id"] == first["id"]
    assert detail["previous"]["worse"] == 3 and detail["previous"]["better"] == 0
    results = {r["code"]: r for r in ev.get(f"/api/eval/runs/{second['id']}/results").json()}
    assert results["EV-001"]["previous_passed"] is True and results["EV-001"]["passed"] is False


def test_judge_failure_and_generation_failure_are_recorded(ev, fake_llm, monkeypatch):
    def broken(case):
        raise suggest.SuggestionFailed("模型接口超时")

    monkeypatch.setattr(suggest, "generate", lambda *a, **k: broken(None))
    run = _start(ev)
    results = ev.get(f"/api/eval/runs/{run['id']}/results").json()
    assert all(r["error"] == "模型接口超时" and r["passed"] is None for r in results)
    assert _area(ev.get(f"/api/eval/runs/{run['id']}").json(), "BI+知数")["errors"] == 1


def test_only_one_run_at_a_time_and_stop(ev, monkeypatch):
    monkeypatch.setattr(evaluate, "submit", lambda fn, *args: None)  # 先不跑
    run = _start(ev)
    again = ev.post("/api/eval/runs", json={"set_name": "v1"})
    assert again.status_code == 409 and "在跑" in again.json()["detail"]
    assert ev.post(f"/api/eval/runs/{run['id']}/stop").json()["status"] == "stopping"
    evaluate.execute(uuid.UUID(run["id"]))
    assert ev.get(f"/api/eval/runs/{run['id']}").json()["status"] == "stopped"


def test_resume_after_restart(ev, monkeypatch):
    monkeypatch.setattr(evaluate, "submit", lambda fn, *args: None)  # 模拟跑到一半后台重启
    run = _start(ev)
    monkeypatch.setattr(evaluate, "submit", lambda fn, *args: fn(*args))
    assert evaluate.resume() == 1
    detail = ev.get(f"/api/eval/runs/{run['id']}").json()
    assert detail["status"] == "done" and detail["done"] == 4


def test_admin_only_for_import_and_start(ev):
    ev.post("/api/staff", json={"name": "王五", "username": "wangwu", "password": "secret-123", "role": "support"})
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"username": "wangwu", "password": "secret-123"}).status_code == 200
    assert c.post("/api/eval/runs", json={"set_name": "v1"}).status_code == 403
    assert c.post("/api/eval/sets/v1/import", files={"file": ("v1.jsonl", _jsonl(CASES))}).status_code == 403
    assert c.get("/api/eval/runs").status_code == 200  # 结果大家都能看
    with db.SessionLocal() as s:
        assert s.query(EvalRun).count() == 0


def test_judge_prompt_uses_reference_by_kind():
    from app.ai.evaluate import CaseSpec, judge_prompt

    good = CaseSpec(id=None, kind="确认答对", question="q", context="", expected="", must_not="",
                    reference="被认可的回答", expected_keys=[], expect_reply=None)
    assert "<reference_answer>" in judge_prompt(good, "回复", True)
    # 点踩的那条回答不能当参照；期望答案还没整理时按「没有标准答案」判
    bad = CaseSpec(id=None, kind="确认答错", question="q", context="", expected="待技术支持补充", must_not="",
                   reference="被点踩的回答", expected_keys=[], expect_reply=None)
    p = judge_prompt(bad, "回复", True)
    assert "<reference_answer>" not in p and "<wrong_answer>" in p and "没有可靠的标准答案" in p
    fixed = CaseSpec(id=None, kind="确认答错", question="q", context="", expected="不会带出序号", must_not="说可以导出",
                     reference="被点踩的回答", expected_keys=[], expect_reply=None)
    p = judge_prompt(fixed, "回复", True)
    assert "<expected>\n不会带出序号" in p and "<must_not>" in p and "没有可靠的标准答案" not in p
