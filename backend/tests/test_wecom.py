import base64
import json
import os

import pytest
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app import channels, db
from app.channels import wecom
from app.channels.wecom.archive import ArchiveSyncer, to_message_in
from app.channels.wecom.crypto import WeComCrypto, WeComCryptoError
from app.channels.wecom.directory import GroupInfo
from app.channels.wecom.sdk import ArchiveError
from app.config import settings
from app.models import Message, Suggestion, SyncCursor

AES_KEY = base64.b64encode(os.urandom(32)).decode().rstrip("=")
CORP = "ww_test_corp"
CUST = "wmAbCdEfGhIjKlMnOpQrStUvWxYz0123"  # 客户 external_userid


# ---------- 回调加解密 ----------

def test_crypto_roundtrip_and_signature():
    c = WeComCrypto("tok", AES_KEY, CORP)
    enc = c.encrypt("<xml>你好</xml>")
    assert c.decrypt(enc) == "<xml>你好</xml>"
    sig = c.signature("1", "n", enc)
    c.verify(sig, "1", "n", enc)
    with pytest.raises(WeComCryptoError):
        c.verify("bad", "1", "n", enc)
    with pytest.raises(WeComCryptoError):
        WeComCrypto("tok", AES_KEY, "other_corp").decrypt(enc)


@pytest.fixture()
def callback_conf(monkeypatch):
    monkeypatch.setattr(settings, "wecom_callback_token", "tok")
    monkeypatch.setattr(settings, "wecom_callback_aes_key", AES_KEY)
    monkeypatch.setattr(settings, "wecom_corp_id", CORP)
    return WeComCrypto("tok", AES_KEY, CORP)


def test_url_verification(client, callback_conf):
    echo = callback_conf.encrypt("12345")
    params = {"msg_signature": callback_conf.signature("1", "n", echo), "timestamp": "1", "nonce": "n",
              "echostr": echo}
    r = client.get("/webhooks/wecom", params=params)
    assert r.status_code == 200 and r.text == "12345"
    assert client.get("/webhooks/wecom", params={**params, "msg_signature": "x"}).status_code == 403


def test_msgaudit_notify_triggers_sync(client, callback_conf, monkeypatch):
    triggered = []
    monkeypatch.setattr(wecom, "trigger_sync", lambda: triggered.append(1))
    enc = callback_conf.encrypt("<xml><MsgType>event</MsgType><Event>msgaudit_notify</Event></xml>")
    body = f"<xml><ToUserName>{CORP}</ToUserName><Encrypt>{enc}</Encrypt></xml>"
    q = {"msg_signature": callback_conf.signature("1", "n", enc), "timestamp": "1", "nonce": "n"}
    r = client.post("/webhooks/wecom", params=q, content=body)
    assert r.status_code == 200 and r.text == "success" and triggered == [1]
    assert client.post("/webhooks/wecom", params={**q, "nonce": "x"}, content=body).status_code == 403


def test_callback_not_configured(client):
    assert client.post("/webhooks/wecom", content="<xml/>").status_code == 503


# ---------- 会话存档拉取 ----------

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _item(seq: int, record: dict, ver: int = 1) -> dict:
    random_key = f"rk{seq}"
    enc_key = base64.b64encode(KEY.public_key().encrypt(random_key.encode(), padding.PKCS1v15())).decode()
    return {"seq": seq, "msgid": record["msgid"], "publickey_ver": ver, "encrypt_random_key": enc_key,
            "encrypt_chat_msg": json.dumps({"k": random_key, "r": record})}


class FakeArchive:
    def __init__(self, items):
        self.items = items
        self.calls = []

    def get_chat_data(self, seq, limit):
        self.calls.append(seq)
        return [i for i in self.items if i["seq"] > seq][:limit]

    def decrypt(self, random_key, encrypt_chat_msg):
        data = json.loads(encrypt_chat_msg)
        assert data["k"] == random_key  # RSA 解出的随机密钥正确
        return json.dumps(data["r"])


def rec(msgid, frm, content="", room="wrROOM1", action="send", msgtype="text", ts=1763539200000, **extra):
    r = {"msgid": msgid, "action": action, "from": frm, "tolist": [], "roomid": room, "msgtime": ts,
         "msgtype": msgtype, **extra}
    if msgtype == "text":
        r["text"] = {"content": content}
    return r


