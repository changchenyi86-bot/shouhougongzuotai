from abc import ABC, abstractmethod

from fastapi import Request

from app.models import Channel, Message, MessageType
from app.schemas import MessageIn


class ChannelAdapter(ABC):
    """每个 IM 渠道实现一个适配器：校验回调、解析报文、归一成 MessageIn。"""

    channel: Channel

    @abstractmethod
    async def parse(self, request: Request) -> MessageIn | None:
        """解析渠道回调。返回 None 表示非消息事件（如 URL 校验、心跳），无需入库。"""

    def reply_senders(self) -> list:
        """按优先级返回可用的直发方式（app.delivery.ReplySender）。默认没有，只能复制后人工发送。"""
        return []

    def is_customer(self, message: Message) -> bool:
        """发送人是否客户（相对于本公司员工）。渠道能区分时应覆盖。"""
        return True

    def needs_reply(self, message: Message) -> bool:
        """这条消息是否需要 AI 生成回复建议：客户发的、非空文本消息。"""
        return self.is_customer(message) and message.msg_type == MessageType.TEXT and bool(message.content.strip())
