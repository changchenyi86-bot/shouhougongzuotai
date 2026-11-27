"""企业微信会话内容存档：按 seq 增量拉取 → RSA + SDK 解密 → 归一成 MessageIn → 入库。"""

import base64
import json
import logging
import threading
import time
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from sqlalchemy import func, select, update

from app import db
from app.channels.wecom import media
from app.channels.wecom.directory import GroupDirectory, GroupInfo
from app.channels.wecom.sdk import ArchiveClient, ArchiveError


class MissingKeyError(ArchiveError):
    """缺少某个版本的解密私钥：停止拉取且不推进 seq，避免丢消息。"""
from app.models import Channel, Message, MessageType, SyncCursor
from app.schemas import MessageIn

log = logging.getLogger(__name__)

CURSOR_KEY = "wecom_archive_seq"
BACKFILL_INTERVAL = 600  # 秒

# 外部联系人（客户）的 external_userid 是 30 位左右的随机串：wm 开头是微信用户，wo、lo 开头是其他企业的企业微信用户
# （群成员接口里都是「外部联系人」）。员工 userid 由企业自定义，可能恰好以这些字母开头（如 wmeng），所以同时检查长度
WECHAT_USER_PREFIX = "wm"
EXTERNAL_USER_PREFIXES = (WECHAT_USER_PREFIX, "wo", "lo")
EXTERNAL_USER_MIN_LEN = 20


def is_external_user(user_id: str) -> bool:
    return user_id.startswith(EXTERNAL_USER_PREFIXES) and len(user_id) >= EXTERNAL_USER_MIN_LEN


def sender_kind(user_id: str) -> str:
    """internal 本企业员工 / wechat 微信用户 / wecom 其他企业的企业微信用户。"""
    if not is_external_user(user_id):
        return "internal"
    return "wechat" if user_id.startswith(WECHAT_USER_PREFIX) else "wecom"


def load_private_keys(files: dict[str, str]) -> dict[str, RSAPrivateKey]:
    return {
        str(ver): serialization.load_pem_private_key(Path(path).read_bytes(), password=None)
        for ver, path in files.items()
    }


LABELS = {"image": "图片", "file": "文件", "voice": "语音", "video": "视频", "emotion": "表情", "card": "名片",
          "location": "位置", "weapp": "小程序", "chatrecord": "聊天记录", "redpacket": "红包",
          "meeting_voice_call": "会议"}


def _content_of(record: dict) -> tuple[MessageType, str]:
    t = record.get("msgtype", "")
    body = record.get(t) if isinstance(record.get(t), dict) else {}
    if t == "text":
        return MessageType.TEXT, body.get("content", "")
    if t == "markdown":
        return MessageType.TEXT, body.get("info", {}).get("content", "") or body.get("content", "")
    if t == "image":
        return MessageType.IMAGE, "[图片]"
    if t == "video":
        return MessageType.VIDEO, "[视频]"
    if t == "file":
        return MessageType.FILE, f"[文件] {body.get('filename', '')}".strip()
    if t == "link":
        return MessageType.OTHER, f"[链接] {body.get('title', '')} {body.get('link_url', '')}".strip()
    if t == "mixed":
        parts = []
        for item in body.get("item", []):
            try:
                inner = json.loads(item.get("content", "{}"))
            except (TypeError, ValueError):
                inner = {}
            if item.get("type") == "text":
                parts.append(inner.get("content", ""))
            else:
                parts.append(f"[{LABELS.get(item.get('type', ''), '其他')}]")
        return MessageType.TEXT, "".join(parts)
    return MessageType.OTHER, f"[{LABELS.get(t, t or '未知消息')}]"


