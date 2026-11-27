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

    def is_customer(self, message: Message) -> bool:
        """发送人是否客户（相对于本公司员工）。渠道能区分时应覆盖。"""
        return True

    def sender_kind(self, message: Message) -> str:
        """发送人来自哪里：internal 本企业员工 / wechat 微信用户 / wecom 其他企业的企业微信用户 /
        external 渠道分不出细类的外部人员。"""
        return "external" if self.is_customer(message) else "internal"

    def needs_reply(self, message: Message) -> bool:
        """这条消息是否需要 AI 生成回复建议：客户发的、非空文本消息。"""
        return self.is_customer(message) and message.msg_type == MessageType.TEXT and bool(message.content.strip())
