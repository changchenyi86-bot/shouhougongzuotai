"""会话分流和可见范围。"""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import delete

from app import db, routing
from app.main import app
from app.models import Conversation

CUSTOMER = "wmAbCdEfGhIjKlMnOpQrStUvWxYz012345"
T0 = datetime.now(timezone.utc) - timedelta(hours=1)
_n = iter(range(10_000))


def _msg(client, group, sender=CUSTOMER):
    n = next(_n)
    r = client.post("/api/messages", json={
        "channel": "wecom", "external_id": f"{group}-{n}", "group_id": group, "group_name": f"{group}群",
        "is_external_group": True, "sender_id": sender, "content": f"问题{n}",
        "sent_at": (T0 + timedelta(seconds=n)).isoformat(),
    })
    assert r.status_code == 201, r.text


def _teams(client):
    return {t["name"]: t["id"] for t in client.get("/api/teams").json()}


def _member(client, name, team, online=True):
    """加一个成员；online=True 时登录（登录了就算在线），返回 (成员 id, 这个成员的客户端)。"""
    username = f"u{next(_n)}"
    r = client.post("/api/staff", json={"name": name, "username": username, "password": "secret-123",
                                        "role": "support", "team_id": _teams(client)[team]})
    assert r.status_code == 201, r.text
    c = TestClient(app)
    if online:
        assert c.post("/api/auth/login", json={"username": username, "password": "secret-123"}).status_code == 200
    return r.json()["id"], c


def _owner(client, group, staff_id):
    _msg(client, group, sender="lisi")  # 员工先在群里说过话，工作台才知道有这个群（员工发言不开会话）
    r = client.put("/api/groups/owner", json={"channel": "wecom", "group_id": group, "owner_id": staff_id})
    assert r.status_code == 204, r.text


def _group(client, group):
    return next((g for g in client.get("/api/groups").json() if g["group_id"] == group), None)


def _assignee(client, group):
    g = _group(client, group)
    return g["assignee"]["name"] if g and g["assignee"] else None


def _end(c, group):
    return c.post("/api/conversations/end", json={"channel": "wecom", "group_id": group})


def test_owner_online_gets_it_and_others_cannot_see(client):
    jy, jy_c = _member(client, "姜瑜", "BI 组")
    sb, sb_c = _member(client, "宋奔", "BI 组")
    _owner(client, "wrA", jy)
    _msg(client, "wrA")
    assert _assignee(client, "wrA") == "姜瑜"
    g = _group(jy_c, "wrA")
    assert g["owner"]["name"] == "姜瑜" and g["pending_count"] == 1
    # 同组的人看不到，也打不开、不能操作
    assert _group(sb_c, "wrA") is None
    assert sb_c.get("/api/conversation", params={"channel": "wecom", "group_id": "wrA"}).status_code == 403
    assert _end(sb_c, "wrA").status_code == 403
    assert sb_c.get("/api/inbox", params={"view": "all"}).json() == []
    sid = jy_c.get("/api/inbox").json()[0]["suggestion"]["id"]
    assert sb_c.post(f"/api/suggestions/{sid}/dismiss", json={}).status_code == 403
    # 后来的消息进同一个会话
    _msg(client, "wrA")
    assert _group(jy_c, "wrA")["pending_count"] == 2


def test_owner_offline_goes_to_least_busy_online_teammate(client):
    owner, _ = _member(client, "姜瑜", "BI 组", online=False)
    busy, busy_c = _member(client, "宋奔", "BI 组")
    free, free_c = _member(client, "史明源", "BI 组")
    _member(client, "唐已杰", "中台组")  # 别的组在线也不分给他
    # 宋奔手上已经有一个会话
    _owner(client, "wrX", busy)
    _msg(client, "wrX")
    _owner(client, "wrA", owner)
    _msg(client, "wrA")
    assert _assignee(client, "wrA") == "史明源"
    assert _group(free_c, "wrA") is not None  # 替别人处理的群也能看到
    assert _group(busy_c, "wrA") is None


def test_away_counts_as_offline_and_nobody_online_falls_back_to_owner(client):
    owner, owner_c = _member(client, "姜瑜", "BI 组")
    mate, mate_c = _member(client, "宋奔", "BI 组")
    owner_c.put("/api/auth/me/status", json={"status": "away"})
    _owner(client, "wrA", owner)
    _msg(client, "wrA")
    assert _assignee(client, "wrA") == "宋奔"
    # 全组都不在线：还是给负责人，等负责人上线
    mate_c.put("/api/auth/me/status", json={"status": "away"})
    _owner(client, "wrB", owner)
    _msg(client, "wrB")
    assert _assignee(client, "wrB") == "姜瑜"
    assert _group(owner_c, "wrB")["pending_count"] == 1


