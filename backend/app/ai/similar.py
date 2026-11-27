"""相似历史问答：工作台自己的向量检索（bge-m3 等 embedding，存 pgvector）。

两类问答：
- knowledge：审核通过的工作台知识（候选知识审核通过后），技术支持确认过，可信度和纠错卡相当
- reply：技术支持以前实际发出的回复，未经审核，只供模型参考思路和口径

生成回复建议时按客户消息查这两类，各取最相似的几条交给模型。没配置 embedding 时不查。
"""

import logging
import math
import uuid

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.ai import embedding
from app.ai.embedding import EmbeddingError
from app.ai.knowledge import KnowledgeItem, search_query
from app.config import settings
from app.models import CandidateStatus, KnowledgeCandidate, Message, QaEntry, Suggestion, SuggestionStatus

log = logging.getLogger(__name__)

REPLIED = (SuggestionStatus.ADOPTED, SuggestionStatus.EDITED)


def _embed(texts: list[str]) -> list[list[float]] | None:
    client = embedding.get_client()
    return client.embed(texts) if client else None


def _upsert(session: Session, entry: QaEntry | None, **values) -> QaEntry:
    if entry is None:
        entry = QaEntry(**values)
        session.add(entry)
    else:
        for k, v in values.items():
            setattr(entry, k, v)
    return entry


def index_reply(session: Session, sug: Suggestion) -> bool:
    """把一条已发出的回复存成相似问答（寒暄、确认类的客户消息不存）。失败抛 EmbeddingError。"""
    message = session.get(Message, sug.message_id)
    answer = (sug.final_content or "").strip()
    question = (sug.question or message.content) if message is not None else ""
    if message is None or sug.status not in REPLIED or not answer or search_query(question) is None:
        return False
    vectors = _embed([question])
    if vectors is None:
        return False
    entry = session.scalar(select(QaEntry).where(QaEntry.suggestion_id == sug.id))
    _upsert(session, entry, source="reply", suggestion_id=sug.id, message_id=message.id,
            question=question[:2000], answer=answer[:4000], branch=None,
            group_name=message.group_name, author=sug.handled_by, embedding=vectors[0],
            model=settings.embedding_model)
    return True


def index_knowledge(session: Session, cand: KnowledgeCandidate) -> bool:
    """审核通过的工作台知识存成相似问答；停用后删掉。失败抛 EmbeddingError。"""
    entry = session.scalar(select(QaEntry).where(QaEntry.candidate_id == cand.id))
    if cand.status != CandidateStatus.APPROVED:
        if entry is not None:
            session.delete(entry)
        return False
    vectors = _embed([cand.question])
    if vectors is None:
        return False
    _upsert(session, entry, source="knowledge", candidate_id=cand.id, question=cand.question,
            answer=cand.answer, branch=cand.branch, group_name=None, author=cand.author,
            embedding=vectors[0], model=settings.embedding_model)
    return True


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _nearest(session: Session, vector: list[float], source: str, exclude: set[uuid.UUID], k: int,
             min_score: float, skip_suggestions: set[uuid.UUID] = frozenset()) -> list[tuple[QaEntry, float]]:
    if session.get_bind().dialect.name == "postgresql":
        rows = session.execute(
            text("SELECT id, 1 - (embedding <=> CAST(:q AS vector)) AS score FROM qa_entries "
                 "WHERE source = :source AND embedding IS NOT NULL AND model = :model "
                 "ORDER BY embedding <=> CAST(:q AS vector) LIMIT :n"),
            {"q": "[" + ",".join(f"{x:.6f}" for x in vector) + "]", "source": source,
             "model": settings.embedding_model, "n": k + len(exclude) + len(skip_suggestions)},
        ).all()
        scored = [(session.get(QaEntry, r.id), float(r.score)) for r in rows]
    else:  # 测试用的 SQLite：在内存里算
        entries = session.scalars(
            select(QaEntry).where(QaEntry.source == source, QaEntry.embedding.is_not(None),
                                  QaEntry.model == settings.embedding_model)
        ).all()
        scored = sorted(((e, _cosine(vector, e.embedding)) for e in entries), key=lambda x: -x[1])
    out = []
    for entry, score in scored:
        if entry is None or score < min_score or entry.message_id in exclude or entry.suggestion_id in skip_suggestions:
            continue
        out.append((entry, score))
        if len(out) >= k:
            break
    return out


