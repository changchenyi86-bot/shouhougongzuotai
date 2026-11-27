"""按群翻看完整聊天记录。"""

from datetime import datetime, timedelta, timezone

CUSTOMER = "wmAbCdEfGhIjKlMnOpQrStUvWxYz012345"
T0 = datetime(2026, 11, 19, 8, 0, tzinfo=timezone.utc)


def _msg(client, n, group="wrA", name="客户A群", sender=CUSTOMER, content=None, msg_type="text", minutes=None):
    r = client.post("/api/messages", json={
        "channel": "wecom", "external_id": f"{group}-{n}", "group_id": group, "group_name": name,
        "is_external_group": True, "sender_id": sender, "msg_type": msg_type,
        "content": content or f"第{n}条", "sent_at": (T0 + timedelta(minutes=n if minutes is None else minutes)).isoformat(),
    })
    assert r.status_code == 201
    return r.json()["id"]


def _conv(client, **params):
    r = client.get("/api/conversation", params={"channel": "wecom", "group_id": "wrA", **params})
    assert r.status_code == 200, r.text
    return r.json()


def _texts(page):
    return [i["message"]["content"] for i in page["items"]]


def test_full_history_with_staff_and_files(client):
    _msg(client, 1)
    _msg(client, 2, sender="zhangsan", content="员工回复")
    _msg(client, 3, msg_type="image", content="[图片]")
    page = _conv(client)
    assert _texts(page) == ["第1条", "员工回复", "[图片]"]  # 从早到晚，员工发言和图片都在
    assert page["items"][0]["suggestion"]["status"] == "ready"  # 客户消息带 AI 建议
    assert page["items"][1]["suggestion"] is None
    assert [i["message"]["from_customer"] for i in page["items"]] == [True, False, True]
    assert not page["has_more_before"]


def test_paging_older_and_newer(client):
    ids = [_msg(client, n) for n in range(1, 8)]
    latest = _conv(client, limit=3)
    assert _texts(latest) == ["第5条", "第6条", "第7条"] and latest["has_more_before"]
    older = _conv(client, limit=3, before=ids[4])
    assert _texts(older) == ["第2条", "第3条", "第4条"] and older["has_more_before"]
    oldest = _conv(client, limit=3, before=ids[1])
    assert _texts(oldest) == ["第1条"] and not oldest["has_more_before"]
    newer = _conv(client, limit=2, after=ids[1])
    assert _texts(newer) == ["第3条", "第4条"] and newer["has_more_after"]


def test_anchor_and_same_timestamp(client):
    ids = [_msg(client, n) for n in range(1, 10)]
    # 同一分钟内的多条消息也不会丢或重复
    same = [_msg(client, n, minutes=20) for n in (20, 21, 22)]
    page = _conv(client, anchor=ids[4], limit=4)
    assert _texts(page) == ["第3条", "第4条", "第5条", "第6条", "第7条"]
    assert page["has_more_before"] and page["has_more_after"]
    seen, cursor = [], ids[0]
    while True:
        p = _conv(client, limit=2, after=cursor)
        seen += [i["message"]["id"] for i in p["items"]]
        if not p["has_more_after"]:
            break
        cursor = seen[-1]
    assert set(seen) == set(ids[1:] + same)
    assert len(seen) == len(set(seen)) == 11


def test_anchor_from_other_group_404(client):
    other = _msg(client, 1, group="wrB")
    r = client.get("/api/conversation", params={"channel": "wecom", "group_id": "wrA", "anchor": other})
    assert r.status_code == 404


def test_groups_list(client):
    _msg(client, 1)
    _msg(client, 2, sender="zhangsan", content="员工回复")
    _msg(client, 5, group="wrB", name="客户B群")
    _msg(client, 6, group="wrC", name=None)
    groups = client.get("/api/groups").json()
    assert [g["group_id"] for g in groups] == ["wrC", "wrB", "wrA"]  # 最近有消息的在前
    a = groups[2]
    assert a["group_name"] == "客户A群" and a["message_count"] == 2 and a["pending_count"] == 1
    assert a["last_content"] == "员工回复"
    assert [g["group_id"] for g in client.get("/api/groups", params={"q": "b群"}).json()] == ["wrB"]


def test_set_group_name_when_channel_has_none(client):
    """外部群查不到群名时，技术支持在工作台里填写：已入库的消息改名，之后进来的消息也带上，自动发送能用它找群。"""
    from app import db, send_queue
    from app.models import Channel

    _msg(client, 1, group="wrX", name=None)
    groups = {g["group_id"]: g for g in client.get("/api/groups").json()}
    assert groups["wrX"]["group_name"] is None

    r = client.put("/api/groups/name", json={"channel": "wecom", "group_id": "wrX", "name": "  某客户-有数项目群 ",
                                             "operator": "小王"})
    assert r.status_code == 200, r.text
    assert r.json() == {"channel": "wecom", "group_id": "wrX", "name": "某客户-有数项目群", "duplicate": False}
    groups = {g["group_id"]: g for g in client.get("/api/groups").json()}
    assert groups["wrX"]["group_name"] == "某客户-有数项目群"

    _msg(client, 2, group="wrX", name=None)  # 之后进来、渠道仍查不到群名的消息
    assert all(i["message"]["group_name"] == "某客户-有数项目群"
               for i in _conv(client, group_id="wrX")["items"])
    with db.SessionLocal() as s:
        assert send_queue.target_group_name(s, Channel.WECOM, "wrX") == "某客户-有数项目群"

    # 和别的群同名时提醒（这两个群自动发送会被拒绝）
    _msg(client, 1, group="wrY", name=None)
    r = client.put("/api/groups/name", json={"channel": "wecom", "group_id": "wrY", "name": "某客户-有数项目群"})
    assert r.json()["duplicate"] is True


def test_set_group_name_validation(client):
    _msg(client, 1, group="wrX", name=None)
    assert client.put("/api/groups/name", json={"channel": "wecom", "group_id": "wrX", "name": "   "}).status_code == 422
    assert client.put("/api/groups/name", json={"channel": "wecom", "group_id": "nope", "name": "x"}).status_code == 404


def test_updates_version_changes_on_new_message_and_suggestion(client):
    v0 = client.get("/api/updates")
    assert v0.status_code == 200
    _msg(client, 1)
    v1 = client.get("/api/updates").json()["version"]
    assert v1 != v0.json()["version"]
    assert client.get("/api/updates").json()["version"] == v1  # 没有变化时版本号不变

    sug = _conv(client)["items"][0]["suggestion"]
    assert client.post(f"/api/suggestions/{sug['id']}/dismiss", json={}).status_code == 200
    assert client.get("/api/updates").json()["version"] != v1


def test_updates_requires_login(client):
    client.cookies.clear()
    assert client.get("/api/updates").status_code == 401