def test_no_owner_waits_for_admin(client):
    jy, jy_c = _member(client, "姜瑜", "BI 组")
    _msg(client, "wrA")
    g = _group(client, "wrA")
    assert g["conversation_open"] and g["assignee"] is None and g["owner"] is None
    assert [x["group_id"] for x in client.get("/api/groups", params={"view": "unassigned"}).json()] == ["wrA"]
    assert _group(jy_c, "wrA") is None
    # 技术支持不能指定负责人
    assert jy_c.put("/api/groups/owner", json={"channel": "wecom", "group_id": "wrA", "owner_id": jy}).status_code == 403
    # 管理员指定负责人后，正在等的会话直接分过去
    _owner(client, "wrA", jy)
    assert _assignee(client, "wrA") == "姜瑜"
    assert client.get("/api/groups", params={"view": "unassigned"}).json() == []
    # 取消负责人：群还在，但以后的新会话又要等管理员
    client.put("/api/groups/owner", json={"channel": "wecom", "group_id": "wrA", "owner_id": None})
    assert _group(client, "wrA")["owner"] is None


def test_transfer(client):
    owner, _ = _member(client, "姜瑜", "BI 组", online=False)
    mate, mate_c = _member(client, "宋奔", "BI 组")
    other, other_c = _member(client, "唐已杰", "中台组")
    _owner(client, "wrA", owner)
    _msg(client, "wrA")
    assert _assignee(client, "wrA") == "宋奔"
    body = {"channel": "wecom", "group_id": "wrA"}
    # 不相干的人不能转
    assert other_c.post("/api/conversations/assign", json={**body, "staff_id": other}).status_code == 403
    # 正在处理的人转给别人后，自己就看不到了
    r = mate_c.post("/api/conversations/assign", json={**body, "staff_id": other})
    assert r.status_code == 200 and r.json()["name"] == "唐已杰"
    assert _group(mate_c, "wrA") is None and _group(other_c, "wrA") is not None
    # 管理员能转回给群负责人
    staff = {s["name"]: s for s in client.get("/api/staff").json()}
    assert staff["唐已杰"]["open_count"] == 1
    r = client.post("/api/conversations/assign", json={**body, "staff_id": owner})
    assert r.status_code == 200 and _assignee(client, "wrA") == "姜瑜"
    _end(client, "wrA")
    assert client.post("/api/conversations/assign", json={**body, "staff_id": mate}).status_code == 409


def test_end_then_new_session_routes_again(client):
    owner, owner_c = _member(client, "姜瑜", "BI 组")
    mate, mate_c = _member(client, "宋奔", "BI 组")
    _owner(client, "wrA", owner)
    owner_c.put("/api/auth/me/status", json={"status": "away"})
    _msg(client, "wrA")
    assert _assignee(client, "wrA") == "宋奔"
    # 全都回复完了也可以结束；结束后宋奔就看不到这个群了
    assert _end(mate_c, "wrA").status_code == 201
    assert _end(mate_c, "wrA").status_code == 403
    g = _group(client, "wrA")
    assert not g["conversation_open"] and g["assignee"] is None
    # 负责人回来了，客户再发消息：新会话给负责人
    owner_c.put("/api/auth/me/status", json={"status": "online"})
    _msg(client, "wrA")
    assert _assignee(client, "wrA") == "姜瑜"
    conv = owner_c.get("/api/conversation", params={"channel": "wecom", "group_id": "wrA"}).json()
    assert len(conv["ends"]) == 1 and conv["assignee"]["name"] == "姜瑜" and conv["owner"]["name"] == "姜瑜"


def test_deactivated_member_hands_back(client):
    owner, _ = _member(client, "姜瑜", "BI 组", online=False)
    mate, _ = _member(client, "宋奔", "BI 组")
    _owner(client, "wrA", owner)
    _msg(client, "wrA")
    assert _assignee(client, "wrA") == "宋奔"
    client.patch(f"/api/staff/{mate}", json={"active": False})
    assert _assignee(client, "wrA") == "姜瑜"  # 同组没人在线了，回到负责人
    client.patch(f"/api/staff/{owner}", json={"active": False})
    assert _assignee(client, "wrA") is None  # 负责人也停用：等管理员分配


def test_backfill_opens_sessions_for_old_pending(client):
    jy, _ = _member(client, "姜瑜", "BI 组")
    _owner(client, "wrA", jy)
    _msg(client, "wrA")
    _msg(client, "wrB")
    with db.SessionLocal() as s:  # 模拟分流上线前就在待处理里的群
        s.execute(delete(Conversation))
        s.commit()
        assert routing.backfill(s) == 2
        assert routing.backfill(s) == 0
    assert _assignee(client, "wrA") == "姜瑜"
    assert _group(client, "wrB")["conversation_open"]


def test_messages_api_is_admin_only(client):
    _, jy_c = _member(client, "姜瑜", "BI 组")
    assert jy_c.get("/api/messages").status_code == 403
    assert jy_c.post("/api/messages", json={}).status_code == 403
