"""下载客户在群里发的图片、文件，存成附件，工作台里可以直接查看。

会话存档里的媒体内容需要用 sdkfileid 单独拉取。入库后在后台线程下载，失败时记录原因，可在工作台重试。
"""

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor

from app import attachments, db
from app.config import settings
from app.models import Message, MessageType

log = logging.getLogger(__name__)

_executor: ThreadPoolExecutor | None = None


def media_info(raw: dict | None) -> tuple[str, str, int] | None:
    """(sdkfileid, 文件名, 大小)；不是可下载的图片或文件时返回 None。"""
    if not raw:
        return None
    t = raw.get("msgtype")
    body = raw.get(t) if isinstance(raw.get(t), dict) else None
    if t not in ("image", "file") or not body or not body.get("sdkfileid"):
        return None
    name = body.get("filename") or f"图片_{raw.get('msgid', '')[:8]}"
    return body["sdkfileid"], name, int(body.get("filesize") or 0)


def submit(message_id: uuid.UUID) -> None:
    """测试中会被替换为同步执行。"""
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="wecom-media")
    _executor.submit(fetch, message_id)


def fetch(message_id: uuid.UUID) -> bool:
    """下载一条消息的图片或文件。返回是否成功。"""
    from app.channels.wecom.archive import get_syncer

    with db.SessionLocal() as session:
        msg = session.get(Message, message_id)
        if msg is None or msg.attachment_id is not None:
            return msg is not None
        info = media_info(msg.raw)
        if info is None:
            msg.media_error = "这条消息没有可下载的内容"
            session.commit()
            return False
        sdkfileid, name, size = info
        if size > attachments.max_bytes():
            msg.media_error = f"文件超过 {settings.attachment_max_mb}MB，未下载，请到企业微信里查看"
            session.commit()
            return False
        syncer = get_syncer()
        if syncer is None:
            msg.media_error = "会话存档未配置，无法下载"
            session.commit()
            return False
        try:
            data = syncer.client.get_media(sdkfileid, attachments.max_bytes())
            att = attachments.save(session, data, name, "channel")
        except Exception as exc:  # noqa: BLE001  下载失败不影响消息本身
            log.warning("下载企业微信媒体失败 message=%s: %s", message_id, exc)
            session.rollback()
            msg = session.get(Message, message_id)
            msg.media_error = f"下载失败：{exc}"
            session.commit()
            return False
        msg.attachment_id, msg.media_error = att.id, None
        session.commit()
        return True


def wants_media(msg: Message) -> bool:
    return msg.msg_type in (MessageType.IMAGE, MessageType.FILE) and media_info(msg.raw) is not None
