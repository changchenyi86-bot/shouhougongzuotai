"""会话工作台：附件上传、在会话里发文字/图片/文件、发送记录、下载客户发来的图片和文件。"""

import io
from datetime import datetime, timezone

import pytest

from app import db
from app.channels.wecom import media
from app.channels.wecom.archive import ArchiveSyncer, set_syncer
from app.config import settings
from app.models import Message
from tests.test_wecom import CUST, KEY, FakeArchive, FakeDirectory, _item, rec

TOKEN = "t"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 100


@pytest.fixture()
def ws(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "attachment_dir", str(tmp_path))
    monkeypatch.setattr(settings, "send_agent_token", TOKEN)
    monkeypatch.setattr(settings, "send_min_interval_seconds", 0)
    monkeypatch.setattr(media, "submit", media.fetch)
    client.post("/api/agent/heartbeat", headers=AUTH, json={"agent_id": "pc1", "wecom_ready": True})
    return client


def _customer_msg(client, n=1, content="报表导出一直转圈"):
    r = client.post("/api/messages", json={
        "channel": "wecom", "external_id": f"m{n}", "group_id": "wrA", "group_name": "客户A群",
        "is_external_group": True, "sender_id": CUST, "content": content,
        "sent_at": datetime.now(timezone.utc).isoformat()})
    return r.json()["id"]


def _upload(client, name="截图.png", data=PNG):
    r = client.post("/api/attachments", files={"file": (name, io.BytesIO(data))}, data={"operator": "李四"})
    assert r.status_code == 201, r.text
    return r.json()


def _conv(client):
    return client.get("/api/conversation", params={"channel": "wecom", "group_id": "wrA"}).json()


def test_upload_and_download(ws, monkeypatch):
    att = _upload(ws)
    assert att["mime"] == "image/png" and att["size"] == len(PNG) and att["filename"] == "截图.png"
    r = ws.get(f"/api/attachments/{att['id']}")
    assert r.content == PNG and r.headers["content-type"] == "image/png" and "inline" in r.headers["content-disposition"]
    doc = _upload(ws, "../../etc/日志.txt", b"hello")
    assert doc["filename"] == "日志.txt" and doc["mime"] == "text/plain"
    assert "attachment" in ws.get(f"/api/attachments/{doc['id']}").headers["content-disposition"]
    assert ws.post("/api/attachments", files={"file": ("空.txt", io.BytesIO(b""))}).status_code == 422
    monkeypatch.setattr(settings, "attachment_max_mb", 0)
    assert ws.post("/api/attachments", files={"file": ("大.txt", io.BytesIO(b"x"))}).status_code == 413
    assert ws.get("/api/attachments/00000000-0000-0000-0000-000000000000").status_code == 404


def test_send_text_and_files_without_suggestion(ws):
    _customer_msg(ws)
    img, doc = _upload(ws), _upload(ws, "说明.pdf", b"%PDF-1.4")
    r = ws.post("/api/conversations/send", json={"channel": "wecom", "group_id": "wrA", "content": "请看附件",
                                                  "attachment_ids": [img["id"], doc["id"]], "operator": "李四"})
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "queued" and [a["filename"] for a in r.json()["attachments"]] == ["截图.png", "说明.pdf"]
    # 发送记录显示在会话底部；排队中不需要人处理，不计入待处理
    assert [t["content"] for t in _conv(ws)["outbox"]] == ["请看附件"]
    assert ws.get("/api/groups").json()[0]["pending_count"] == 1

    task = ws.post("/api/agent/claim", headers=AUTH, json={"agent_id": "pc1"}).json()["task"]
    assert task["content"] == "请看附件" and [a["id"] for a in task["attachments"]] == [img["id"], doc["id"]]
    # 发送程序用口令下载附件
    assert ws.get(f"/api/agent/attachments/{img['id']}").status_code == 401
    assert ws.get(f"/api/agent/attachments/{img['id']}", headers=AUTH).content == PNG

    ws.post(f"/api/agent/tasks/{task['id']}/result", headers=AUTH, json={"agent_id": "pc1", "ok": True})
    assert _conv(ws)["outbox"][0]["status"] == "sent"  # 刚发出的仍显示一会儿
    assert ws.get("/api/groups").json()[0]["pending_count"] == 1


def test_cancelled_send_counts_as_pending(ws):
    _customer_msg(ws)
    t = ws.post("/api/conversations/send", json={"channel": "wecom", "group_id": "wrA", "content": "稍等"}).json()
    assert ws.post(f"/api/send-tasks/{t['id']}/cancel").status_code == 200
    assert ws.get("/api/groups").json()[0]["pending_count"] == 2  # 客户消息待回复 + 这条没发出去
    assert ws.post(f"/api/send-tasks/{t['id']}/mark-manual-sent").status_code == 200
    assert ws.get("/api/groups").json()[0]["pending_count"] == 1
    assert _conv(ws)["outbox"] == []


def test_send_only_attachment_and_validation(ws):
    _customer_msg(ws)
    img = _upload(ws)
    body = {"channel": "wecom", "group_id": "wrA"}
    assert ws.post("/api/conversations/send", json={**body, "attachment_ids": [img["id"]]}).status_code == 201
    assert ws.post("/api/conversations/send", json={**body, "content": "  "}).status_code == 422
    bad = {**body, "attachment_ids": ["00000000-0000-0000-0000-000000000000"]}
    assert ws.post("/api/conversations/send", json=bad).status_code == 422
    unknown_group = {"channel": "wecom", "group_id": "wrNone", "content": "x"}
    assert "名字" in ws.post("/api/conversations/send", json=unknown_group).json()["detail"]


