from collections.abc import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    """建表。正式迭代时改为 Alembic 迁移。"""
    from app import models  # noqa: F401  确保模型已注册

    if engine.dialect.name == "postgresql":
        # 知识库向量检索（问题自迭代）将依赖 pgvector
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(engine)
    _add_missing_columns()
    _merge_conversation_ends()


# 早期版本建的表缺少的列（create_all 不会给已有表加列）
_NEW_COLUMNS = {
    "suggestions": {"send_status": "VARCHAR(16)", "send_error": "TEXT", "sent_at": "TIMESTAMP WITH TIME ZONE",
                    "knowledge": "JSON", "knowledge_error": "TEXT", "knowledge_note": "TEXT",
                    "needs_reply": "BOOLEAN", "auto_dismissed": "BOOLEAN NOT NULL DEFAULT FALSE",
                    "reopened_by": "VARCHAR(64)", "adoption": "VARCHAR(16)", "adoption_score": "DOUBLE PRECISION",
                    "via_chat": "BOOLEAN NOT NULL DEFAULT FALSE", "profile": "JSON"},
    "messages": {"attachment_id": "UUID REFERENCES attachments(id) ON DELETE SET NULL", "media_error": "TEXT"},
    "send_tasks": {"attachment_ids": "JSON", "replaced_by": "UUID"},
}
# 早期版本的非空约束需要放开
_DROP_NOT_NULL = {"send_tasks": ["suggestion_id"]}


def _add_missing_columns() -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as conn:
        for table, columns in _NEW_COLUMNS.items():
            for name, ddl in columns.items():
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {name} {ddl}"))
        for table, columns in _DROP_NOT_NULL.items():
            for name in columns:
                conn.execute(text(f"ALTER TABLE {table} ALTER COLUMN {name} DROP NOT NULL"))


def get_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session


def _merge_conversation_ends() -> None:
    """早期版本只记了结束会话（conversation_ends），现在并入会话表 conversations。"""
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as conn:
        if conn.execute(text("SELECT to_regclass('conversation_ends')")).scalar() is None:
            return
        conn.execute(text(
            "INSERT INTO conversations (id, channel, group_id, opened_at, ended_at, ended_by, closed_count) "
            "SELECT id, channel, group_id, ended_at, ended_at, ended_by, closed_count FROM conversation_ends "
            "ON CONFLICT (id) DO NOTHING"
        ))
        conn.execute(text("DROP TABLE conversation_ends"))
