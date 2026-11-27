import os

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
os.environ["APP_ENV_FILE"] = ""  # 不读本机的 backend/.env，测试只用代码里的默认配置

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app import db  # noqa: E402
from app.ai import llm, worker  # noqa: E402
from app.ai.llm import SuggestionDraft  # noqa: E402
from app.ai.reflow import Judgement  # noqa: E402
from app.ai.suggest import RoundQuery  # noqa: E402
from app.ai.refine import RefinedReply  # noqa: E402
from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402


class FakeLLM:
    """记录调用并返回固定建议，避免测试访问真实模型。"""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.draft_result = SuggestionDraft(can_answer=True, confidence="high",
                                            reply="您好，请清理浏览器缓存后重试。", reason="常见问题",
                                            references=[])
        self.error: Exception | None = None
        # 和 AI 对话、回流判断的固定结果，按输出结构取
        self.results: dict[type, object] = {
            RoundQuery: RoundQuery(question="补全后的客户问题"),
            RefinedReply: RefinedReply(reply="您好，请先清理浏览器缓存再重新导出。", note=""),
            Judgement: Judgement(general=True, reason="导出问题，别的客户也会遇到", title="报表导出一直转圈",
                                 question="报表导出一直转圈怎么办？", answer="请先清理浏览器缓存再重新导出。",
                                 module="报表导出", branch="有数", kind="troubleshoot"),
        }
        self.generated: list[tuple[type, str]] = []

    def draft(self, system: str, prompt: str):
        self.calls.append((system, prompt))
        if self.error:
            raise self.error
        return self.draft_result, "fake-model"

    def generate(self, system: str, prompt: str, schema):
        self.generated.append((schema, prompt))
        if self.error:
            raise self.error
        return self.results[schema], "fake-model"


@pytest.fixture()
def fake_llm(monkeypatch):
    fake = FakeLLM()
    monkeypatch.setattr(llm, "_provider", fake)
    return fake


@pytest.fixture()
def client(monkeypatch, fake_llm):
    engine = create_engine("sqlite+pysqlite:///:memory:", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    monkeypatch.setattr(db, "engine", engine)
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False))
    # 建议生成改为同步执行，便于断言
    monkeypatch.setattr(worker, "submit", lambda fn, *args: fn(*args))
    monkeypatch.setattr(worker, "schedule", lambda delay, fn, *args: worker.submit(fn, *args))
    # 测试数据的时间是固定的，不受“只为近期消息生成建议”的限制
    monkeypatch.setattr(settings, "ai_suggest_max_age_minutes", 10**8)
    with TestClient(app) as c:
        # 工作台接口都要登录：先设一个管理员并登录，Cookie 留在 c 里
        r = c.post("/api/auth/setup", json={"name": "李四", "username": "lisi", "password": "password123"})
        assert r.status_code == 201, r.text
        yield c
