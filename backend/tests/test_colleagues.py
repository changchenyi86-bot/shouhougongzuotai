"""同事的个人微信：名单上的外部账号按我方处理。"""

from datetime import datetime, timezone

COLLEAGUE = "wmColleague0123456789abcdefghijklmno"
CUSTOMER = "wmCustomer0123456789abcdefghijklmnop"
N = 0


def _msg(client, sender, content, name="客户张三", group="wrA"):
    global N
    N += 1
    r = client.post("/api/messages", json={
        "channel": "wecom", "external_id": f"c{N}", "group_id": group, "group_name": "某银行-BD-支持",
        "is_external_group": True, "sender_id": sender, "sender_name": name, "content": content,
        "sent_at": datetime(2026, 11, 27, 3, N % 60, tzinfo=timezone.utc).isoformat(),
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _items(client, group="wrA"):
    return {i["message"]["id"]: i for i in
            client.get("/api/conversation", params={"channel": "wecom", "group_id": group}).json()["items"]}


def test_colleague_list_treats_personal_wechat_as_staff(client, fake_llm):
    mid = _msg(client, COLLEAGUE, "这个是分区表结构没更新，加字段要带 CASCADE", name="网易-BI技术支持-唐伊杰")
    _msg(client, COLLEAGUE, "我看下", name="网易-BI技术支持-唐伊杰", group="wrB")
    _msg(client, COLLEAGUE, "稍等", name="网易-BI技术支持-唐伊杰", group="wrC")
    item = _items(client)[mid]
    assert item["message"]["from_customer"] is True and item["suggestion"]["status"] == "ready"

    cands = client.get("/api/colleague-accounts/candidates").json()
    assert [c["sender_id"] for c in cands] == [COLLEAGUE] and cands[0]["groups"] == 3

    r = client.post("/api/colleague-accounts", json={"sender_id": COLLEAGUE})
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["closed"] == 3 and out["account"]["name"] == "网易-BI技术支持-唐伊杰"
    item = _items(client)[mid]
    assert item["message"]["from_customer"] is False and item["suggestion"]["status"] == "closed"
    assert client.get("/api/colleague-accounts/candidates").json() == []

    # 之后同事再说话不生成建议；客户的问题照常
    calls = len(fake_llm.calls)
    mid2 = _msg(client, COLLEAGUE, "我确认下")
    assert _items(client)[mid2]["suggestion"] is None and len(fake_llm.calls) == calls
    mid3 = _msg(client, CUSTOMER, "抽数任务能批量改来源吗？")
    assert _items(client)[mid3]["suggestion"]["status"] == "ready"

    # 移出名单后又按客户处理
    assert client.delete(f"/api/colleague-accounts/wecom/{COLLEAGUE}").status_code == 204
    assert _items(client)[mid]["message"]["from_customer"] is True
    assert client.get("/api/colleague-accounts").json() == []


def test_colleague_list_admin_only(client):
    r = client.post("/api/staff", json={"name": "王五", "username": "ww", "password": "secret-123", "role": "support"})
    assert r.status_code == 201
    from fastapi.testclient import TestClient

    from app.main import app

    c = TestClient(app)
    assert c.post("/api/auth/login", json={"username": "ww", "password": "secret-123"}).status_code == 200
    assert c.get("/api/colleague-accounts").status_code == 403
    assert c.post("/api/colleague-accounts", json={"sender_id": COLLEAGUE}).status_code == 403
