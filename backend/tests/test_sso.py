"""公司单点登录（网易 OpenID Connect）：用假的公司登录服务走完整个流程。"""

import base64
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from app import sso
from app.config import settings
from app.main import app

CLIENT_ID = "wb-client-id"
SECRET = "wb-client-secret-0123456789"
REDIRECT = "http://testserver/api/auth/sso/callback"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def make_token(claims: dict, secret: str = SECRET, alg: str = "HS256") -> str:
    head = _b64(json.dumps({"alg": alg, "typ": "JWT"}).encode())
    body = _b64(json.dumps(claims).encode())
    sig = _b64(hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest())
    return f"{head}.{body}.{sig}"


class FakeProvider:
    """假的 login.netease.com：记住授权请求里的 nonce，换 token 时签进 id_token。"""

    def __init__(self):
        self.sub = "yiyongjie"
        self.fullname = "易永杰"
        self.nonce = ""
        self.claims: dict = {}  # 额外覆盖 id_token 里的字段
        self.secret = SECRET
        self.userinfo_status = 200
        self.userinfo_sub: str | None = None
        self.token_requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/connect/token":
            self.token_requests.append(request)
            now = int(time.time())
            claims = {"iss": "https://login.netease.com/connect", "aud": CLIENT_ID, "sub": self.sub,
                      "iat": now, "exp": now + 600, "nonce": self.nonce, **self.claims}
            return httpx.Response(200, json={"access_token": "at-123", "token_type": "Bearer", "expires_in": 3600,
                                             "id_token": make_token(claims, self.secret)})
        if request.url.path == "/connect/userinfo":
            assert request.headers["authorization"] == "Bearer at-123"
            return httpx.Response(self.userinfo_status, json={
                "sub": self.userinfo_sub or self.sub, "fullname": self.fullname,
                "email": f"{self.sub}@corp.netease.com"})
        return httpx.Response(404)


@pytest.fixture()
def op(monkeypatch):
    fake = FakeProvider()
    monkeypatch.setattr(settings, "sso_client_id", CLIENT_ID)
    monkeypatch.setattr(settings, "sso_client_secret", SECRET)
    monkeypatch.setattr(settings, "sso_redirect_uri", REDIRECT)
    monkeypatch.setattr(sso, "_transport", httpx.MockTransport(fake.handler))
    return fake


