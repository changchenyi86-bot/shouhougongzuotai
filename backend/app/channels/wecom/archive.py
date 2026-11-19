"""企业微信会话内容存档：按 seq 增量拉取 → RSA + SDK 解密 → 归一成 MessageIn → 入库。"""

import base64
import json
import logging
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

from app import db
from app.channels.wecom.directory import GroupDirectory, GroupInfo
from app.channels.wecom.sdk import ArchiveClient, ArchiveError
from app.models import Channel, MessageType, SyncCursor
from app.schemas import MessageIn

log = logging.getLogger(__name__)

CURSOR_KEY = "wecom_archive_seq"

# 外部联系人（客户）的 userid 以 wo / wm 开头，员工为企业内 userid
EXTERNAL_USER_PREFIXES = ("wo", "wm")


def is_external_user(user_id: str) -> bool:
    return user_id.startswith(EXTERNAL_USER_PREFIXES)


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
        sender_name=None,
        msg_type=msg_type,
        content=content,
        sent_at=datetime.fromtimestamp(record.get("msgtime", 0) / 1000, tz=timezone.utc),
        raw=record,
    )


class ArchiveSyncer:
    def __init__(self, client_factory: Callable[[], ArchiveClient], private_keys: dict[str, RSAPrivateKey],
                 directory: GroupDirectory | None = None, batch_size: int = 100):
        self._client_factory = client_factory
        self._client: ArchiveClient | None = None
        self._keys = private_keys
        self._directory = directory
        self._batch_size = batch_size
        self._lock = threading.Lock()

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
            raise ArchiveError(f"缺少 publickey_ver={ver} 对应的私钥，请在 WECOM_ARCHIVE_PRIVATE_KEY_FILES 中配置")
        random_key = key.decrypt(base64.b64decode(item["encrypt_random_key"]), padding.PKCS1v15()).decode()
        return json.loads(self.client.decrypt(random_key, item["encrypt_chat_msg"]))

    def _group(self, room_id: str) -> GroupInfo:
        return self._directory.lookup(room_id) if self._directory else GroupInfo(None, None)

    def sync_once(self) -> int:
        """拉取到最新为止，返回新入库的消息数。已有拉取在进行时直接返回 0。"""
        if not self._lock.acquire(blocking=False):
            return 0
        try:
            return self._sync()
        finally:
            self._lock.release()

    def _sync(self) -> int:
        from app.ingest import ingest

        created = 0
        with db.SessionLocal() as session:
            cursor = session.get(SyncCursor, CURSOR_KEY) or SyncCursor(key=CURSOR_KEY, value="0")
            seq = int(cursor.value)
            while True:
                items = self.client.get_chat_data(seq, self._batch_size)
                if not items:
                    break
                for item in items:
                    try:
                        record = self._decrypt(item)
                    except ArchiveError:
                        raise
                    except Exception as exc:  # noqa: BLE001  单条损坏的消息跳过，不阻塞后续
                        log.exception("会话存档消息解密失败 seq=%s msgid=%s: %s", item.get("seq"), item.get("msgid"), exc)
                    else:
                        data = to_message_in(record, self._group(record["roomid"])) if record.get("roomid") else None
                        if data is not None:
                            created += int(ingest(session, data)[1])
                    seq = max(seq, int(item["seq"]))
                    cursor.value = str(seq)
                    session.merge(cursor)
                    session.commit()
                if len(items) < self._batch_size:
                    break
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
                                     settings.wecom_contact_secret),
            batch_size=settings.wecom_archive_batch_size,
        )
    return _syncer


def set_syncer(syncer: ArchiveSyncer | None) -> None:
    global _syncer
    _syncer = syncer
