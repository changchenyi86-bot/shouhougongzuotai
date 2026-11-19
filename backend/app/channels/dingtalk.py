from fastapi import Request

from app.channels.base import ChannelAdapter
from app.models import Channel
from app.schemas import MessageIn


class DingTalkAdapter(ChannelAdapter):
    """钉钉群消息适配器（待实现：回调签名校验、解密、报文解析）。"""

    channel = Channel.DINGTALK

    async def parse(self, request: Request) -> MessageIn | None:
        raise NotImplementedError("钉钉渠道接入尚未实现")