def _add(client, name, username, role="support"):
    r = client.post("/api/staff", json={"name": name, "username": username, "password": "secret-123", "role": role})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _start(c: TestClient, op: FakeProvider) -> str:
    """点「用网易账号登录」，返回 state。"""
    r = c.get("/api/auth/sso/login", follow_redirects=False)
    assert r.status_code == 302
    url = urlsplit(r.headers["location"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == "https://login.netease.com/connect/authorize"
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert q["client_id"] == CLIENT_ID and q["redirect_uri"] == REDIRECT and q["response_type"] == "code"
    assert q["scope"].split() == ["openid", "nickname", "fullname", "email"]
    op.nonce = q["nonce"]
    return q["state"]


def _sso_login(op: FakeProvider, c: TestClient | None = None):
    """走一遍公司登录，返回（浏览器, 回调的响应）。"""
    c = c or TestClient(app)
    state = _start(c, op)
    r = c.get("/api/auth/sso/callback", params={"code": "code-1", "state": state}, follow_redirects=False)
    return c, r


def _error(r) -> dict:
    assert r.status_code == 302, r.text
    url = urlsplit(r.headers["location"])
    assert url.path == "/"
    return {k: v[0] for k, v in parse_qs(url.query).items()}


def test_off_until_configured(client):
    assert client.get("/api/auth/state").json()["sso_enabled"] is False
    r = TestClient(app).get("/api/auth/sso/login", follow_redirects=False)
    assert _error(r) == {"sso_error": "off"}


def test_auto_bind_when_account_and_name_match(client, op):
    sid = _add(client, "易永杰", "YiYongJie")
    assert client.get("/api/auth/state").json()["sso_enabled"] is True

    c, r = _sso_login(op)
    assert r.status_code == 302 and r.headers["location"] == "/"
    me = c.get("/api/auth/me").json()
    assert me["id"] == sid and me["sso_subject"] == "yiyongjie"
    # 换 token 用的是 Basic 认证、登记的回调地址
    req = op.token_requests[0]
    assert req.headers["authorization"] == "Basic " + base64.b64encode(f"{CLIENT_ID}:{SECRET}".encode()).decode()
    assert parse_qs(req.content.decode())["redirect_uri"] == [REDIRECT]
    # 团队设置里显示绑定的公司账号；第二次登录直接进
    assert {s["name"]: s["sso_subject"] for s in client.get("/api/staff").json()}["易永杰"] == "yiyongjie"
    assert _sso_login(op)[1].headers["location"] == "/"


def test_unbound_account_waits_for_admin(client, op):
    sid = _add(client, "张三", "zhangsan_bi")  # 账号不一样，不自动认
    op.sub, op.fullname = "zhangsan", "张三"
    c, r = _sso_login(op)
    assert _error(r) == {"sso_error": "unbound", "account": "zhangsan"}
    assert c.get("/api/auth/me").status_code == 401

    rows = client.get("/api/sso/accounts").json()
    assert len(rows) == 1
    row = rows[0]
    assert row["subject"] == "zhangsan" and row["fullname"] == "张三" and row["email"] == "zhangsan@corp.netease.com"
    assert row["staff_id"] is None and row["suggested_staff_id"] == sid  # 姓名一样，推荐给管理员

    r = client.put("/api/sso/accounts/zhangsan", json={"staff_id": sid})
    assert r.status_code == 200 and r.json()["staff_name"] == "张三"
    c, r = _sso_login(op)
    assert r.headers["location"] == "/" and c.get("/api/auth/me").json()["id"] == sid


def test_name_only_or_account_only_is_not_enough(client, op):
    _add(client, "易永杰", "someone_else")
    _add(client, "别人", "yiyongjie")
    assert _error(_sso_login(op)[1])["sso_error"] == "unbound"


def test_state_is_one_time_and_bound_to_browser(client, op):
    _add(client, "易永杰", "yiyongjie")
    c = TestClient(app)
    state = _start(c, op)
    # 别的浏览器拿着这个 state 回调不行
    r = TestClient(app).get("/api/auth/sso/callback", params={"code": "x", "state": state}, follow_redirects=False)
    assert _error(r)["sso_error"] == "expired"
    ok = c.get("/api/auth/sso/callback", params={"code": "x", "state": state}, follow_redirects=False)
    assert ok.headers["location"] == "/"
    # 同一个 state 再用一次不行
    c.cookies.set("wb_sso_state", state, path="/api/auth/sso")
    again = c.get("/api/auth/sso/callback", params={"code": "x", "state": state}, follow_redirects=False)
    assert _error(again)["sso_error"] == "expired"


@pytest.mark.parametrize("tamper", [
    {"secret": "wrong-secret-0123456789"},
    {"claims": {"aud": "other-app"}},
    {"claims": {"iss": "https://evil.example.com/connect"}},
    {"claims": {"nonce": "replayed"}},
    {"claims": {"exp": int(time.time()) - 10}},
    {"claims": {"at_hash": "not-the-hash"}},
    {"userinfo_sub": "someone-else"},
])
def test_rejects_bad_tokens(client, op, tamper):
    _add(client, "易永杰", "yiyongjie")
    op.secret = tamper.get("secret", SECRET)
    op.claims = tamper.get("claims", {})
    op.userinfo_sub = tamper.get("userinfo_sub")
    c, r = _sso_login(op)
    assert _error(r)["sso_error"] == "failed"
    assert c.get("/api/auth/me").status_code == 401


def test_userinfo_down_still_logs_in_bound_account(client, op):
    sid = _add(client, "易永杰", "yiyongjie")
    assert _sso_login(op)[1].headers["location"] == "/"  # 先自动绑上
    op.userinfo_status = 500
    c, r = _sso_login(op)
    assert r.headers["location"] == "/" and c.get("/api/auth/me").json()["id"] == sid


def test_denied_and_disabled(client, op):
    sid = _add(client, "易永杰", "yiyongjie")
    c = TestClient(app)
    _start(c, op)
    r = c.get("/api/auth/sso/callback", params={"error": "access_denied"}, follow_redirects=False)
    assert _error(r)["sso_error"] == "denied"

    client.put("/api/sso/accounts/yiyongjie", json={"staff_id": sid})
    client.patch(f"/api/staff/{sid}", json={"active": False})
    assert _error(_sso_login(op)[1])["sso_error"] == "disabled"


def test_login_button_points_at_registered_address(client, op, monkeypatch):
    monkeypatch.setattr(settings, "sso_redirect_uri", "http://localhost:5173/api/auth/sso/callback")
    state = client.get("/api/auth/state").json()
    assert state["sso_login_url"] == "http://localhost:5173/api/auth/sso/login" and state["sso_logout_url"] is None
    monkeypatch.setattr(settings, "sso_upstream_logout", True)
    assert client.get("/api/auth/state").json()["sso_logout_url"] == "https://login.netease.com/logout/"


def test_binding_rules(client, op):
    a = _add(client, "姜瑜", "jiangyu")
    b = _add(client, "宋奔", "songben")
    assert client.put("/api/sso/accounts/JiangYu", json={"staff_id": a}).json()["subject"] == "jiangyu"
    assert client.put("/api/sso/accounts/bad name", json={"staff_id": a}).status_code == 422
    # 已经绑给姜瑜的账号不能再绑给宋奔
    assert client.put("/api/sso/accounts/jiangyu", json={"staff_id": b}).status_code == 409
    # 给姜瑜换一个账号：旧账号变成没绑定
    client.put("/api/sso/accounts/wb.jiangyu", json={"staff_id": a})
    rows = {r["subject"]: r["staff_id"] for r in client.get("/api/sso/accounts").json()}
    assert rows == {"wb.jiangyu": a, "jiangyu": None}
    assert {s["name"]: s["sso_subject"] for s in client.get("/api/staff").json()}["姜瑜"] == "wb.jiangyu"
    # 绑着的不能直接忽略；解除后可以
    assert client.delete("/api/sso/accounts/wb.jiangyu").status_code == 409
    assert client.delete("/api/sso/accounts/jiangyu").status_code == 204
    assert [r["subject"] for r in client.get("/api/sso/accounts").json()] == ["wb.jiangyu"]


def test_unbinding_signs_that_member_out(client, op):
    sid = _add(client, "易永杰", "yiyongjie")
    c, _ = _sso_login(op)
    assert c.get("/api/auth/me").status_code == 200
    assert client.put("/api/sso/accounts/yiyongjie", json={"staff_id": None}).json()["staff_id"] is None
    assert c.get("/api/auth/me").status_code == 401  # 可能之前绑错了人，已有的登录作废
    assert client.get("/api/auth/me").status_code == 200  # 操作的管理员自己不受影响
    assert sid


def test_support_cannot_manage_bindings(client, op):
    _add(client, "姜瑜", "jiangyu")
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"username": "jiangyu", "password": "secret-123"}).status_code == 200
    assert c.get("/api/sso/accounts").status_code == 403
    assert c.put("/api/sso/accounts/jiangyu", json={"staff_id": None}).status_code == 403


def test_validate_rejects_other_algorithms(op):
    now = time.time()
    token = make_token({"iss": settings.sso_issuer, "aud": CLIENT_ID, "sub": "a", "iat": now, "exp": now + 60,
                        "nonce": "n"}, alg="none")
    with pytest.raises(sso.SsoError):
        sso.validate_id_token(token, "n", now)
    good = make_token({"iss": settings.sso_issuer, "aud": [CLIENT_ID], "azp": CLIENT_ID, "sub": "a", "iat": now,
                       "exp": now + 60, "nonce": "n"})
    assert sso.validate_id_token(good, "n", now)["sub"] == "a"
    # 发起登录之前签发的 token（被拿来重放）不认
    with pytest.raises(sso.SsoError):
        sso.validate_id_token(good, "n", now + 3600)