class FakeDirectory:
    def lookup(self, room_id, member=None):
        return GroupInfo("某客户-有数项目群", True, {CUST: "王工", "zhangsan": "张三"})


def test_archive_sync(client, fake_llm):
    items = [
        _item(1, rec("m1", CUST, "报表导出一直转圈怎么办？")),
        _item(2, rec("m2", "zhangsan", "我看一下", ts=1763539260000)),
        _item(3, rec("m3", CUST, "单聊消息", room="")),  # 单聊，跳过
        _item(4, rec("m4", CUST, action="recall")),  # 撤回，跳过
        _item(5, rec("m5", CUST, msgtype="image", image={"md5sum": "x"}, ts=1763539320000)),
    ]
    archive = FakeArchive(items)
    syncer = ArchiveSyncer(lambda: archive, {"1": KEY}, FakeDirectory(), batch_size=2)

    assert syncer.sync_once() == 3
    with db.SessionLocal() as s:
        msgs = {m.external_id: m for m in s.query(Message).all()}
        assert set(msgs) == {"m1", "m2", "m5"}
        m1 = msgs["m1"]
        assert m1.group_name == "某客户-有数项目群" and m1.is_external_group and m1.content == "报表导出一直转圈怎么办？"
        assert m1.sender_name == "王工" and msgs["m2"].sender_name == "张三"  # 群成员名单里的名字
        assert msgs["m5"].content == "[图片]"
        # 只有客户的文本消息生成建议
        sugs = s.query(Suggestion).all()
        assert [s.message_id for s in sugs] == [m1.id]
        assert s.get(SyncCursor, "wecom_archive_seq").value == "5"

    # 员工发言作为上下文出现在提示里
    assert "客户 王工：报表导出一直转圈怎么办？" in fake_llm.calls[0][1]

    # 再次拉取从 seq=5 开始，没有新消息
    archive.calls.clear()
    assert syncer.sync_once() == 0
    assert archive.calls == [5]


def test_archive_missing_key_stops_without_advancing(client):
    archive = FakeArchive([_item(1, rec("m1", CUST, "hi")), _item(2, rec("m2", CUST, "hi"), ver=2)])
    syncer = ArchiveSyncer(lambda: archive, {"1": KEY}, batch_size=10)
    with pytest.raises(ArchiveError):
        syncer.sync_once()
    with db.SessionLocal() as s:
        assert s.get(SyncCursor, "wecom_archive_seq").value == "1"


def test_archive_skips_versions_without_key(client):
    """旧版本私钥只在七鱼手里：跳过版本 1 的消息、推进 seq，版本 2 正常解密入库。"""
    key2 = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def item_v2(seq, record):
        it = _item(seq, record, ver=2)
        it["encrypt_random_key"] = base64.b64encode(
            key2.public_key().encrypt(f"rk{seq}".encode(), padding.PKCS1v15())).decode()
        return it

    archive = FakeArchive([_item(1, rec("m1", CUST, "旧消息")), _item(2, rec("m2", CUST, "旧消息2")),
                           item_v2(3, rec("m3", CUST, "换钥后的新消息"))])
    syncer = ArchiveSyncer(lambda: archive, {"2": key2}, batch_size=2, skip_versions=["1"])
    assert syncer.sync_once() == 1
    with db.SessionLocal() as s:
        assert [m.external_id for m in s.query(Message).all()] == ["m3"]
        assert s.get(SyncCursor, "wecom_archive_seq").value == "3"

    # 配了私钥的版本即使也列在跳过里，仍然解密
    archive.items.append(_item(4, rec("m4", CUST, "版本 1 也有私钥")))
    syncer = ArchiveSyncer(lambda: archive, {"1": KEY, "2": key2}, batch_size=10, skip_versions=["1"])
    assert syncer.sync_once() == 1


def test_skip_versions_setting_accepts_numbers(monkeypatch):
    from app.config import Settings

    monkeypatch.setenv("WECOM_ARCHIVE_SKIP_KEY_VERSIONS", "[1]")
    monkeypatch.setenv("WECOM_ARCHIVE_PRIVATE_KEY_FILES", '{"2": "/opt/wecom/archive_v2.pem"}')
    s = Settings()
    assert s.wecom_archive_skip_key_versions == ["1"]
    assert s.wecom_archive_private_key_files == {"2": "/opt/wecom/archive_v2.pem"}