def test_send_as_reply_adopts_suggestion(ws):
    mid = _customer_msg(ws)
    sug = _conv(ws)["items"][0]["suggestion"]
    r = ws.post("/api/conversations/send", json={"channel": "wecom", "group_id": "wrA", "content": "改过的回复",
                                                  "suggestion_id": sug["id"], "operator": "李四"})
    assert r.status_code == 201 and r.json()["suggestion_id"] == sug["id"]
    item = _conv(ws)["items"][0]
    assert item["message"]["id"] == mid
    s = item["suggestion"]
    assert s["status"] == "edited" and s["final_content"] == "改过的回复" and s["send_status"] == "queued"
    again = ws.post("/api/conversations/send", json={"channel": "wecom", "group_id": "wrA", "content": "x",
                                                      "suggestion_id": sug["id"]})
    assert again.status_code == 409
    # 回复别的群的消息不行
    _customer_msg(ws, 2)
    other = _conv(ws)["items"][1]["suggestion"]["id"]
    r = ws.post("/api/conversations/send", json={"channel": "wecom", "group_id": "wrB", "content": "x",
                                                  "suggestion_id": other})
    assert r.status_code == 404


def test_task_resend_cancel_dismiss(ws):
    _customer_msg(ws)
    r = ws.post("/api/conversations/send", json={"channel": "wecom", "group_id": "wrA", "content": "你好"})
    tid = r.json()["id"]
    assert ws.post(f"/api/send-tasks/{tid}/cancel").json()["status"] == "cancelled"
    new = ws.post(f"/api/send-tasks/{tid}/resend", json={}).json()
    assert new["status"] == "queued" and new["id"] != tid
    assert ws.post(f"/api/send-tasks/{tid}/resend", json={}).status_code == 409  # 已经重发过
    assert [t["id"] for t in _conv(ws)["outbox"]] == [new["id"]]  # 被接替的不再显示
    task = ws.post("/api/agent/claim", headers=AUTH, json={"agent_id": "pc1"}).json()["task"]
    ws.post(f"/api/agent/tasks/{task['id']}/result", headers=AUTH, json={"agent_id": "pc1", "ok": False, "error": "搜不到群"})
    assert _conv(ws)["outbox"][0]["error"] == "搜不到群"
    assert ws.post(f"/api/send-tasks/{new['id']}/mark-manual-sent").json()["status"] == "manual"
    assert _conv(ws)["outbox"] == []


def test_groups_views(ws):
    _customer_msg(ws)
    sid = _conv(ws)["items"][0]["suggestion"]["id"]
    assert [g["group_id"] for g in ws.get("/api/groups", params={"view": "todo"}).json()] == ["wrA"]
    assert ws.get("/api/groups", params={"view": "escalated"}).status_code == 422  # 转人工已去掉
    ws.post(f"/api/suggestions/{sid}/dismiss", json={})
    g = ws.get("/api/groups", params={"view": "all"}).json()[0]
    assert g["pending_count"] == 0 and "escalated_count" not in g
    assert ws.get("/api/groups", params={"view": "todo"}).json() == []


class MediaArchive(FakeArchive):
    def __init__(self, items, blobs):
        super().__init__(items)
        self.blobs = blobs

    def get_media(self, sdkfileid, max_bytes):
        if sdkfileid not in self.blobs:
            raise RuntimeError("媒体已过期")
        return self.blobs[sdkfileid]


def test_customer_images_and_files_downloaded(ws):
    items = [
        _item(1, rec("i1", CUST, msgtype="image", image={"sdkfileid": "F1", "filesize": len(PNG)})),
        _item(2, rec("f1", CUST, msgtype="file", file={"sdkfileid": "F2", "filename": "报错日志.txt", "filesize": 5})),
        _item(3, rec("i2", CUST, msgtype="image", image={"sdkfileid": "GONE", "filesize": 10})),
        _item(4, rec("f2", CUST, msgtype="file", file={"sdkfileid": "F3", "filename": "大.zip", "filesize": 10**9})),
    ]
    archive = MediaArchive(items, {"F1": PNG, "F2": b"error", "GONE2": b"x"})
    syncer = ArchiveSyncer(lambda: archive, {"1": KEY}, FakeDirectory())
    set_syncer(syncer)
    try:
        assert syncer.sync_once() == 4
        page = ws.get("/api/conversation", params={"channel": "wecom", "group_id": "wrROOM1"}).json()
        by = {i["message"]["external_id"]: i["message"] for i in page["items"]}
        assert by["i1"]["attachment"]["mime"] == "image/png" and by["i1"]["attachment"]["filename"].endswith(".png")
        assert ws.get(f"/api/attachments/{by['i1']['attachment']['id']}").content == PNG
        assert by["f1"]["attachment"]["filename"] == "报错日志.txt"
        assert by["i2"]["attachment"] is None and "媒体已过期" in by["i2"]["media_error"]
        assert by["f2"]["attachment"] is None and "MB" in by["f2"]["media_error"]
        # 重新下载
        archive.blobs["GONE"] = PNG
        r = ws.post(f"/api/messages/{by['i2']['id']}/fetch-media")
        assert r.status_code == 200 and r.json()["attachment"]["mime"] == "image/png" and r.json()["media_error"] is None
        text_msg = _customer_msg(ws)
        assert ws.post(f"/api/messages/{text_msg}/fetch-media").status_code == 422
    finally:
        set_syncer(None)
    with db.SessionLocal() as s:
        assert s.query(Message).filter(Message.attachment_id.is_not(None)).count() == 3
