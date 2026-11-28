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
                    "via_chat": "BOOLEAN NOT NULL DEFAULT FALSE", "profile": "JSON",
                    "covers": "JSON", "merged_into": "UUID", "question": "TEXT",
                    "products": "JSON", "product_source": "VARCHAR(16)", "product_reason": "TEXT",
                    "product_override": "JSON", "product_original": "JSON", "product_corrected_by": "VARCHAR(64)",
                    "missed": "VARCHAR(16)", "missed_by": "VARCHAR(64)", "missed_at": "TIMESTAMP WITH TIME ZONE"},
    "eval_results": {"question": "TEXT"},
    "attachments": {"extracted_text": "TEXT", "extract_status": "VARCHAR(16)",
                    "extract_error": "TEXT", "extract_model": "VARCHAR(128)",
                    "extracted_at": "TIMESTAMP WITH TIME ZONE"},
    "messages": {"attachment_id": "UUID REFERENCES attachments(id) ON DELETE SET NULL", "media_error": "TEXT"},
    "send_tasks": {"attachment_ids": "JSON", "replaced_by": "UUID", "mentions": "JSON", "warning": "TEXT"},
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
        # 早期版本把企业微信视频存成「其他」消息，改成视频，工作台里才能下载、播放（枚举按名字存）
        conn.execute(text("UPDATE messages SET msg_type = 'VIDEO' "
                          "WHERE msg_type = 'OTHER' AND content = '[视频]' AND attachment_id IS NULL"))
        # 去掉了「转人工」：早期转人工、还没回复的消息回到待处理，记为 AI 没答上（谁、什么时候转的照留）
        conn.execute(text(
            "UPDATE suggestions SET missed = 'escalated', missed_by = handled_by, "
            "missed_at = COALESCE(handled_at, updated_at), handled_by = NULL, handled_at = NULL, "
            "status = CASE WHEN COALESCE(content, '') <> '' THEN 'READY' ELSE 'FAILED' END, "
            "error = CASE WHEN COALESCE(content, '') <> '' THEN error "
            "ELSE COALESCE(error, 'AI 没有写回复，可以重新生成') END "
            "WHERE status = 'ESCALATED'"))


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