def test_to_message_in_types():
    g = GroupInfo(None, None)
    m = to_message_in(rec("a", CUST, msgtype="file", file={"filename": "日志.zip"}), g)
    assert m.content == "[文件] 日志.zip" and m.is_external_group  # 查不到群信息时按发送人推断
    mixed = rec("b", "lisi", msgtype="mixed", mixed={"item": [
        {"type": "text", "content": json.dumps({"content": "看图"})},
        {"type": "image", "content": "{}"}]})
    m = to_message_in(mixed, g)
    assert m.content == "看图[图片]" and not m.is_external_group


def test_wecom_customer_detection():
    adapter = channels.ADAPTERS[channels.Channel.WECOM]
    assert adapter.is_customer(Message(sender_id=CUST)) and adapter.is_customer(Message(sender_id="wo" + CUST[2:]))
    assert adapter.is_customer(Message(sender_id="lo" + CUST[2:]))  # 也是其他企业的企业微信用户
    assert not adapter.is_customer(Message(sender_id="zhangsan"))
    assert not adapter.is_customer(Message(sender_id="wmeng"))  # 员工 userid 恰好以 wm 开头


def test_wecom_sender_kind():
    """微信用户、其他企业的企业微信用户、本企业员工，聊天记录里分别标注。"""
    adapter = channels.ADAPTERS[channels.Channel.WECOM]
    kinds = {uid: adapter.sender_kind(Message(sender_id=uid))
             for uid in (CUST, "wo" + CUST[2:], "lo" + CUST[2:], "zhangsan", "wmeng")}
    assert kinds == {CUST: "wechat", "wo" + CUST[2:]: "wecom", "lo" + CUST[2:]: "wecom",
                     "zhangsan": "internal", "wmeng": "internal"}


def test_archive_bad_item_skipped(client):
    class BadOnce(FakeArchive):
        def decrypt(self, random_key, encrypt_chat_msg):
            if random_key == "rk1":
                raise ArchiveError("DecryptData 失败，错误码 -1")
            return super().decrypt(random_key, encrypt_chat_msg)

    archive = BadOnce([_item(1, rec("m1", CUST, "坏消息")), _item(2, rec("m2", CUST, "好消息"))])
    syncer = ArchiveSyncer(lambda: archive, {"1": KEY}, batch_size=10)
    assert syncer.sync_once() == 1
    with db.SessionLocal() as s:
        assert [m.external_id for m in s.query(Message).all()] == ["m2"]
        assert s.get(SyncCursor, "wecom_archive_seq").value == "2"


def test_sync_requested_during_running_sync_reruns(client):
    archive = FakeArchive([_item(1, rec("m1", CUST, "第一条"))])
    syncer = ArchiveSyncer(lambda: archive, {"1": KEY}, batch_size=10)
    original = syncer._sync

    def sync_and_receive_more():
        n = original()
        if len(archive.items) == 1:  # 拉取过程中又来了新消息和通知
            archive.items.append(_item(2, rec("m2", CUST, "第二条")))
            assert syncer.sync_once() == 0  # 并发的通知不重复拉取，只做标记
        return n

    syncer._sync = sync_and_receive_more
    assert syncer.sync_once() == 2


def test_preload_sdk_only_when_archive_configured(monkeypatch, tmp_path):
    """会话存档启用时，后端启动先载入 SDK（避免和数据库组件的 C++ 运行库冲突崩溃）；未启用时不载入。"""
    import ctypes

    import app

    loaded = []
    monkeypatch.setattr(ctypes, "CDLL", lambda path: loaded.append(path))
    lib = tmp_path / "libWeWorkFinanceSdk_C.so"
    lib.write_bytes(b"")
    monkeypatch.setattr(settings, "wecom_sdk_lib_path", str(lib))

    app._preload_wecom_sdk()  # 未配置企业 ID 等，不载入
    assert loaded == []

    monkeypatch.setattr(settings, "wecom_corp_id", CORP)
    monkeypatch.setattr(settings, "wecom_archive_secret", "s")
    monkeypatch.setattr(settings, "wecom_archive_private_key_files", {"2": "/opt/wecom/archive_v2.pem"})
    app._preload_wecom_sdk()
    assert loaded == [str(lib)]

    monkeypatch.setattr(settings, "wecom_sdk_lib_path", str(tmp_path / "missing.so"))
    app._preload_wecom_sdk()  # 文件不存在时跳过，不影响启动
    assert loaded == [str(lib)]


