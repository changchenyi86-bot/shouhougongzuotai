from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse

from app.channels import ADAPTERS
from app.models import Channel

router = APIRouter()


@router.get("/webhooks/wecom", response_class=PlainTextResponse)
def wecom_verify(msg_signature: str = Query(...), timestamp: str = Query(...), nonce: str = Query(...),
                 echostr: str = Query(...)) -> str:
    """企业微信「接收事件服务器」URL 校验。"""
    return ADAPTERS[Channel.WECOM].verify_url(msg_signature, timestamp, nonce, echostr)


@router.post("/webhooks/wecom", response_class=PlainTextResponse)
async def wecom_event(request: Request) -> str:
    """企业微信事件推送（msgaudit_notify），企业微信要求 5 秒内返回。"""
    await ADAPTERS[Channel.WECOM].parse(request)
    return "success"
