"""结束会话：没回复的客户消息随会话结束，客户再发消息时重新回到待处理。"""

from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
CUSTOMER = "wmAbCdEfGhIjKlMnOpQrStUvWxYz012345"
T0 = datetime.now(timezone.utc) - timedelta(hours=1)
GROUP = {"channel": "wecom", "group_id": "wrA"}


def _msg(client, n, group="wrA", sender=CUSTOMER):
    r = client.post("/api/messages", json={
        "channel": "wecom", "external_id": f"{group}-{n}", "group_id": group, "group_name": f"{group}群",
        "is_external_group": True, "sender_id": sender, "content": f"第{n}条",
        "sent_at": (T0 + timedelta(minutes=n)).isoformat(),
    })
    assert r.status_code == 201
    return r.json()["id"]


def _conv(client, group="wrA"):
    return client.get("/api/conversation", params={"channel": "wecom", "group_id": group}).json()


def _status(client, group="wrA"):
    return {i["message"]["content"]: i["suggestion"]["status"] for i in _conv(client, group)["items"] if i["suggestion"]}


def _sid(client, content):
    return next(i["suggestion"]["id"] for i in _conv(client)["items"] if i["message"]["content"] == content)


def _todo(client):
    return {g["group_id"]: g["pending_count"] for g in client.get("/api/groups", params={"view": "todo"}).json()}


def _end(client, group="wrA", operator="李四"):
    return client.post("/api/conversations/end", json={"channel": "wecom", "group_id": group, "operator": operator})


def test_end_closes_unreplied_and_reopens_on_new_message(client, monkeypatch):
    # 本用例按一条消息一张建议测结束会话，不测一轮合并。
    monkeypatch.setattr(settings, "ai_round_window_minutes", 0)
    for n in (1, 2, 3):
        _msg(client, n)
    _msg(client, 4, sender="lisi")  # 员工发言没有建议
    _msg(client, 5, group="wrB")
    client.post(f"/api/suggestions/{_sid(client, '第2条')}/adopt", json={"content": "好的", "operator": "李四"})
    assert _todo(client) == {"wrA": 2, "wrB": 1}

    r = _end(client)
    assert r.status_code == 201
    end = r.json()
    assert end["ended_by"] == "李四" and end["closed_count"] == 2  # 第1条、第3条（待处理）
    # 已回复的不受影响，其余随会话结束；别的群不受影响
    assert _status(client) == {"第1条": "closed", "第2条": "edited", "第3条": "closed"}
    assert _todo(client) == {"wrB": 1}
    conv = _conv(client)
    assert [e["id"] for e in conv["ends"]] == [end["id"]]
    closed = next(i["suggestion"] for i in conv["items"] if i["message"]["content"] == "第3条")
    assert closed["handled_by"] == "李四"

    # 没有要处理的了，再结束一次没有意义
    assert _end(client).status_code == 409

    # 客户再发消息：重新回到待处理，旧消息仍是已结束
    _msg(client, 6)
    assert _todo(client) == {"wrA": 1, "wrB": 1}
    assert _status(client)["第6条"] == "ready" and _status(client)["第3条"] == "closed"
    assert _end(client).json()["closed_count"] == 1
    assert len(_conv(client)["ends"]) == 2


def test_closed_message_can_still_be_replied(client):
    _msg(client, 1)
    sid = _sid(client, "第1条")
    _end(client)
    # 结束后不能再标记无需回复，但可以补上回复
    assert client.post(f"/api/suggestions/{sid}/dismiss", json={}).status_code == 409
    r = client.post(f"/api/suggestions/{sid}/adopt", json={"content": "补充一下：已修复", "operator": "李四"})
    assert r.status_code == 200 and r.json()["suggestion"]["status"] == "edited"


@pytest.fixture()
def sending(client, monkeypatch):
    monkeypatch.setattr(settings, "send_agent_token", TOKEN)
    monkeypatch.setattr(settings, "send_min_interval_seconds", 0)
    r = client.post("/api/agent/heartbeat", headers=AUTH, json={"agent_id": "pc1", "wecom_ready": True})
    assert r.status_code == 200
    return client


def _claim_and_fail(client):
    task = client.post("/api/agent/claim", headers=AUTH, json={"agent_id": "pc1"}).json()["task"]
    client.post(f"/api/agent/tasks/{task['id']}/result", headers=AUTH, json={"agent_id": "pc1", "ok": False, "error": "x"})


def test_end_drops_unsent_replies_but_keeps_queued(sending):
    _msg(sending, 1)
    # 一条回复发送失败、一条直接在会话里发的还在排队
    sending.post(f"/api/suggestions/{_sid(sending, '第1条')}/adopt", json={"send": True, "operator": "李四"})
    _claim_and_fail(sending)
    queued = sending.post("/api/conversations/send", json={**GROUP, "content": "稍等", "operator": "李四"}).json()
    assert _todo(sending) == {"wrA": 1}

    assert _end(sending).json()["closed_count"] == 0
    assert _todo(sending) == {}
    outbox = {t["content"]: t["status"] for t in _conv(sending)["outbox"]}
    assert outbox == {"稍等": "queued"}  # 失败的不再发，排队的照常发
    assert sending.post("/api/agent/claim", headers=AUTH, json={"agent_id": "pc1"}).json()["task"]["id"] == queued["id"]
