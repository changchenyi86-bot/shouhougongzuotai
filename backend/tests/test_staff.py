"""登录、成员和分组。"""

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.main import app


def _anon():
    """不带登录 Cookie 的客户端（和 client 共用同一个库）。"""
    return TestClient(app)


def _login(username, password):
    c = _anon()
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    return c, r


def _teams(client):
    return {t["name"]: t["id"] for t in client.get("/api/teams").json()}


def _add(client, name, username, role="support", team=None, password="secret-123"):
    body = {"name": name, "username": username, "password": password, "role": role}
    if team:
        body["team_id"] = _teams(client)[team]
    return client.post("/api/staff", json=body)


def test_setup_once_and_login(client):
    # client 已经设过管理员（李四）
    assert client.get("/api/auth/state").json()["needs_setup"] is False
    r = client.post("/api/auth/setup", json={"name": "王五", "username": "ww", "password": "password123"})
    assert r.status_code == 409
    me = client.get("/api/auth/me").json()
    assert me["name"] == "李四" and me["role"] == "admin" and me["online"]

    assert _login("lisi", "wrong-pass")[1].status_code == 401
    assert _login("nobody", "password123")[1].status_code == 401
    c, r = _login("lisi", "password123")
    assert r.status_code == 200 and c.get("/api/auth/me").status_code == 200

    assert c.post("/api/auth/logout").status_code == 204
    assert c.get("/api/auth/me").status_code == 401


def test_workbench_needs_login_but_agent_and_webhooks_do_not(client):
    anon = _anon()
    for method, url in [("get", "/api/groups"), ("get", "/api/messages"), ("get", "/api/send/status"),
                        ("post", "/api/conversations/end"), ("get", "/api/staff"),
                        ("get", "/api/attachments/00000000-0000-0000-0000-000000000000")]:
        assert getattr(anon, method)(url).status_code == 401, url
    assert anon.get("/api/health").status_code == 200
    assert anon.get("/api/auth/state").status_code == 200
    # 发送程序用自己的口令，不受登录影响
    assert anon.post("/api/agent/claim", json={"agent_id": "pc1"}).status_code in (401, 503)
    assert anon.post("/webhooks/dingtalk", json={}).status_code == 501


def test_default_teams_and_members(client):
    assert list(_teams(client)) == ["BI 组", "中台组"]
    r = _add(client, "姜瑜", "jiangyu", team="BI 组")
    assert r.status_code == 201 and r.json()["team"]["name"] == "BI 组" and r.json()["role"] == "support"
    assert not r.json()["online"]  # 还没登录过
    assert _add(client, "姜瑜", "jy2").status_code == 409  # 姓名重复
    assert _add(client, "别人", "jiangyu").status_code == 409  # 账号重复
    assert _add(client, "短密码", "short", password="1234567").status_code == 422

    c, r = _login("jiangyu", "secret-123")
    assert r.status_code == 200 and r.json()["online"]
    # 技术支持不能管成员
    assert _add(c, "宋奔", "songben").status_code == 403
    sid = r.json()["id"]
    assert c.patch(f"/api/staff/{sid}", json={"role": "admin"}).status_code == 403
    # 但能看成员名单（转给同事时用）
    assert [s["name"] for s in c.get("/api/staff").json()] == ["李四", "姜瑜"]


def test_edit_member(client):
    sid = _add(client, "宋奔", "songben", team="BI 组").json()["id"]
    zt = _teams(client)["中台组"]
    r = client.patch(f"/api/staff/{sid}", json={"team_id": zt, "role": "admin"})
    assert r.json()["team"]["name"] == "中台组" and r.json()["role"] == "admin"
    assert client.patch(f"/api/staff/{sid}", json={"clear_team": True}).json()["team"] is None

    client.patch(f"/api/staff/{sid}", json={"password": "new-secret-1"})
    assert _login("songben", "secret-123")[1].status_code == 401
    c, r = _login("songben", "new-secret-1")
    assert r.status_code == 200

    # 停用后登录不了，已经登录的也立刻失效
    client.patch(f"/api/staff/{sid}", json={"active": False})
    assert c.get("/api/auth/me").status_code == 401
    assert _login("songben", "new-secret-1")[1].status_code == 403


def test_imported_member_without_password(client):
    """批量导入的成员没有密码：登录不了，管理员设了密码以后才能登录。"""
    from app import db
    from app.models import Staff

    with db.SessionLocal() as s:
        s.add(Staff(name="王博", username="王博", password_hash=""))
        s.commit()
    row = next(x for x in client.get("/api/staff").json() if x["name"] == "王博")
    assert row["has_password"] is False
    assert _login("王博", "")[1].status_code == 422
    assert _login("王博", "anything")[1].status_code == 401
    client.patch(f"/api/staff/{row['id']}", json={"password": "wb-pass-123"})
    assert _login("王博", "wb-pass-123")[1].status_code == 200
    assert next(x for x in client.get("/api/staff").json() if x["name"] == "王博")["has_password"]


def test_keep_one_admin(client):
    me = client.get("/api/auth/me").json()["id"]
    assert client.patch(f"/api/staff/{me}", json={"role": "support"}).status_code == 409
    assert client.patch(f"/api/staff/{me}", json={"active": False}).status_code == 409
    _add(client, "王五", "wangwu", role="admin")
    assert client.patch(f"/api/staff/{me}", json={"role": "support"}).status_code == 200


def test_status_and_password(client):
    r = client.put("/api/auth/me/status", json={"status": "away"})
    assert r.json()["status"] == "away" and not r.json()["online"]
    assert client.put("/api/auth/me/status", json={"status": "online"}).json()["online"]

    assert client.put("/api/auth/me/password", json={"old_password": "x", "new_password": "another-1"}).status_code == 422
    assert client.put("/api/auth/me/password",
                      json={"old_password": "password123", "new_password": "another-1"}).status_code == 204
    assert _login("lisi", "another-1")[1].status_code == 200


def test_actions_recorded_under_login_name(client):
    tyj = _add(client, "唐已杰", "tyj").json()["id"]
    c, _ = _login("tyj", "secret-123")
    r = client.post("/api/messages", json={
        "channel": "wecom", "external_id": "x1", "group_id": "wrA", "group_name": "A群", "is_external_group": True,
        "sender_id": "wmAbCdEfGhIjKlMnOpQrStUvWxYz012345", "content": "导出报错",
        "sent_at": datetime.now(timezone.utc).isoformat(),
    })
    client.put("/api/groups/owner", json={"channel": "wecom", "group_id": "wrA", "owner_id": tyj})
    sid = next(i["suggestion"]["id"] for i in c.get("/api/inbox", params={"view": "all"}).json()
               if i["message"]["id"] == r.json()["id"])
    # 请求里写的处理人不算数，一律记登录的人
    r = c.post(f"/api/suggestions/{sid}/dismiss", json={"operator": "冒充的人"})
    assert r.json()["handled_by"] == "唐已杰"
