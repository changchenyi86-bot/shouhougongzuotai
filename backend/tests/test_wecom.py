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
    def lookup(self, room_id):
        return GroupInfo("某客户-有数项目群", True)


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
        assert msgs["m5"].content == "[图片]"
        # 只有客户的文本消息生成建议
        sugs = s.query(Suggestion).all()
        assert [s.message_id for s in sugs] == [m1.id]
        assert s.get(SyncCursor, "wecom_archive_seq").value == "5"

    # 员工发言作为上下文出现在提示里
    assert f"客户 {CUST}：报表导出一直转圈怎么办？" in fake_llm.calls[0][1]

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
    assert not adapter.is_customer(Message(sender_id="zhangsan"))
    assert not adapter.is_customer(Message(sender_id="wmeng"))  # 员工 userid 恰好以 wm 开头


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
