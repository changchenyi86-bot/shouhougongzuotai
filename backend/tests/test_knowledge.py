"""生成建议时查售后数字人知识：检索、拼进提示词、记录引用。检索服务用假的 MCP 接口代替。"""

import json

import httpx
import pytest

from app.ai import embedding, knowledge
from app.ai.knowledge import KnowledgeClient, search_query
from app.ai.llm import SuggestionDraft, parse_draft
from app.config import settings

MSG = {
    "channel": "feishu",
    "external_id": "om_k1",
    "group_id": "oc_k",
    "group_name": "【模拟】某客户-有数项目群",
    "is_external_group": True,
    "sender_id": "ou_1",
    "sender_name": "张三",
    "content": "@技术支持 报表导出 Excel 打开提示文件损坏怎么办？",
    "sent_at": "2026-11-20T08:00:00Z",
}

ARTICLE = """---
id: bi-faq-cjwt14
title: "Excel导出后，打开提示Excel文件损坏"
type: faq
source_url: https://study.sf.163.com/documents/read/privatization/cjwt14
---

## Excel导出后，打开提示Excel文件损坏

![](/documents/uploads/projects/FAQ/1.png)

原因是有重名报表，导出后同名的报告会变成同名的 sheet 导致 Excel 报错。删除或改名重名报告即可。
"""

SEARCH = {
    "manual": [{"file": "bi-manual-daochu.md", "title": "导出", "source": "manual", "index": "bi-manual",
                "snippet": "支持导出当前sheet页面为Excel"}],
    "faq": [{"file": "bi-faq-cjwt14.md", "title": "Excel导出后，打开提示Excel文件损坏", "source": "faq",
             "index": "bi-faq", "snippet": "原因是有重名报表"}],
    "": [{"file": "bi-faq-cjwt14.md", "title": "Excel导出后，打开提示Excel文件损坏", "source": "faq",
          "index": "bi-faq", "snippet": "原因是有重名报表"},
         {"file": "zs-bug-188123.md", "title": "导出Excel失效", "source": "bug", "index": "zs-overmind",
          "snippet": "![截图](http://x/y.png) Merge branch"}],
}

CARD = {"card_id": "bi-verified-export-dup-sheet", "product": "bi", "title": "导出 Excel 提示损坏：重名报告",
        "question": "导出的 Excel 打不开，提示文件损坏", "answer": "检查是否有重名报告，改名后重新导出。",
        "not_applicable": "导出 CSV 时不适用", "must_not": ["说是浏览器问题"], "internal_note": "7.x 版本已知问题",
        "visibility": "public", "card_status": "draft", "status": "ok"}


