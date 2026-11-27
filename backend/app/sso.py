"""公司单点登录：网易 OpenID Connect（login.netease.com）的授权码流程。

流程：浏览器跳到 /authorize 登录 → 带 code 回到工作台 → 后台拿 code 换 token → 校验 id_token → 取 userinfo。
id_token 默认 HS256 签名，密钥就是 client_secret（网易文档「1.5 应用进行 ID Token 校验」）。
地址只用配置里的 issuer 拼，不读请求、回调参数或 token 里的地址。
"""

import base64
import hashlib
import hmac
import json
import logging
import math
import re
import time
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit

import httpx

from app.config import settings

log = logging.getLogger(__name__)

STATE_TTL = 600  # 秒；网易的 code 也是 600 秒有效，留够输密码、将军令的时间
CLOCK_SKEW = 60
_transport: httpx.BaseTransport | None = None  # 测试时换成假的公司登录服务


class SsoError(Exception):
    """登录没成功。code 给登录页显示对应提示。"""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code


@dataclass(frozen=True)
class Identity:
    subject: str  # 统一小写的公司账号（邮箱前缀）
    fullname: str | None
    email: str | None


def _endpoint(name: str) -> str:
    return settings.sso_issuer.rstrip("/") + "/" + name


LOGIN_PATH = "/api/auth/sso/login"


def login_url() -> str:
    """登录页「用网易账号登录」按钮的地址：和登记的回调地址同一个网址（scheme://host:port）。

    从 127.0.0.1、局域网 IP 等别的地址打开工作台时，也要在回调回来的那个网址上发起登录，state Cookie 才对得上。
    """
    url = urlsplit(settings.sso_redirect_uri)
    return f"{url.scheme}://{url.netloc}{LOGIN_PATH}"


def authorize_url(state: str, nonce: str) -> str:
    return _endpoint("authorize") + "?" + urlencode({
        "response_type": "code",
        "scope": settings.sso_scope,
        "client_id": settings.sso_client_id,
        "redirect_uri": settings.sso_redirect_uri,
        "state": state,
        "nonce": nonce,
    })


def logout_url() -> str | None:
    """站点设了跳过二步确认时，退出工作台后要跳到这里退出公司登录。"""
    if not settings.sso_upstream_logout:
        return None
    return urlsplit(settings.sso_issuer)._replace(path="/logout/", query="", fragment="").geturl()


def _b64decode(part: str) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", part):
        raise ValueError("不是 base64url")
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def _no_duplicate_keys(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise ValueError("JSON 里有重复的键")
        out[k] = v
    return out


def _half_hash(value: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(value.encode()).digest()[:16]).decode().rstrip("=")


def validate_id_token(token: object, nonce: str, started_at: float, *, access_token: str = "", code: str = "",
                      now: float | None = None) -> dict:
    """校验签名、iss、aud、azp、exp、iat、nonce，返回 claims。任何一项不对都抛 SsoError。"""
    now = time.time() if now is None else now
    try:
        if not isinstance(token, str) or len(token) > 16384:
            raise ValueError("id_token 缺失或太长")
        head, body, sig = token.split(".")
        header = json.loads(_b64decode(head), object_pairs_hook=_no_duplicate_keys)
        if not isinstance(header, dict) or header.get("alg") != "HS256" or "crit" in header:
            # 站点要是登记成 RS256，需要另外实现公钥校验，不能跳过签名
            raise ValueError(f"签名方式不是 HS256：{header.get('alg') if isinstance(header, dict) else header}")
        expected = hmac.new(settings.sso_client_secret.encode(), f"{head}.{body}".encode("ascii"),
                            hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64decode(sig)):
            raise ValueError("签名不对")
        claims = json.loads(_b64decode(body), object_pairs_hook=_no_duplicate_keys)
        if not isinstance(claims, dict) or claims.get("iss") != settings.sso_issuer:
            raise ValueError("iss 不对")
        aud = claims.get("aud")
        aud = [aud] if isinstance(aud, str) else aud
        if not isinstance(aud, list) or settings.sso_client_id not in aud:
            raise ValueError("aud 不对")
        if (len(aud) > 1 or "azp" in claims) and claims.get("azp") != settings.sso_client_id:
            raise ValueError("azp 不对")
        sub = claims.get("sub")
        if not isinstance(sub, str) or not 1 <= len(sub) <= 255 or sub.strip() != sub:
            raise ValueError("sub 缺失")
        for k in ("exp", "iat"):
            if type(claims.get(k)) not in (int, float) or not math.isfinite(claims[k]):
                raise ValueError(f"{k} 不对")
        if claims["exp"] <= now or claims["iat"] > now + CLOCK_SKEW or claims["iat"] < started_at - CLOCK_SKEW:
            raise ValueError("token 过期或时间不对")
        if not isinstance(claims.get("nonce"), str) or not hmac.compare_digest(claims["nonce"].encode(), nonce.encode()):
            raise ValueError("nonce 不对")
        for claim, value in (("at_hash", access_token), ("c_hash", code)):
            if claim in claims and (not value or claims[claim] != _half_hash(value)):
                raise ValueError(f"{claim} 不对")
        return claims
    except (ValueError, TypeError, KeyError, UnicodeError) as exc:
        log.warning("公司登录 id_token 校验没通过：%s", exc)
        raise SsoError("failed", str(exc)) from None


def _json(resp: httpx.Response) -> dict:
    if resp.status_code != 200 or len(resp.content) > 1024 * 1024:
        raise ValueError(f"HTTP {resp.status_code}：{resp.text[:200]}")
    data = resp.json()
    if not isinstance(data, dict) or "error" in data:
        raise ValueError(f"返回出错：{str(data)[:200]}")
    return data


def _text(value: object, limit: int) -> str | None:
    return value.strip()[:limit] or None if isinstance(value, str) else None


def exchange(code: str, nonce: str, started_at: float) -> Identity:
    """用回调带回来的 code 换 token、校验 id_token、取用户信息。"""
    if not code or len(code) > 2048:
        raise SsoError("failed", "回调没带 code")
    with httpx.Client(timeout=settings.sso_timeout, follow_redirects=False, transport=_transport) as client:
        try:
            token = _json(client.post(
                _endpoint("token"),
                data={"grant_type": "authorization_code", "code": code, "redirect_uri": settings.sso_redirect_uri},
                auth=(settings.sso_client_id, settings.sso_client_secret),
                headers={"Accept": "application/json"},
            ))
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("公司登录换 token 失败：%s", exc)
            raise SsoError("failed", f"换 token 失败：{exc}") from None
        access = token.get("access_token")
        if not isinstance(access, str) or not access or len(access) > 16384 or not re.fullmatch(r"[\x21-\x7e]+", access):
            raise SsoError("failed", "token 返回里没有 access_token")
        claims = validate_id_token(token.get("id_token"), nonce, started_at, access_token=access, code=code)

        # 姓名、邮箱以 userinfo 为准；userinfo 一时取不到，凭已校验的 id_token 也能登录（只是认不出姓名）
        profile: dict = {}
        try:
            profile = _json(client.get(_endpoint("userinfo"), headers={"Authorization": f"Bearer {access}",
                                                                        "Accept": "application/json"}))
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("公司登录取用户信息失败，只用 id_token 登录：%s", exc)
        if profile and profile.get("sub") != claims["sub"]:
            raise SsoError("failed", "userinfo 和 id_token 不是同一个人")
    return Identity(
        subject=claims["sub"].lower(),
        fullname=_text(profile.get("fullname", claims.get("fullname")), 64),
        email=_text(profile.get("email", claims.get("email")), 255),
    )
