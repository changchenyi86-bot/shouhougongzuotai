"""回复里 @ 群成员：群成员名单、发送时带上要 @ 的人、发送程序回报没 @ 上的原因。"""

from datetime import datetime, timedelta, timezone

import pytest

from app import mentions
from app.config import settings

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
CUST = "wmAbCdEfGhIjKlMnOpQrStUvWxYz012345"  # wm 开头为微信用户
CUST2 = "woZyXwVuTsRqPoNmLkJiHgFeDcBa98765"  # wo 开头为其他企业的企业微信用户
GROUP = {"channel": "wecom", "group_id": "wrABC", "group_name": "某客户-有数项目群", "is_external_group": True}


@pytest.fixture()
def sending(client, monkeypatch):
    monkeypatch.setattr(settings, "send_agent_token", TOKEN)
    monkeypatch.setattr(settings, "send_min_interval_seconds", 0)
    r = client.post("/api/agent/heartbeat", headers=AUTH, json={"agent_id": "pc1", "wecom_ready": True})
    assert r.status_code == 200
    return client


def _msg(client, n, sender=CUST, name="张三", content="报表导出一直转圈怎么办？", minutes_ago=0):
    sent_at = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    r = client.post("/api/messages", json={**GROUP, "external_id": f"m{n}", "sent_at": sent_at,
                                           "sender_id": sender, "sender_name": name, "content": content})
    assert r.status_code == 201
    return r.json()["id"]


def _sid(client, message_id):
    items = client.get("/api/inbox", params={"view": "all"}).json()
    return next(i["suggestion"]["id"] for i in items if i["message"]["id"] == message_id)


def _send(client, content, at, **kw):
    return client.post("/api/conversations/send", json={"channel": "wecom", "group_id": "wrABC",
                                                        "content": content, "mentions": at, **kw})


def _claim(client, features=("mentions",)):
    r = client.post("/api/agent/claim", headers=AUTH, json={"agent_id": "pc1", "features": list(features)})
    assert r.status_code == 200
    return r.json()["task"]


def _outbox(client):
    return client.get("/api/conversation", params={"channel": "wecom", "group_id": "wrABC"}).json()["outbox"]


def test_members_from_directory_recent_speakers_first(client, monkeypatch):
    _msg(client, 1, sender=CUST2, name="李四", minutes_ago=30)
    _msg(client, 2, sender=CUST, name="张三", minutes_ago=5)
    monkeypatch.setattr(mentions, "_directory_members", lambda channel, group_id: {
        "zhangsan": "平台-小帆", CUST: "张三", CUST2: "李四", "wmQqQqQqQqQqQqQqQqQqQqQqQqQq": "张三"})
    r = client.get("/api/conversation/members", params={"channel": "wecom", "group_id": "wrABC"})
    assert r.status_code == 200
    got = [(m["id"], m["name"], m["kind"], m["duplicate"]) for m in r.json()]
    assert got == [
        (CUST, "张三", "wechat", True),  # 最近说过话
        (CUST2, "李四", "wecom", False),
        ("wmQqQqQqQqQqQqQqQqQqQqQqQqQq", "张三", "wechat", True),  # 没说过话：客户在员工前面
        ("zhangsan", "平台-小帆", "internal", False),
    ]


def test_members_fall_back_to_people_who_spoke(client, monkeypatch):
    _msg(client, 1, sender=CUST, name="张三")
    _msg(client, 2, sender="yuangong", name=None, content="收到")  # 没有名字的没法 @
    monkeypatch.setattr(mentions, "_directory_members", lambda channel, group_id: {})
    r = client.get("/api/conversation/members", params={"channel": "wecom", "group_id": "wrABC"})
    assert [m["name"] for m in r.json()] == ["张三"]