def to_message_in(record: dict, group: GroupInfo) -> MessageIn | None:
    """解密后的存档记录 → MessageIn。只保留群聊里的发送动作，单聊、撤回、切换企业等跳过。"""
    room_id = record.get("roomid")
    if not room_id or record.get("action") != "send":
        return None
    msg_type, content = _content_of(record)
    sender = record.get("from", "")
    return MessageIn(
        channel=Channel.WECOM,
        external_id=record["msgid"],
        group_id=room_id,
        group_name=group.name,
        is_external_group=group.is_external if group.is_external is not None else is_external_user(sender),
        sender_id=sender,
        sender_name=group.members.get(sender),
        msg_type=msg_type,
        content=content,
        sent_at=datetime.fromtimestamp(record.get("msgtime", 0) / 1000, tz=timezone.utc),
        raw=record,
    )


class ArchiveSyncer:
    def __init__(self, client_factory: Callable[[], ArchiveClient], private_keys: dict[str, RSAPrivateKey],
                 directory: GroupDirectory | None = None, batch_size: int = 100,
                 skip_versions: Iterable[str] = ()):
        self._client_factory = client_factory
        self._client: ArchiveClient | None = None
        self._keys = private_keys
        # 有私钥的版本照常解密；没有私钥且列在这里的版本直接跳过
        self._skip_versions = {str(v) for v in skip_versions} - set(private_keys)
        self._directory = directory
        self._batch_size = batch_size
        self._lock = threading.Lock()
        self._rerun = threading.Event()
        self._names_checked_at = float("-inf")  # monotonic() 从开机算起，刚开机时也要能立刻查第一轮

    @property
    def client(self) -> ArchiveClient:
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    def _decrypt(self, item: dict) -> dict:
        ver = str(item.get("publickey_ver"))
        key = self._keys.get(ver)
        if key is None:
            # 不推进 seq，等管理员补上对应版本的私钥后重拉，避免丢消息
            raise MissingKeyError(f"缺少 publickey_ver={ver} 对应的私钥，请在 WECOM_ARCHIVE_PRIVATE_KEY_FILES 中配置；"
                                  f"确定不要这个版本的消息时，可加到 WECOM_ARCHIVE_SKIP_KEY_VERSIONS")
        random_key = key.decrypt(base64.b64decode(item["encrypt_random_key"]), padding.PKCS1v15()).decode()
        return json.loads(self.client.decrypt(random_key, item["encrypt_chat_msg"]))

    @property
    def directory(self) -> GroupDirectory | None:
        """查群名、群成员名单（工作台 @ 人时也用）。"""
        return self._directory

    def _group(self, room_id: str, sender: str | None = None) -> GroupInfo:
        return self._directory.lookup(room_id, sender) if self._directory else GroupInfo(None, None)

    def sync_once(self) -> int:
        """拉取到最新为止，返回新入库的消息数。
        已有拉取在进行时只做标记并返回 0，正在进行的那次结束前会再拉一轮，避免漏掉期间到达的消息。"""
        self._rerun.set()
        if not self._lock.acquire(blocking=False):
            return 0
        try:
            created = 0
            while self._rerun.is_set():
                self._rerun.clear()
                created += self._sync()
            return created
        finally:
            self._lock.release()

    def backfill_names(self, session) -> int:
        """已入库但没有群名、发送人名字的消息（如当时没查到、或早期版本没查）再查一次，查到就补上。
        最多每 10 分钟一轮；群信息有缓存，查不到名字的人（如已退群）不会反复调接口。返回补上的群名和人名数。"""
        if self._directory is None or time.monotonic() - self._names_checked_at < BACKFILL_INTERVAL:
            return 0
        self._names_checked_at = time.monotonic()
        wecom = Message.channel == Channel.WECOM
        no_group_name = set(session.scalars(
            select(Message.group_id).where(wecom, Message.group_name.is_(None)).distinct()).all())
        no_sender_name = session.execute(
            select(Message.group_id, Message.sender_id).where(wecom, Message.sender_name.is_(None))
            .group_by(Message.group_id, Message.sender_id).order_by(func.max(Message.sent_at).desc()).limit(2000)
        ).all()
        missing: dict[str, set[str]] = {room_id: set() for room_id in no_group_name}
        for room_id, sender in no_sender_name:
            missing.setdefault(room_id, set()).add(sender)
        groups = people = 0
        for room_id, senders in missing.items():
            info = self._directory.lookup(room_id)
            where = (wecom, Message.group_id == room_id)
            if room_id in no_group_name and info.name:
                session.execute(update(Message).where(*where, Message.group_name.is_(None))
                                .values(group_name=info.name))
                groups += 1
            for sender in senders & info.members.keys():
                session.execute(update(Message).where(*where, Message.sender_id == sender,
                                                      Message.sender_name.is_(None))
                                .values(sender_name=info.members[sender]))
                people += 1
        if groups or people:
            session.commit()
            log.info("补上了 %d 个群的群名、%d 个人的名字", groups, people)
        return groups + people

    def _sync(self) -> int:
        from app.ingest import ingest

        created = skipped = 0
        with db.SessionLocal() as session:
            try:
                self.backfill_names(session)
            except Exception:  # noqa: BLE001  补名字失败不影响拉消息
                session.rollback()
                log.exception("补群名、人名失败")
            cursor = session.get(SyncCursor, CURSOR_KEY) or SyncCursor(key=CURSOR_KEY, value="0")
            seq = int(cursor.value)
            while True:
                items = self.client.get_chat_data(seq, self._batch_size)
                if not items:
                    break
                for item in items:
                    if str(item.get("publickey_ver")) in self._skip_versions:
                        skipped += 1
                        record = None
                    else:
                        try:
                            record = self._decrypt(item)
                        except MissingKeyError:
                            if skipped:
                                log.info("会话存档跳过旧公钥版本加密的消息 %d 条，seq=%d", skipped, seq)
                            raise
                        except Exception as exc:  # noqa: BLE001  单条损坏的消息跳过，不阻塞后续
                            log.exception("会话存档消息解密失败 seq=%s msgid=%s: %s",
                                          item.get("seq"), item.get("msgid"), exc)
                            record = None
                    if record is not None:
                        data = (to_message_in(record, self._group(record["roomid"], record.get("from")))
                                if record.get("roomid") else None)
                        if data is not None:
                            msg, is_new = ingest(session, data)
                            created += int(is_new)
                            if is_new and media.wants_media(msg):
                                media.submit(msg.id)
                    seq = max(seq, int(item["seq"]))
                    cursor.value = str(seq)
                    session.merge(cursor)
                    session.commit()
                if len(items) < self._batch_size:
                    break
        if skipped:
            log.info("会话存档跳过旧公钥版本加密的消息 %d 条，seq=%d", skipped, seq)
        if created:
            log.info("会话存档拉取新消息 %d 条，seq=%d", created, seq)
        return created


