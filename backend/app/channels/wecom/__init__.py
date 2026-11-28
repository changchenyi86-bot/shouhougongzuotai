"""企业微信渠道：通过「会话内容存档」获取内外部群全部消息。

- 消息来源：ArchiveSyncer 定时按 seq 拉取（archive.py）
- 回调：存档应用的「接收事件服务器」推送 msgaudit_notify 时立即触发一次拉取
- 回复：会话存档无法通过 API 发消息。工作台采纳后要么复制后人工发送，要么由电脑端发送程序
  操作官方企业微信客户端自动发送（见 app/send_queue.py 和 windows-agent/）
"""

import asyncio
import logging
import xml.etree.ElementTree as ET

from fastapi import HTTPException, Request, status

from app.channels.base import ChannelAdapter
from app.channels.wecom.archive import get_syncer, is_external_user, sender_kind
from app.channels.wecom.crypto import WeComCrypto, WeComCryptoError
from app.config import settings
from app.models import Channel, Message
from app.schemas import MessageIn

log = logging.getLogger(__name__)


def get_crypto() -> WeComCrypto:
    if not (settings.wecom_callback_token and settings.wecom_callback_aes_key and settings.wecom_corp_id):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "企业微信回调未配置")
    return WeComCrypto(settings.wecom_callback_token, settings.wecom_callback_aes_key, settings.wecom_corp_id)


def _run_sync() -> None:
    syncer = get_syncer()
    if syncer is None:
        log.warning("收到 msgaudit_notify，但会话存档未配置，忽略")
        return
    try:
        syncer.sync_once()
    except Exception:  # noqa: BLE001
        log.exception("会话存档拉取失败")


def trigger_sync() -> None:
    asyncio.get_running_loop().run_in_executor(None, _run_sync)


class WeComAdapter(ChannelAdapter):
    channel = Channel.WECOM

    def verify_url(self, msg_signature: str, timestamp: str, nonce: str, echostr: str) -> str:
        """配置回调地址时企业微信发起的 GET 校验，返回解密后的 echostr。"""
        crypto = get_crypto()
        try:
            crypto.verify(msg_signature, timestamp, nonce, echostr)
            return crypto.decrypt(echostr)
        except WeComCryptoError as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc

    async def parse(self, request: Request) -> MessageIn | None:
        """处理事件推送。消息本身走存档拉取，这里只负责在 msgaudit_notify 时触发拉取，始终返回 None。"""
        crypto = get_crypto()
        q = request.query_params
        try:
            encrypted = ET.fromstring(await request.body()).findtext("Encrypt") or ""
            crypto.verify(q.get("msg_signature", ""), q.get("timestamp", ""), q.get("nonce", ""), encrypted)
            event = ET.fromstring(crypto.decrypt(encrypted))
        except (ET.ParseError, WeComCryptoError) as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"回调校验失败：{exc}") from exc
        if event.findtext("Event") == "msgaudit_notify":
            trigger_sync()
        return None

    def is_customer(self, message: Message) -> bool:
        # 外部联系人即客户（名单上用个人微信回复的同事除外）；员工发言也入库，用于上下文和后续知识沉淀，但不生成建议
        from app import colleagues

        return is_external_user(message.sender_id) and not colleagues.is_colleague(self.channel, message.sender_id)

    def sender_kind(self, message: Message) -> str:
        return sender_kind(message.sender_id)