def test_group_directory_contact_lookups_use_proxy_client():
    """外部群群名走单独的（代理）客户端，内部群仍用直连客户端。"""
    import httpx

    from app.channels.wecom.directory import GroupDirectory

    def handler(name, calls):
        def h(request: httpx.Request):
            calls.append(request.url.path)
            if request.url.path.endswith("/gettoken"):
                return httpx.Response(200, json={"errcode": 0, "access_token": f"tk-{name}", "expires_in": 7200})
            if request.url.path.endswith("/externalcontact/groupchat/get"):
                return httpx.Response(200, json={"errcode": 0, "group_chat": {"name": "某客户-有数项目群"}})
            if request.url.path.endswith("/msgaudit/groupchat/get"):
                return httpx.Response(200, json={"errcode": 0, "roomname": "售后内部群"})
            return httpx.Response(404)
        return h

    direct, proxied = [], []
    d = GroupDirectory(CORP, "archive-secret", "contact-secret",
                       client=httpx.Client(transport=httpx.MockTransport(handler("direct", direct))),
                       contact_client=httpx.Client(transport=httpx.MockTransport(handler("proxy", proxied))))
    assert d.lookup("wrEXT") == GroupInfo("某客户-有数项目群", True)
    assert proxied == ["/cgi-bin/gettoken", "/cgi-bin/externalcontact/groupchat/get"] and direct == []

    # 客户联系接口被拒（如 IP 不在可信 IP 里）时退回查内部群，走直连
    def refuse(request: httpx.Request):
        if request.url.path.endswith("/gettoken"):
            return httpx.Response(200, json={"errcode": 0, "access_token": "tk", "expires_in": 7200})
        return httpx.Response(200, json={"errcode": 60020, "errmsg": "not allow to access from your ip"})

    d = GroupDirectory(CORP, "archive-secret", "contact-secret",
                       client=httpx.Client(transport=httpx.MockTransport(handler("direct", direct))),
                       contact_client=httpx.Client(transport=httpx.MockTransport(refuse)))
    assert d.lookup("wrINT") == GroupInfo("售后内部群", False)


def test_backfill_group_names(client):
    """当时没查到群名的群，之后查得到时补上。"""
    class Directory:
        def __init__(self):
            self.names = {}

        def lookup(self, room_id, member=None):
            return GroupInfo(self.names.get(room_id), True if room_id in self.names else None)

    directory = Directory()
    archive = FakeArchive([_item(1, rec("m1", CUST, "你好", room="wrA")), _item(2, rec("m2", CUST, "在吗", room="wrB"))])
    syncer = ArchiveSyncer(lambda: archive, {"1": KEY}, directory, batch_size=10)
    assert syncer.sync_once() == 2
    with db.SessionLocal() as s:
        assert {m.group_name for m in s.query(Message).all()} == {None}

    directory.names["wrA"] = "某客户-有数项目群"
    syncer._names_checked_at = float("-inf")  # 跳过 10 分钟间隔（不能用 0：刚开机的机器 monotonic() 还不到 10 分钟）
    syncer.sync_once()
    with db.SessionLocal() as s:
        names = {m.group_id: m.group_name for m in s.query(Message).all()}
    assert names == {"wrA": "某客户-有数项目群", "wrB": None}


def test_backfill_sender_names(client):
    """早期入库、没有名字的消息，查到群成员名单后补上名字；查不到的人（如已退群）保持空。"""
    class Directory:
        def __init__(self):
            self.members = {}
            self.calls = 0

        def lookup(self, room_id, member=None):
            self.calls += 1
            return GroupInfo("某客户-有数项目群", True, self.members)

    directory = Directory()
    gone = "wm" + "Z" * 30
    archive = FakeArchive([_item(1, rec("m1", CUST, "你好")), _item(2, rec("m2", "zhangsan", "在的")),
                           _item(3, rec("m3", gone, "我先退群了"))])
    syncer = ArchiveSyncer(lambda: archive, {"1": KEY}, directory, batch_size=10)
    assert syncer.sync_once() == 3

    directory.members = {CUST: "王工", "zhangsan": "张三"}
    syncer._names_checked_at = float("-inf")
    syncer.sync_once()
    with db.SessionLocal() as s:
        names = {m.sender_id: m.sender_name for m in s.query(Message).all()}
    assert names == {CUST: "王工", "zhangsan": "张三", gone: None}

    calls = directory.calls
    syncer.sync_once()  # 10 分钟内不再补
    assert directory.calls == calls


