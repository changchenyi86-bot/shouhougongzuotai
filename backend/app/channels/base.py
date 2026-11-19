from abc import ABC, abstractmethod

from fastapi import Request

from app.models import Channel
from app.schemas import MessageIn


class ChannelAdapter(ABC):
    """每个 IM 渠道实现一个适配器：校验回调、解析报文、归一成 MessageIn。"""

    channel: Channel

    @abstractmethod
    async def parse(self, request: Request) -> MessageIn | None:
        """解析渠道回调。返回 None 表示非消息事件（如 URL 校验、心跳），无需入库。"""