_syncer: ArchiveSyncer | None = None


def get_syncer() -> ArchiveSyncer | None:
    """按配置构造全局 syncer；未配置会话存档时返回 None。"""
    global _syncer
    from app.config import settings

    if _syncer is None and settings.wecom_archive_enabled:
        from app.channels.wecom.sdk import FinanceSdk

        _syncer = ArchiveSyncer(
            client_factory=lambda: FinanceSdk(
                settings.wecom_sdk_lib_path, settings.wecom_corp_id, settings.wecom_archive_secret,
                settings.wecom_archive_proxy, settings.wecom_archive_proxy_password,
            ),
            private_keys=load_private_keys(settings.wecom_archive_private_key_files),
            directory=GroupDirectory(settings.wecom_corp_id, settings.wecom_archive_secret,
                                     settings.wecom_contact_secret, contact_proxy=settings.wecom_contact_proxy),
            batch_size=settings.wecom_archive_batch_size,
            skip_versions=settings.wecom_archive_skip_key_versions,
        )
        log.info("会话存档已启用：可解密的公钥版本 %s，跳过的版本 %s",
                 sorted(settings.wecom_archive_private_key_files), sorted(settings.wecom_archive_skip_key_versions))
    return _syncer


def set_syncer(syncer: ArchiveSyncer | None) -> None:
    global _syncer
    _syncer = syncer
