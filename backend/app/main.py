import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import auth, customers, db, routing
from app.ai import evaluate, similar, worker
from app.api import (assist, attachments, candidates, conversations, customers as customers_api, evaluation, messages,
                     sending, staff, stats, suggestions)
from app.channels.wecom import router as wecom_router
from app.channels.wecom.archive import get_syncer
from app.config import settings
from app.db import init_db

log = logging.getLogger(__name__)


ARCHIVE_RETRY_SECONDS = 30  # 拉取出错（网络断了、IP 不在白名单等）后隔多久再试，免得每几秒刷一屏错误日志


async def _poll_wecom_archive() -> None:
    """定时拉取企业微信会话存档（回调通知之外的兜底）。"""
    while True:
        wait = settings.wecom_archive_poll_interval
        try:
            syncer = get_syncer()
            if syncer is not None:
                await asyncio.to_thread(syncer.sync_once)
        except Exception:  # noqa: BLE001  配置错误或网络问题时记录日志，稍后重试
            log.exception("会话存档拉取失败")
            wait = max(wait, ARCHIVE_RETRY_SECONDS)
        await asyncio.sleep(wait)


async def _sync_customers() -> None:
    """定时从售后系统、运维门户只读同步客户档案。"""
    while True:
        try:
            await asyncio.to_thread(customers.sync_once)
        except Exception:  # noqa: BLE001  源库连不上等，记日志，下一轮再试
            log.exception("客户档案同步出错")
        await asyncio.sleep(max(settings.customer_sync_minutes, 5) * 60)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    with db.SessionLocal() as session:
        auth.seed_teams(session)
        routing.backfill(session)
    # 重启前没生成完的建议重新生成；接入 embedding 之前、或隧道断开期间发出的回复，补存相似问答向量
    worker.resume_pending()
    worker.submit(similar.backfill)
    evaluate.resume()  # 重启前没跑完的评测接着跑
    tasks = []
    if settings.wecom_archive_enabled and settings.wecom_archive_poll_interval > 0:
        tasks.append(asyncio.create_task(_poll_wecom_archive()))
    if settings.customer_db_config_file:
        tasks.append(asyncio.create_task(_sync_customers()))
    yield
    for task in tasks:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    worker.shutdown()
    evaluate.shutdown()


app = FastAPI(title="售后技术支持工作台", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)
# 企业微信回调需返回纯文本，路由需在通用 /webhooks/{channel} 之前注册
app.include_router(wecom_router.router)
app.include_router(messages.router)
app.include_router(suggestions.router)
app.include_router(sending.router)
app.include_router(conversations.router)
app.include_router(attachments.router)
app.include_router(staff.router)
app.include_router(assist.router)
app.include_router(candidates.router)
app.include_router(candidates.sync_router)
app.include_router(stats.router)
app.include_router(evaluation.router)
app.include_router(customers_api.router)
app.include_router(sending.agent_router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}
