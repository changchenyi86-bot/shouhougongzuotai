"""自动发送队列：工作台确认发送 → 电脑端发送程序领取 → 回报结果。"""

from datetime import datetime, timedelta, timezone

import pytest

from app import db
from app.config import settings
from app.models import SendTask

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
GROUP = {
    "channel": "wecom",
    "group_id": "wrABC",
    "group_name": "某客户-有数项目群",
    "is_external_group": True,
    "sender_id": "wmAbCdEfGhIjKlMnOpQrStUvWxYz012345",  # wm 开头为外部客户
    "sender_name": "张三",
    "content": "报表导出一直转圈怎么办？",
}


@pytest.fixture()
def sending(client, monkeypatch):
    monkeypatch.setattr(settings, "send_agent_token", TOKEN)
    monkeypatch.setattr(settings, "send_min_interval_seconds", 0)
    return client


def _msg(client, n=1, **kw):
    sent_at = datetime.now(timezone.utc).isoformat()
    r = client.post("/api/messages", json={**GROUP, "external_id": f"m{n}", "sent_at": sent_at, **kw})
    assert r.status_code == 201
    items = client.get("/api/inbox", params={"view": "all"}).json()
    return next(i["suggestion"]["id"] for i in items if i["message"]["id"] == r.json()["id"])


def _online(client, ready=True, agent="pc1"):
    r = client.post("/api/agent/heartbeat", headers=AUTH, json={"agent_id": agent, "wecom_ready": ready})
    assert r.status_code == 200


def _adopt_send(client, sid, **kw):
    return client.post(f"/api/suggestions/{sid}/adopt", json={"send": True, "operator": "李四", **kw})


def _claim(client, agent="pc1"):
    r = client.post("/api/agent/claim", headers=AUTH, json={"agent_id": agent})
    assert r.status_code == 200
    return r.json()


def _report(client, task_id, ok, error=None, agent="pc1"):
    return client.post(f"/api/agent/tasks/{task_id}/result", headers=AUTH,
                       json={"agent_id": agent, "ok": ok, "error": error})


def _sug(client, sid, view="all"):
    return next(i["suggestion"] for i in client.get("/api/inbox", params={"view": view}).json()
                if i["suggestion"] and i["suggestion"]["id"] == sid)


def _in_view(client, sid, view):
    return any(i["suggestion"] and i["suggestion"]["id"] == sid
               for i in client.get("/api/inbox", params={"view": view}).json())


def test_full_send_flow(sending):
    _online(sending)
    status = sending.get("/api/send/status").json()
    assert status["enabled"] and status["ready"] and status["agents"][0]["agent_id"] == "pc1"

    sid = _msg(sending)
    r = _adopt_send(sending, sid, content="您好，请清理缓存后重试")
    assert r.status_code == 200 and r.json()["delivery"] == "agent"
    assert r.json()["suggestion"]["send_status"] == "queued"
    # 还没发出，留在待处理里
    assert _in_view(sending, sid, "todo") and not _in_view(sending, sid, "done")

    task = _claim(sending)["task"]
    assert task["group_name"] == "某客户-有数项目群" and task["content"] == "您好，请清理缓存后重试"
    assert _sug(sending, sid)["send_status"] == "sending"
    assert _claim(sending)["task"] is None  # 没有别的任务

    assert _report(sending, task["id"], True).json()["status"] == "sent"
    assert _report(sending, task["id"], True).status_code == 200  # 重复回报幂等
    sug = _sug(sending, sid)
    assert sug["send_status"] == "sent" and sug["sent_at"] and sug["status"] == "edited"
    assert _in_view(sending, sid, "done") and not _in_view(sending, sid, "todo")


def test_agent_auth(sending, monkeypatch):
    body = {"agent_id": "pc1"}
    assert sending.post("/api/agent/claim", json=body).status_code == 401
    assert sending.post("/api/agent/claim", json=body, headers={"Authorization": "Bearer wrong"}).status_code == 401
    monkeypatch.setattr(settings, "send_agent_token", "")
    assert sending.post("/api/agent/claim", json=body, headers=AUTH).status_code == 503


def test_rejected_when_offline_or_not_logged_in(sending):
    sid = _msg(sending)
    r = _adopt_send(sending, sid)
    assert r.status_code == 409 and "不在线" in r.json()["detail"]
    _online(sending, ready=False)
    assert _adopt_send(sending, sid).status_code == 409
    assert sending.get("/api/send/status").json()["ready"] is False
    # 被拒绝时建议保持未处理，可以改为复制发送
    assert _sug(sending, sid)["status"] == "ready"
    assert sending.post(f"/api/suggestions/{sid}/adopt", json={}).json()["delivery"] == "manual"


def test_rejected_when_disabled(client):
    sid = _msg(client)
    r = _adopt_send(client, sid)
    assert r.status_code == 409 and "未启用" in r.json()["detail"]


