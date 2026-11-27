"""查询企业微信群名称、是否外部群。结果在进程内缓存。"""

import logging
import threading
import time
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)

API = "https://qyapi.weixin.qq.com/cgi-bin"


@dataclass
class GroupInfo:
    name: str | None
    is_external: bool | None  # None 表示查不到，由调用方按发送人推断


class _TokenCache:
    def __init__(self, corp_id: str, secret: str):
        self.corp_id, self.secret = corp_id, secret
        self._token, self._expires = "", 0.0
        self._lock = threading.Lock()

    def get(self, client: httpx.Client) -> str:
        with self._lock:
            if time.time() < self._expires - 60:
                return self._token
            r = client.get(f"{API}/gettoken", params={"corpid": self.corp_id, "corpsecret": self.secret})
            data = r.json()
            if data.get("errcode"):
                raise RuntimeError(f"gettoken 失败：{data.get('errcode')} {data.get('errmsg')}")
            self._token, self._expires = data["access_token"], time.time() + data.get("expires_in", 7200)
            return self._token


class GroupDirectory:
    def __init__(self, corp_id: str, archive_secret: str, contact_secret: str = "",
                 client: httpx.Client | None = None, contact_proxy: str = "",
                 contact_client: httpx.Client | None = None):
        self._client = client or httpx.Client(timeout=10)
        # 客户联系接口可以单独走代理（经有可信 IP 的服务器出网）；会话存档接口仍然直连
        self._contact_client = contact_client or (
            httpx.Client(timeout=10, proxy=contact_proxy) if contact_proxy else self._client)
        self._archive = _TokenCache(corp_id, archive_secret) if archive_secret else None
        self._contact = _TokenCache(corp_id, contact_secret) if contact_secret else None
        self._cache: dict[str, tuple[GroupInfo, float]] = {}

    def lookup(self, room_id: str) -> GroupInfo:
        cached = self._cache.get(room_id)
        if cached and time.time() < cached[1]:
            return cached[0]
        info = self._lookup_external(room_id) or self._lookup_internal(room_id)
        # 查询成功缓存 1 天（群名可能改）；失败只缓存 10 分钟，之后重试
        ttl = 86400 if info else 600
        info = info or GroupInfo(None, None)
        self._cache[room_id] = (info, time.time() + ttl)
        return info

    def _post(self, tokens: _TokenCache, path: str, body: dict, client: httpx.Client | None = None) -> dict | None:
        client = client or self._client
        try:
            token = tokens.get(client)
            data = client.post(f"{API}/{path}", params={"access_token": token}, json=body).json()
        except Exception as exc:  # noqa: BLE001  查群名失败不影响消息入库
            log.warning("查询群信息失败 %s: %s", path, exc)
            return None
        if data.get("errcode"):
            log.warning("查询群信息失败 %s: %s %s", path, data.get("errcode"), data.get("errmsg"))
            return None
        return data

    def _lookup_external(self, room_id: str) -> GroupInfo | None:
        """外部客户群：需要「客户联系」Secret。"""
        if not self._contact:
            return None
        data = self._post(self._contact, "externalcontact/groupchat/get", {"chat_id": room_id, "need_name": 0},
                          self._contact_client)
        if data:
            return GroupInfo(data.get("group_chat", {}).get("name") or None, True)
        return None

    def _lookup_internal(self, room_id: str) -> GroupInfo | None:
        """内部群：用会话存档 Secret 查询。"""
        if not self._archive:
            return None
        data = self._post(self._archive, "msgaudit/groupchat/get", {"roomid": room_id})
        if data:
            return GroupInfo(data.get("roomname") or None, False)
        return None
