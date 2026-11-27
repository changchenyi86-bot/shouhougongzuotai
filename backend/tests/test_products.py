"""查哪个产品的知识：管理员指定 > 群名 > AI 判断 > 都查；建议卡上改产品；群产品设置。"""

import uuid

from app.ai import knowledge, products
from app.ai.suggest import RoundQuery
from app.models import Suggestion


def post(client, n, text, group="wrP", name="某客户项目群"):
    r = client.post("/api/messages", json={
        "channel": "wecom", "external_id": f"p{n}", "group_id": group, "group_name": name, "is_external_group": True,
        "sender_id": "wmAbCdEfGhIjKlMnOpQrStUvWxYz012345", "sender_name": "客户", "content": text,
        "sent_at": f"2026-11-28T08:00:{n:02d}Z"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def sug(mid):
    from app import db
    with db.SessionLocal() as s:
        return s.query(Suggestion).filter_by(message_id=uuid.UUID(mid)).one()


def test_by_name():
    assert products.by_name("华宝证券-网易-BD-支持") == ["ed"]
    assert products.by_name("众诚-网易-DB-支持") == ["ed"]
    assert products.by_name("陕旅-数据中台-BD-支持") == ["ed"]
    assert products.by_name("tcl华星光电-网易-知数-支持") == ["zs"]
    assert products.by_name("恒丰银行-网易-BI运维-支持") == ["bi"]
    assert products.by_name("湖北消金-网易-BD和Bi支持") == ["bi", "ed"]
    assert products.by_name("外高桥造船-网易-平台&BI-售前") == ["bi", "ed"]
    # 指标平台是有数的，不算中台的“平台”；英文单词里的字母不算
    assert products.by_name("某客户-指标平台-支持") == ["bi"]
    assert products.by_name("小浪底-网易-运维-支持") == [] and products.by_name("MOBILE ABDC 群") == []


def test_mcp_products_and_ai_decision():
    assert products.mcp_products(["ed"]) == ["ed"]
    assert products.mcp_products(["zs"]) == ["zs", "bi"]  # 只查知数时知数在前，再补有数
    assert products.mcp_products(["bi", "zs", "ed"]) == ["bi", "ed"]  # 有数会自动带上知数
    d = products.from_ai("中台", "截图里是离线开发的任务运维", "某客户群")
    assert (d.codes, d.source) == (["ed"], "ai")
    d = products.from_ai("不确定", "", "某客户-BD&BI-支持")
    assert (d.codes, d.source) == (["bi", "ed"], "all")
    assert products.from_ai(None, "", "某客户群").codes == ["bi", "zs", "ed"]


def test_generate_uses_group_name_without_ai(client, fake_llm, monkeypatch):
    seen = []
    monkeypatch.setattr(knowledge, "skip_reason", lambda text: None)
    monkeypatch.setattr(knowledge, "retrieve", lambda text, products=None: seen.append(products) or [])
    mid = post(client, 1, "调度没触发怎么办？", name="华宝证券-网易-BD-支持")
    s = sug(mid)
    assert seen == [["ed"]] and (s.products, s.product_source) == (["ed"], "group")
    # 单条消息、群名能定产品：不额外调 AI 判断
    assert not any(schema == RoundQuery for schema, _ in fake_llm.generated)


def test_generate_ai_judges_when_group_unclear(client, fake_llm, monkeypatch):
    seen = []
    monkeypatch.setattr(knowledge, "skip_reason", lambda text: None)
    monkeypatch.setattr(knowledge, "retrieve", lambda text, products=None: seen.append(products) or [])
    fake_llm.results[RoundQuery] = RoundQuery(question="离线任务一直等待运行怎么排查", product="中台",
                                              product_reason="说的是离线开发的任务调度")
    mid = post(client, 1, "@曹俊 任务一直在等待运行", group="wrQ", name="温氏股份-网易-BD&BI-支持")
    s = sug(mid)
    assert seen == [["ed"]] and (s.products, s.product_source) == (["ed"], "ai")
    assert "离线开发" in s.product_reason
    prompt = [p for schema, p in fake_llm.generated if schema == RoundQuery][-1]
    assert "<group_name>温氏股份-网易-BD&BI-支持</group_name>" in prompt
    # 判断不出：几个都查
    fake_llm.results[RoundQuery] = RoundQuery(question="这个怎么弄", product="不确定")
    mid = post(client, 2, "这个怎么弄", group="wrR", name="浙江电信-网易-内部沟通")
    assert seen[-1] == ["bi", "ed"] and sug(mid).product_source == "all"


def test_manual_setting_beats_group_name_and_override(client, fake_llm, monkeypatch):
    seen = []
    monkeypatch.setattr(knowledge, "skip_reason", lambda text: None)
    monkeypatch.setattr(knowledge, "retrieve", lambda text, products=None: seen.append(products) or [])
    mid = post(client, 1, "报表打不开", group="wrS", name="某客户-BD-支持")
    # 管理员给群指定知数：之后这个群按知数查
    r = client.put("/api/groups/product", json={"channel": "wecom", "group_id": "wrS", "products": ["zs"]})
    assert r.status_code == 204, r.text
    mid2 = post(client, 2, "问数结果不对", group="wrS", name="某客户-BD-支持")
    assert seen[-1] == ["zs", "bi"] and sug(mid2).product_source == "manual"
    # 建议卡上改成有数：按有数重新生成，记下改之前的判断
    r = client.post(f"/api/suggestions/{sug(mid2).id}/product", json={"products": ["bi"]})
    assert r.status_code == 200, r.text
    s = sug(mid2)
    assert seen[-1] == ["bi"] and (s.products, s.product_source) == (["bi"], "override")
    assert s.product_original == {"products": ["zs"], "source": "manual", "reason": "管理员给这个群指定的"}
    assert s.product_corrected_by == "李四"
    assert client.post(f"/api/suggestions/{s.id}/product", json={"products": ["xx"]}).status_code == 422
    assert sug(mid).product_source == "group"  # 指定之前生成的那条按群名


def test_group_products_list(client, fake_llm, monkeypatch):
    monkeypatch.setattr(knowledge, "skip_reason", lambda text: "没配置售后数字人知识库")
    post(client, 1, "你好", group="wrA", name="小浪底-网易-运维-支持")
    post(client, 2, "你好", group="wrB", name="某客户-BD-支持")
    post(client, 3, "你好", group="wrC", name="【模拟】模型连通测试（非真实群）")
    rows = client.get("/api/groups/products").json()
    assert [r["group_id"] for r in rows] == ["wrA", "wrB"]  # 要指定的在前，测试群不列
    a, b = rows
    assert a["needs_setting"] and a["effective"] == "ai" and a["by_name"] == []
    assert not b["needs_setting"] and b["effective"] == "group" and b["by_name"] == ["ed"]
    client.put("/api/groups/product", json={"channel": "wecom", "group_id": "wrA", "products": ["bi", "ed"]})
    a = client.get("/api/groups/products").json()[0]
    assert a["manual"] == ["bi", "ed"] and a["effective"] == "manual" and a["updated_by"] == "李四"
    client.put("/api/groups/product", json={"channel": "wecom", "group_id": "wrA", "products": None})
    assert client.get("/api/groups/products").json()[0]["manual"] is None


def test_recommend():
    rec, basis = products.recommend([], ["数开平台", "有数BI"], "中台组", None)
    assert rec == ["ed"] and "客户已购" in basis and "中台组" in basis
    from collections import Counter
    rec, basis = products.recommend([], None, None, Counter({"bi": 6, "ed": 1}))
    assert rec == ["bi"] and "AI 判断" in basis
    rec, _ = products.recommend(["bi", "ed"], ["有数BI"], None, None)
    assert rec == ["bi"]
