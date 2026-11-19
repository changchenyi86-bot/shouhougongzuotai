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


# 早期版本建的表缺少的列（create_all 不会给已有表加列）
_NEW_COLUMNS = {
    "suggestions": {"send_status": "VARCHAR(16)", "send_error": "TEXT", "sent_at": "TIMESTAMP WITH TIME ZONE"},
}


def _add_missing_columns() -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as conn:
        for table, columns in _NEW_COLUMNS.items():
            for name, ddl in columns.items():
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {name} {ddl}"))


def get_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session