def search(session: Session, question: str, exclude_messages: set[uuid.UUID] | None = None,
           exclude_same_question: bool = False) -> list[KnowledgeItem]:
    """按客户消息查相似的工作台知识和历史回复。没配置 embedding 或不需要查时返回空，失败抛 EmbeddingError。

    exclude_same_question：评测时用，题目取自真实群消息，问题原文一字不差的那条回复就是答案，不能给模型看。
    """
    if search_query(question) is None:
        return []
    vectors = _embed([question])
    if vectors is None:
        return []
    exclude = exclude_messages or set()
    top, floor = settings.similar_qa_top, settings.similar_qa_min_score
    knowledge = _nearest(session, vectors[0], "knowledge", exclude, top, floor)
    # 已经整理成工作台知识的那条回复，不再作为历史回复重复给模型
    distilled = set(session.scalars(select(KnowledgeCandidate.suggestion_id).where(
        KnowledgeCandidate.id.in_([e.candidate_id for e, _ in knowledge]))))
    replies = _nearest(session, vectors[0], "reply", exclude, top, floor, distilled)
    if exclude_same_question:
        same = question.strip()
        knowledge = [(e, x) for e, x in knowledge if e.question.strip() != same]
        replies = [(e, x) for e, x in replies if e.question.strip() != same]
    items: list[KnowledgeItem] = []
    for found, kind, prefix in ((knowledge, "workbench", "W"), (replies, "history", "H")):
        for i, (e, score) in enumerate(found, 1):
            items.append(KnowledgeItem(
                ref=f"{prefix}{i}", kind=kind, branch=e.branch or "通用", title=e.question[:120],
                source_id=str(e.candidate_id or e.suggestion_id or e.id), snippet=e.answer[:200],
                content=f"客户的问题：{e.question}\n{'审核过的答案' if kind == 'workbench' else '技术支持当时的回复'}：{e.answer}",
                author=e.author, group_name=e.group_name, score=round(score, 3)))
    return items


def backfill(limit: int = 500) -> int:
    """补上还没存向量的回复和工作台知识（如接入 embedding 之前发出的、隧道断开期间发出的、换了模型的）。"""
    from app import db

    if embedding.get_client() is None:
        return 0
    done = 0
    with db.SessionLocal() as session:
        fresh = select(QaEntry.suggestion_id).where(QaEntry.suggestion_id.is_not(None),
                                                    QaEntry.model == settings.embedding_model)
        sugs = session.scalars(
            select(Suggestion).where(Suggestion.status.in_(REPLIED), Suggestion.final_content.is_not(None),
                                     Suggestion.id.not_in(fresh))
            .order_by(Suggestion.handled_at.desc()).limit(limit)
        ).all()
        fresh_k = select(QaEntry.candidate_id).where(QaEntry.candidate_id.is_not(None),
                                                     QaEntry.model == settings.embedding_model)
        cands = session.scalars(
            select(KnowledgeCandidate).where(KnowledgeCandidate.status == CandidateStatus.APPROVED,
                                             KnowledgeCandidate.id.not_in(fresh_k))
        ).all()
        try:
            for c in cands:
                done += index_knowledge(session, c)
            for s in sugs:
                done += index_reply(session, s)
        except EmbeddingError as exc:
            log.warning("补存相似问答向量失败，下次启动再补：%s", exc)
        session.commit()
    if done:
        log.info("补存相似问答向量 %d 条", done)
    return done