class FakeMCP:
    """记录调用的工具，按工具名返回固定结果。"""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.fail = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        name, args = body["params"]["name"], body["params"]["arguments"]
        self.calls.append((name, args))
        if self.fail:
            return httpx.Response(502, text="bad gateway")
        if name == "search":
            result = {"results": SEARCH[args["type"]][: args["top"]], "signals": {}}
        elif name == "case_match":
            result = {"status": "ok", "hits": [
                {"card_id": CARD["card_id"], "title": CARD["title"], "candidate": True, "coverage": 0.6},
                {"card_id": "bi-verified-unrelated", "title": "不相关", "candidate": False, "coverage": 0.1}]}
        elif name == "case_card":
            result = CARD
        elif name == "read_article":
            text = ARTICLE if args["file"] == "bi-faq-cjwt14.md" else f"File not found: {args['file']}"
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1,
                                             "result": {"content": [{"type": "text", "text": text}]}})
        else:
            raise AssertionError(name)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {
            "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}})


@pytest.fixture()
def mcp(monkeypatch):
    fake = FakeMCP()
    monkeypatch.setattr(settings, "knowledge_mcp_url", "http://mcp.test/messages")
    knowledge.set_client(KnowledgeClient(settings.knowledge_mcp_url,
                                         client=httpx.Client(transport=httpx.MockTransport(fake.handler))))
    yield fake
    knowledge.set_client(None)


def _post(client, **kw):
    r = client.post("/api/messages", json={**MSG, **kw})
    assert r.status_code == 201


def _suggestion(client):
    return client.get("/api/inbox", params={"view": "all"}).json()[0]["suggestion"]


def test_search_query():
    assert search_query("@技术支持 报表打不开了") == "报表打不开了"
    for text in ("好的", "好的谢谢！", "收到👍", "OK", "嗯嗯", "  ", "@张三 ？"):
        assert search_query(text) is None, text


def test_retrieve_cards_then_documents(mcp):
    items = knowledge.retrieve(MSG["content"])
    assert [k.ref for k in items] == ["K1", "K2", "K3", "K4"]
    card, manual, faq, bug = items
    assert card.kind == "card" and card.branch == "有数" and card.card_status == "draft"
    assert "改名后重新导出" in card.content and "不适用：导出 CSV" in card.content and "不能说：说是浏览器问题" in card.content
    # 三路轮流合并、按文件去重：手册、FAQ、不限类型里新出现的缺陷单
    assert (manual.kind, faq.kind, bug.kind) == ("manual", "faq", "bug")
    assert bug.branch == "知数" and "![" not in bug.snippet and "公开链接" not in bug.to_prompt()
    # 前几篇读全文：去掉头部元数据和图片，取出原文链接；读不到的只用摘要
    assert faq.url == "https://study.sf.163.com/documents/read/privatization/cjwt14"
    assert faq.content.startswith("## Excel导出后") and "![" not in faq.content and "source_url" not in faq.content
    assert manual.content == "" and manual.url is None
    # 只读覆盖度够的卡片；@ 的人名不进检索词
    assert [a["card_id"] for n, a in mcp.calls if n == "case_card"] == [CARD["card_id"]]
    assert all(a["query"] == "报表导出 Excel 打开提示文件损坏怎么办？" for n, a in mcp.calls if n == "search")
    assert {a["type"] for n, a in mcp.calls if n == "search"} == {"manual", "faq", ""}


def test_retrieve_links_beyond_read_full(mcp, monkeypatch):
    monkeypatch.setattr(settings, "knowledge_read_full", 1)
    faq = knowledge.retrieve(MSG["content"])[2]
    # 只有第一篇带正文，后面的也读一下取原文链接
    assert faq.kind == "faq" and faq.content == "" and faq.url.startswith("https://study.sf.163.com/")
    assert ("[K3] FAQ｜有数\n标题：Excel导出后，打开提示Excel文件损坏\n"
            "公开链接（客户能打开）：https://study.sf.163.com/documents/read/privatization/cjwt14\n原因是有重名报表") == faq.to_prompt()


def test_retrieve_by_products(mcp):
    items = knowledge.retrieve(MSG["content"], ["ed"])
    assert {a["product"] for n, a in mcp.calls if n in ("search", "case_match")} == {"ed"}
    assert len(items) == 4
    mcp.calls.clear()
    # 几个产品都查：各查一遍，按名次轮流合并；假接口两个产品返回同样的文件，按文件去重
    items = knowledge.retrieve(MSG["content"], ["bi", "ed"])
    assert [a["product"] for n, a in mcp.calls if n == "case_match"] == ["bi", "ed"]
    assert [k.source_id for k in items] == [CARD["card_id"], "bi-manual-daochu.md", "bi-faq-cjwt14.md", "zs-bug-188123.md"]
    assert [k.ref for k in items] == ["K1", "K2", "K3", "K4"]
    # 只读最后入选的几篇
    assert len([1 for n, a in mcp.calls if n == "read_article"]) == 3


class FakeEmbedder:
    """标题含「损坏」的文档向量和问题最接近，其余正交；调用失败时抛 EmbeddingError。"""

    def __init__(self, fail=False):
        self.fail, self.calls = fail, 0

    def embed(self, texts):
        self.calls += 1
        if self.fail:
            raise embedding.EmbeddingError("timed out")
        return [[1.0, 0.0] if ("损坏" in t or i == 0) else [0.0, 1.0] for i, t in enumerate(texts)]


def test_rerank_by_vector_and_title(monkeypatch):
    rows = [{"file": "a.md", "title": "导出设置", "snippet": "导出当前页"},
            {"file": "c.md", "title": "Excel导出后，打开提示Excel文件损坏", "snippet": "重名报表"},
            {"file": "b.md", "title": "员工信息登记表", "snippet": "登记"}]
    query = "报表导出 Excel 打开提示文件损坏怎么办"
    monkeypatch.setattr(embedding, "_client", FakeEmbedder())
    # 向量和标题都更贴的 c 从第 2 升到第 1
    assert [r["file"] for r in knowledge.rerank(query, rows)] == ["c.md", "a.md", "b.md"]
    # 标题前缀不算；中文按相邻两字切
    assert knowledge._words("FAQ-文件损坏") == {"文件", "件损", "损坏"}
    # 向量模型失败或没配置：保持原顺序
    monkeypatch.setattr(embedding, "_client", FakeEmbedder(fail=True))
    assert [r["file"] for r in knowledge.rerank(query, rows)] == ["a.md", "c.md", "b.md"]
    monkeypatch.setattr(embedding, "_client", None)
    monkeypatch.setattr(settings, "embedding_base_url", "")
    assert [r["file"] for r in knowledge.rerank(query, rows)] == ["a.md", "c.md", "b.md"]


def test_documents_search_pool_and_rerank(mcp, monkeypatch):
    fake = FakeEmbedder()
    monkeypatch.setattr(embedding, "_client", fake)
    items = knowledge.retrieve(MSG["content"])
    # 每路取 RERANK_POOL 篇，合并后整体重排一次（一次向量调用），再取前几篇
    assert {a["top"] for n, a in mcp.calls if n == "search"} == {knowledge.RERANK_POOL}
    assert fake.calls == 1 and len([k for k in items if k.kind != "card"]) == 3


def test_retrieve_skips_small_talk_and_unconfigured(mcp, monkeypatch):
    assert knowledge.retrieve("好的谢谢") == [] and mcp.calls == []
    monkeypatch.setattr(settings, "knowledge_mcp_url", "")
    knowledge.set_client(None)
    assert knowledge.retrieve(MSG["content"]) == []


def test_suggestion_cites_knowledge(client, fake_llm, mcp):
    fake_llm.draft_result = SuggestionDraft(can_answer=True, confidence="medium", reply="您好，请检查是否有重名报告。",
                                            reason="纠错卡", references=["K1", "[K3]", "K9"])
    _post(client)
    prompt = fake_llm.calls[-1][1]
    assert "<reference>" in prompt and "[K1] 纠错卡｜有数｜草稿，技术支持还没确认" in prompt
    assert "[K4] 缺陷单｜知数" in prompt and "同名的 sheet" in prompt
    sug = _suggestion(client)
    assert sug["status"] == "ready" and sug["knowledge_error"] is None
    # 引用的排在前面；模型编造的 K9 不算
    assert [(k["ref"], k["cited"]) for k in sug["knowledge"]] == [("K1", True), ("K3", True), ("K2", False), ("K4", False)]
    assert "content" not in sug["knowledge"][0]  # 全文不保存
    assert sug["knowledge"][1]["url"].startswith("https://study.sf.163.com/")


def test_knowledge_failure_still_suggests(client, fake_llm, mcp):
    mcp.fail = True
    _post(client)
    sug = _suggestion(client)
    assert sug["status"] == "ready" and sug["knowledge"] == []
    assert "知识库查询失败" in sug["knowledge_error"]
    assert "<reference>" not in fake_llm.calls[-1][1]
    # 恢复后重新生成，带上参考资料，失败原因清掉
    mcp.fail = False
    assert client.post(f"/api/suggestions/{sug['id']}/regenerate").status_code == 200
    sug = _suggestion(client)
    assert sug["knowledge_error"] is None and len(sug["knowledge"]) == 4


def test_no_knowledge_configured(client, fake_llm):
    _post(client)
    sug = _suggestion(client)
    assert sug["status"] == "ready" and sug["knowledge"] == [] and sug["knowledge_error"] is None
    assert "<reference>" not in fake_llm.calls[-1][1]


def test_parse_draft_references_optional():
    d = parse_draft('{"can_answer": true, "confidence": "high", "reply": "好", "reason": "r"}')
    assert d.references == []
    d = parse_draft('{"can_answer": true, "confidence": "high", "reply": "好", "reason": "r", "references": "K1"}')
    assert d.references == []
    d = parse_draft('{"can_answer": true, "confidence": "high", "reply": "好", "reason": "r", "references": ["K2"]}')
    assert d.references == ["K2"]
