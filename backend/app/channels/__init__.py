from app.channels.base import ChannelAdapter
from app.channels.dingtalk import DingTalkAdapter
from app.channels.feishu import FeishuAdapter
from app.channels.wecom import WeComAdapter
from app.models import Channel

ADAPTERS: dict[Channel, ChannelAdapter] = {
    Channel.WECOM: WeComAdapter(),
    Channel.DINGTALK: DingTalkAdapter(),
    Channel.FEISHU: FeishuAdapter(),
}

__all__ = ["ADAPTERS", "ChannelAdapter"]
