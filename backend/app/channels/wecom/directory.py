"""查询企业微信群名称、是否外部群、群成员的名字。结果在进程内缓存。"""

import logging
import threading
import time
from dataclasses import dataclass, field

import httpx

log = logging.getLogger(__name__)

API = "https://qyapi.weixin.qq.com/cgi-bin"
MEMBER_RECHECK = 600  # 发消息的人不在缓存的成员名单里（刚进群）时，同一个群最多每 10 分钟重查一次


@dataclass
class GroupInfo:
    name: str | None
    is_external: bool | None  # None 表示查不到，由调用方按发送人推断
    # 成员 userid → 群里显示的名字（群昵称优先）。只有外部客户群查得到，内部群接口不给名字
    members: dict[str, str] = field(default_factory=dict)


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
        self._cache: dict[str, tuple[GroupInfo, float, float]] = {}  # 群 → (信息, 过期时间, 查询时间)

    def lookup(self, room_id: str, member: str | None = None, max_age: float | None = None) -> GroupInfo:
        """member：发这条消息的人。缓存的成员名单里没有他（刚进群）时重查一次成员名单。
        max_age：缓存超过这么多秒就重查（工作台 @ 人时要新一些的名单）。"""
        now = time.time()
        cached = self._cache.get(room_id)
        if cached:
            info, expires, fetched = cached
            new_member = bool(member and info.members and member not in info.members)
            stale = max_age is not None and now - fetched >= max_age
            if now < expires and not (new_member and now - fetched >= MEMBER_RECHECK) and not stale:
                return info
        info = self._lookup_external(room_id) or self._lookup_internal(room_id)
        # 查询成功缓存 1 天（群名、成员可能变）；失败只缓存 10 分钟，之后重试，期间沿用上次查到的
        ttl = 86400 if info else 600
        info = info or (cached[0] if cached else GroupInfo(None, None))
        self._cache[room_id] = (info, now + ttl, now)
        return info

    def members(self, room_id: str) -> dict[str, str]:
        """群成员 userid → 群里显示的名字。缓存超过 10 分钟就重查，刚进群的人也能 @ 到。"""
        return self.lookup(room_id, max_age=MEMBER_RECHECK).members

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
        """外部客户群：需要「客户联系」Secret。need_name=1 时成员名单带每个人的名字。"""
        if not self._contact:
            return None
        data = self._post(self._contact, "externalcontact/groupchat/get", {"chat_id": room_id, "need_name": 1},
                          self._contact_client)
        if not data:
            return None
        chat = data.get("group_chat", {})
        members = {}
        for m in chat.get("member_list", []):
            # 群昵称（如「国投证券_朱燕军」）比微信名更好认，和企业微信群里显示的一致
            name = (m.get("group_nickname") or m.get("name") or "").strip()
            if m.get("userid") and name:
                members[m["userid"]] = name[:128]
        return GroupInfo(chat.get("name") or None, True, members)

    def _lookup_internal(self, room_id: str) -> GroupInfo | None:
        """内部群：用会话存档 Secret 查询。"""
        if not self._archive:
            return None
        data = self._post(self._archive, "msgaudit/groupchat/get", {"roomid": room_id})
        if data:
            return GroupInfo(data.get("roomname") or None, False)
        return None