def test_unknown_or_duplicate_group_name(sending):
    _online(sending)
    sid = _msg(sending, group_id="wrNoName", group_name=None)
    r = _adopt_send(sending, sid)
    assert r.status_code == 409 and "名字" in r.json()["detail"]

    _msg(sending, n=2, group_id="wrOther")  # 另一个群同名
    sid = _msg(sending, n=3)
    r = _adopt_send(sending, sid)
    assert r.status_code == 409 and "不止一个群" in r.json()["detail"]


def test_only_wecom(sending):
    _online(sending)
    sid = _msg(sending, channel="feishu")
    assert "渠道" in _adopt_send(sending, sid).json()["detail"]


def test_group_hourly_limit(sending, monkeypatch):
    monkeypatch.setattr(settings, "send_group_max_per_hour", 2)
    _online(sending)
    for n in (1, 2):
        assert _adopt_send(sending, _msg(sending, n=n)).status_code == 200
    r = _adopt_send(sending, _msg(sending, n=3))
    assert r.status_code == 409 and "上限" in r.json()["detail"]


def test_min_interval_between_sends(sending, monkeypatch):
    monkeypatch.setattr(settings, "send_min_interval_seconds", 30)
    _online(sending)
    for n in (1, 2):
        _adopt_send(sending, _msg(sending, n=n))
    assert _claim(sending)["task"] is not None
    second = _claim(sending)
    assert second["task"] is None and 0 < second["retry_after"] <= 30


def test_failure_then_resend_or_manual(sending):
    _online(sending)
    sid = _msg(sending)
    _adopt_send(sending, sid)
    task = _claim(sending)["task"]
    _report(sending, task["id"], False, "没有找到群「某客户-有数项目群」，未发送")
    sug = _sug(sending, sid)
    assert sug["send_status"] == "failed" and "没有找到群" in sug["send_error"]
    assert _in_view(sending, sid, "todo")

    r = sending.post(f"/api/suggestions/{sid}/resend", json={"operator": "李四"})
    assert r.status_code == 200 and r.json()["send_status"] == "queued" and r.json()["send_error"] is None
    # 排队中不能重发、不能标记手动
    assert sending.post(f"/api/suggestions/{sid}/resend", json={}).status_code == 409
    assert sending.post(f"/api/suggestions/{sid}/mark-manual-sent").status_code == 409

    task2 = _claim(sending)["task"]
    assert task2["id"] != task["id"]
    _report(sending, task2["id"], False, "企业微信未登录")
    r = sending.post(f"/api/suggestions/{sid}/mark-manual-sent")
    assert r.json()["send_status"] == "manual" and r.json()["delivery"] == "manual"
    assert _in_view(sending, sid, "done")


def test_cancel_queued_only(sending):
    _online(sending)
    sid = _msg(sending)
    _adopt_send(sending, sid)
    assert sending.post(f"/api/suggestions/{sid}/cancel-send").json()["send_status"] == "cancelled"
    assert _claim(sending)["task"] is None  # 取消的不会被领取
    assert _in_view(sending, sid, "todo")

    sending.post(f"/api/suggestions/{sid}/resend", json={})
    _claim(sending)
    r = sending.post(f"/api/suggestions/{sid}/cancel-send")
    assert r.status_code == 409 and "无法撤回" in r.json()["detail"]


def test_report_by_other_agent_rejected(sending):
    _online(sending)
    _adopt_send(sending, _msg(sending))
    task = _claim(sending)["task"]
    assert _report(sending, task["id"], True, agent="pc2").status_code == 409
    assert _report(sending, "00000000-0000-0000-0000-000000000000", True).status_code == 404


def _shift(task_id, **delta):
    """把任务的时间往前拨，模拟时间流逝。"""
    import uuid

    with db.SessionLocal() as s:
        t = s.get(SendTask, uuid.UUID(task_id))
        t.created_at -= timedelta(**delta)
        if t.claimed_at:
            t.claimed_at -= timedelta(**delta)
        s.commit()


def test_stale_queued_task_expires(sending):
    _online(sending)
    sid = _msg(sending)
    _adopt_send(sending, sid)
    with db.SessionLocal() as s:
        task_id = str(s.query(SendTask).one().id)
    _shift(task_id, minutes=settings.send_queue_timeout_minutes + 1)
    assert _claim(sending)["task"] is None  # 过时的回复不再发出
    sug = _sug(sending, sid)
    assert sug["send_status"] == "failed" and "没有发出" in sug["send_error"]


def test_sending_timeout_marks_unknown_and_accepts_late_result(sending):
    _online(sending)
    sid = _msg(sending)
    _adopt_send(sending, sid)
    task = _claim(sending)["task"]
    _shift(task["id"], minutes=settings.send_result_timeout_minutes + 1)
    sug = _sug(sending, sid)
    assert sug["send_status"] == "failed" and "确认" in sug["send_error"]
    # 迟到的结果仍然接受
    assert _report(sending, task["id"], True).json()["status"] == "sent"
    assert _sug(sending, sid)["send_status"] == "sent"


def test_heartbeat_offline_after_timeout(sending, monkeypatch):
    _online(sending)
    monkeypatch.setattr(settings, "send_agent_offline_seconds", -1)
    assert sending.get("/api/send/status").json() == {"enabled": True, "ready": False, "agents": []}
