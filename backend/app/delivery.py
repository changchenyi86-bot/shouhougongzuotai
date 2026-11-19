"""把采纳的回复发回群里。

目前所有回复都是 manual：工作台复制内容，由技术支持在客户端粘贴发送（客户群回复保留人工确认）。
以后接入直发能力（如经网易七鱼、群机器人）时，实现一个 ReplySender 并在渠道适配器的 reply_senders() 里注册即可。
"""

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.orm import Session

from app.models import Message


class DeliveryError(Exception):
    pass


@dataclass
class Delivery:
    mode: str  # "manual" / 以后的直发方式
    detail: str = ""


class ReplySender(Protocol):
    mode: str

    def available(self, session: Session, message: Message) -> bool:
        """这个群能否用该方式发送。"""

    def send(self, session: Session, message: Message, content: str) -> None:
        """发送失败抛 DeliveryError。"""


def deliver(session: Session, message: Message, content: str) -> Delivery:
    from app.channels import ADAPTERS

    for sender in ADAPTERS[message.channel].reply_senders():
        if sender.available(session, message):
            sender.send(session, message, content)
            return Delivery(sender.mode)
    return Delivery("manual", "该群暂不支持自动发送，请复制后在客户端发送")


def preferred_mode(session: Session, message: Message) -> str:
    from app.channels import ADAPTERS

    for sender in ADAPTERS[message.channel].reply_senders():
        if sender.available(session, message):
            return sender.mode
    return "manual"