def _member_directory(member_lists):
    """模拟企业微信群成员接口：每次查询依次返回 member_lists 里的一份名单，记录请求体。"""
    import httpx

    from app.channels.wecom.directory import GroupDirectory

    bodies = []

    def handler(request: httpx.Request):
        if request.url.path.endswith("/gettoken"):
            return httpx.Response(200, json={"errcode": 0, "access_token": "tk", "expires_in": 7200})
        bodies.append(json.loads(request.content))
        members = member_lists[min(len(bodies), len(member_lists)) - 1]
        return httpx.Response(200, json={"errcode": 0, "group_chat": {"name": "国投证券-网易-BD-支持",
                                                                       "member_list": members}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return GroupDirectory(CORP, "", "contact-secret", client=client, contact_client=client), bodies


def test_group_directory_member_names():
    """群成员名字：群昵称优先，没有群昵称用微信名或企业微信名；员工也在名单里。"""
    d, bodies = _member_directory([[
        {"userid": "wmA", "type": 2, "name": "贰零伍柒", "group_nickname": "国投证券_朱燕军"},
        {"userid": "loB", "type": 2, "name": "朱燕军", "group_nickname": ""},
        {"userid": "zhangsan", "type": 1, "name": "平台-小帆"},
        {"userid": "wmC", "type": 2, "name": "", "group_nickname": ""},
    ]])
    info = d.lookup("wrEXT")
    assert bodies == [{"chat_id": "wrEXT", "need_name": 1}]
    assert info.members == {"wmA": "国投证券_朱燕军", "loB": "朱燕军", "zhangsan": "平台-小帆"}


def test_group_directory_rechecks_new_member(monkeypatch):
    """有人刚进群就发言：缓存的名单里没有他时重查，同一个群最多每 10 分钟一次。"""
    from app.channels.wecom import directory as directory_mod

    now = [1000.0]
    monkeypatch.setattr(directory_mod.time, "time", lambda: now[0])
    d, bodies = _member_directory([[{"userid": "wmA", "name": "老客户"}],
                                   [{"userid": "wmA", "name": "老客户"}, {"userid": "wmNEW", "name": "新同事"}]])
    assert d.lookup("wrEXT", "wmA").members == {"wmA": "老客户"}
    assert d.lookup("wrEXT", "wmNEW").members == {"wmA": "老客户"} and len(bodies) == 1  # 刚查过，不重查
    now[0] += directory_mod.MEMBER_RECHECK
    assert d.lookup("wrEXT", "wmNEW").members["wmNEW"] == "新同事" and len(bodies) == 2
    assert d.lookup("wrEXT", "wmGONE").members and len(bodies) == 2  # 刚重查过，查不到的人不再反复查


def test_group_directory_members_for_mentions_refresh_after_10_minutes(monkeypatch):
    """工作台 @ 人时要新一些的名单：缓存超过 10 分钟就重查。"""
    from app.channels.wecom import directory as directory_mod

    now = [1000.0]
    monkeypatch.setattr(directory_mod.time, "time", lambda: now[0])
    d, bodies = _member_directory([[{"userid": "wmA", "name": "老客户"}],
                                   [{"userid": "wmA", "name": "老客户"}, {"userid": "wmNEW", "name": "新客户"}]])
    assert d.members("wrEXT") == {"wmA": "老客户"}
    now[0] += directory_mod.MEMBER_RECHECK - 1
    assert d.members("wrEXT") == {"wmA": "老客户"} and len(bodies) == 1
    now[0] += 1
    assert d.members("wrEXT")["wmNEW"] == "新客户" and len(bodies) == 2


def test_archive_poll_backs_off_after_error(monkeypatch):
    """拉取出错后隔 30 秒再试，正常时按配置的间隔（默认 3 秒）拉。"""
    import asyncio

    from app import main

    waits = []

    class Boom:
        def sync_once(self):
            raise RuntimeError("网络断了")

    class Ok:
        def sync_once(self):
            return 0

    syncers = iter([Boom(), Ok()])
    monkeypatch.setattr(main, "get_syncer", lambda: next(syncers))

    async def fake_sleep(seconds):
        waits.append(seconds)
        if len(waits) == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(main.settings, "wecom_archive_poll_interval", 3)
    try:
        asyncio.run(main._poll_wecom_archive())
    except asyncio.CancelledError:
        pass
    assert waits == [main.ARCHIVE_RETRY_SECONDS, 3]