def test_send_with_mentions_and_agent_reports_warning(sending):
    sid = _sid(sending, _msg(sending, 1))
    # 填建议时自动加上的「@张三」不算改动：仍记为原样采纳
    r = _send(sending, "@张三 您好，请清理浏览器缓存后重试。", [{"user_id": CUST, "name": "张三"}], suggestion_id=sid)
    assert r.status_code == 201, r.text
    assert r.json()["mentions"] == [{"user_id": CUST, "name": "张三", "kind": "wechat"}]
    item = next(i for i in sending.get("/api/inbox", params={"view": "all"}).json()
                if i["suggestion"] and i["suggestion"]["id"] == sid)
    assert item["suggestion"]["status"] == "adopted" and item["suggestion"]["adoption"] == "exact"
    assert item["suggestion"]["final_content"] == "@张三 您好，请清理浏览器缓存后重试。"

    task = _claim(sending)
    assert task["mentions"] == [{"user_id": CUST, "name": "张三", "kind": "wechat"}]
    warning = "「张三」（群里有 2 个叫「张三」的人，分不清是哪一个）没 @ 上，按普通文字发出，对方收不到 @ 提醒"
    r = sending.post(f"/api/agent/tasks/{task['id']}/result", headers=AUTH,
                     json={"agent_id": "pc1", "ok": True, "warning": warning})
    assert r.status_code == 200
    out = _outbox(sending)
    assert out[0]["status"] == "sent" and out[0]["warning"] == warning


def test_old_agent_gets_task_but_warning_recorded(sending):
    _msg(sending, 1)
    assert _send(sending, "@张三 好的", [{"user_id": CUST, "name": "张三"}]).status_code == 201
    task = _claim(sending, features=())
    assert task["content"] == "@张三 好的"
    out = _outbox(sending)
    assert out[0]["warning"] == "发送电脑上的发送程序是旧版本，「张三」没 @ 上，按普通文字发出，对方收不到 @ 提醒。请更新发送程序"


def test_mention_must_be_in_text_in_order(sending):
    _msg(sending, 1)
    r = _send(sending, "好的", [{"user_id": CUST, "name": "张三"}])
    assert r.status_code == 422 and "找不到「@张三」" in r.json()["detail"]
    r = _send(sending, "@李四 @张三 好的", [{"name": "张三"}, {"name": "李四"}])
    assert r.status_code == 422


def test_resend_keeps_mentions(sending):
    _msg(sending, 1)
    _send(sending, "@张三 好的", [{"user_id": CUST, "name": "张三"}])
    task = _claim(sending)
    sending.post(f"/api/agent/tasks/{task['id']}/result", headers=AUTH,
                 json={"agent_id": "pc1", "ok": False, "error": "搜不到群"})
    r = sending.post(f"/api/send-tasks/{task['id']}/resend")
    assert r.status_code == 200 and r.json()["mentions"][0]["name"] == "张三"
    assert _claim(sending)["mentions"][0]["name"] == "张三"


def test_without_mentions():
    assert mentions.without_mentions("@张三 @张三丰 您好", ["张三", "张三丰"]) == "您好"
    assert mentions.without_mentions("好的 @李四 看下", ["李四"]) == "好的 看下"


def test_members_leave_out_the_sending_account(sending, monkeypatch):
    """发送电脑上的客服账号 @ 不了自己：工作台发出的回复在存档里是它说的，就不列进名单。"""
    _msg(sending, 1)
    monkeypatch.setattr(mentions, "_directory_members", lambda channel, group_id: {CUST: "张三", "kefu": "客服小易"})
    assert _send(sending, "好的，我看一下", []).status_code == 201
    task = _claim(sending)
    sending.post(f"/api/agent/tasks/{task['id']}/result", headers=AUTH, json={"agent_id": "pc1", "ok": True})
    names = lambda: [m["name"] for m in sending.get(  # noqa: E731
        "/api/conversation/members", params={"channel": "wecom", "group_id": "wrABC"}).json()]
    assert names() == ["张三", "客服小易"]
    _msg(sending, 2, sender="kefu", name="客服小易", content="好的，我看一下")  # 存档拉回来了：是客服账号发的
    assert names() == ["张三"]
