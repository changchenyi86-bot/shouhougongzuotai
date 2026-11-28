"""候选知识：AI 建议沉淀 → 回复的人确认 → 其他技术支持审核通过 → 工作台生成建议时用，并写入售后数字人。

待补知识：AI 没答上的问题（客服大改或没用 AI 建议、转给同事求助），补知识、提高命中率时从这里看缺什么。
"""

import hmac
import logging
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import cards
from app.ai import similar, worker
from app.ai.embedding import EmbeddingError
from app.auth import current_staff
from app.config import settings
from app.db import get_session
from app.models import CandidateStatus, KnowledgeCandidate, Message, Staff, Suggestion, SuggestionStatus
from app.schemas import (CandidateBrief, CandidateCounts, CandidateEdit, CandidateOut, CardsOut, CardsSyncedIn, GapOut,
                         GapsOut)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", dependencies=[Depends(current_staff)])

C = CandidateStatus
VIEWS: dict[str, list[CandidateStatus]] = {
    "proposed": [C.PROPOSED],
    "pending": [C.PENDING],
    "approved": [C.APPROVED],
    "rejected": [C.REJECTED, C.RETIRED],
    "skipped": [C.NOT_GENERAL, C.DECLINED],
}


def _out(session: Session, cand: KnowledgeCandidate) -> CandidateOut:
    out = CandidateOut.model_validate(cand)
    row = session.execute(
        select(Message.channel, Message.group_id, Message.id)
        .join(Suggestion, Suggestion.message_id == Message.id).where(Suggestion.id == cand.suggestion_id)
    ).first()
    if row:
        out.channel, out.group_id, out.message_id = row
    return out


def _get(session: Session, candidate_id: uuid.UUID) -> KnowledgeCandidate:
    cand = session.get(KnowledgeCandidate, candidate_id, with_for_update=True)
    if cand is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "候选知识不存在")
    return cand


def _need(cand: KnowledgeCandidate, *allowed: CandidateStatus) -> None:
    if cand.status not in allowed:
        raise HTTPException(status.HTTP_409_CONFLICT, f"这条候选知识的状态已经变了（{cand.status.value}），请刷新")


def _edit(cand: KnowledgeCandidate, body: CandidateEdit, who: str) -> None:
    changed = False
    for field in ("title", "question", "answer", "branch", "kind", "module"):
        value = getattr(body, field)
        if value is None:
            continue
        value = value.strip() if isinstance(value, str) else value
        if value != getattr(cand, field):
            setattr(cand, field, value)
            changed = True
    if changed:
        cand.updated_by = who
    if not cand.question.strip() or not cand.answer.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "问题和答案都要填写")
    if not cand.title.strip():
        cand.title = cand.question.strip()[:80]


@router.get("/candidates", response_model=list[CandidateOut])
def list_candidates(
    view: Literal["proposed", "pending", "approved", "rejected", "skipped"] = "pending",
    author: str | None = Query(None, max_length=64, description="只看这个人回复的"),
    limit: int = Query(100, ge=1, le=500),
    session: Session = Depends(get_session),
) -> list[CandidateOut]:
    stmt = select(KnowledgeCandidate).where(KnowledgeCandidate.status.in_(VIEWS[view]))
    if author:
        stmt = stmt.where(KnowledgeCandidate.author == author)
    order = KnowledgeCandidate.reviewed_at if view in ("approved", "rejected") else KnowledgeCandidate.created_at
    stmt = stmt.order_by(order.desc().nulls_last(), KnowledgeCandidate.created_at.desc()).limit(limit)
    return [_out(session, c) for c in session.scalars(stmt)]


@router.get("/candidates/counts", response_model=CandidateCounts)
def counts(staff: Staff = Depends(current_staff), session: Session = Depends(get_session)) -> CandidateCounts:
    by_status = {s.value: n for s, n in session.execute(
        select(KnowledgeCandidate.status, func.count()).group_by(KnowledgeCandidate.status))}
    me = staff.name
    mine = session.scalar(select(func.count()).where(
        KnowledgeCandidate.status == C.PROPOSED, KnowledgeCandidate.author == me)) or 0
    pending = session.scalar(select(func.count()).where(
        KnowledgeCandidate.status == C.PENDING,
        func.coalesce(KnowledgeCandidate.author, "") != me,
        func.coalesce(KnowledgeCandidate.confirmed_by, "") != me)) or 0
    return CandidateCounts(mine=mine, pending=pending, by_status=by_status)


@router.post("/candidates/{candidate_id}/confirm", response_model=CandidateOut)
def confirm(candidate_id: uuid.UUID, body: CandidateEdit, staff: Staff = Depends(current_staff),
            session: Session = Depends(get_session)) -> CandidateOut:
    """加入候选知识（可以先改问答内容），等其他技术支持审核。AI 判断不收录、或之前选了不用的，也可以改为收录。"""
    cand = _get(session, candidate_id)
    _need(cand, C.PROPOSED, C.NOT_GENERAL, C.DECLINED)
    _edit(cand, body, staff.name)
    cand.status, cand.confirmed_by, cand.confirmed_at = C.PENDING, staff.name, datetime.now(timezone.utc)
    session.commit()
    return _out(session, cand)


@router.post("/candidates/{candidate_id}/decline", response_model=CandidateOut)
def decline(candidate_id: uuid.UUID, staff: Staff = Depends(current_staff),
            session: Session = Depends(get_session)) -> CandidateOut:
    """回复的人觉得不用沉淀。"""
    cand = _get(session, candidate_id)
    _need(cand, C.PROPOSED)
    cand.status, cand.confirmed_by, cand.confirmed_at = C.DECLINED, staff.name, datetime.now(timezone.utc)
    session.commit()
    return _out(session, cand)


def _ensure_reviewer(cand: KnowledgeCandidate, who: str) -> None:
    if who in {cand.author, cand.confirmed_by}:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "不能审核自己回复或提交的知识，请其他同事审核")


def _reindex(candidate_id: uuid.UUID) -> None:
    """审核通过后存向量（之后同类问题生成建议时就能用上），停用后删掉。"""
    from app import db

    with db.SessionLocal() as session:
        cand = session.get(KnowledgeCandidate, candidate_id)
        if cand is None:
            return
        try:
            similar.index_knowledge(session, cand)
            session.commit()
        except EmbeddingError as exc:
            session.rollback()
            log.warning("工作台知识存向量失败 candidate=%s：%s（下次启动时补存）", candidate_id, exc)


@router.post("/candidates/{candidate_id}/approve", response_model=CandidateOut)
def approve(candidate_id: uuid.UUID, body: CandidateEdit, staff: Staff = Depends(current_staff),
            session: Session = Depends(get_session)) -> CandidateOut:
    """审核通过：工作台之后生成建议时会用；有数和通用的写入售后数字人（BI 纠错卡），知数、数据中台暂时只在工作台用。"""
    cand = _get(session, candidate_id)
    _need(cand, C.PENDING)
    _ensure_reviewer(cand, staff.name)
    _edit(cand, body, staff.name)
    cand.status, cand.reviewer, cand.reviewed_at = C.APPROVED, staff.name, datetime.now(timezone.utc)
    cand.review_note = (body.note or "").strip() or None
    cand.card_id = cards.card_id_for(cand)
    session.commit()
    worker.submit(_reindex, cand.id)
    return _out(session, cand)


@router.post("/candidates/{candidate_id}/reject", response_model=CandidateOut)
def reject(candidate_id: uuid.UUID, body: CandidateEdit, staff: Staff = Depends(current_staff),
           session: Session = Depends(get_session)) -> CandidateOut:
    cand = _get(session, candidate_id)
    _need(cand, C.PENDING)
    _ensure_reviewer(cand, staff.name)
    cand.status, cand.reviewer, cand.reviewed_at = C.REJECTED, staff.name, datetime.now(timezone.utc)
    cand.review_note = (body.note or "").strip() or None
    session.commit()
    return _out(session, cand)


@router.post("/candidates/{candidate_id}/retire", response_model=CandidateOut)
def retire(candidate_id: uuid.UUID, body: CandidateEdit, staff: Staff = Depends(current_staff),
           session: Session = Depends(get_session)) -> CandidateOut:
    """停用审核通过的知识（答案过时或有误）：工作台不再用，售后数字人里的卡同步为停用。"""
    cand = _get(session, candidate_id)
    _need(cand, C.APPROVED)
    cand.status, cand.updated_by = C.RETIRED, staff.name
    cand.review_note = (body.note or "").strip() or cand.review_note
    session.commit()
    worker.submit(_reindex, cand.id)
    return _out(session, cand)


# ---------- 待补知识 ----------


def _gap_state(sug: Suggestion) -> str:
    cand = sug.candidate
    if cand is not None and cand.status == C.APPROVED:
        return "filled"
    if sug.status == SuggestionStatus.DISMISSED or (cand is not None and cand.status in (C.NOT_GENERAL, C.DECLINED)):
        return "skipped"
    return "todo"


def _gap(sug: Suggestion, message: Message, texts: dict[uuid.UUID, str], state: str) -> GapOut:
    ids = [uuid.UUID(v) for v in (sug.covers or [str(message.id)])]
    knowledge = sug.knowledge or []
    return GapOut(
        suggestion_id=sug.id, message_id=message.id, channel=message.channel, group_id=message.group_id,
        group_name=message.group_name,
        customer_text="\n".join(texts[i] for i in ids if i in texts) or message.content,
        question=sug.question, ai_suggestion=sug.content, can_answer=sug.can_answer, ai_reason=sug.reason,
        knowledge_found=len(knowledge), knowledge_cited=sum(bool(k.get("cited")) for k in knowledge),
        knowledge_problem=sug.knowledge_error or sug.knowledge_note,
        status=sug.status, final_content=sug.final_content, handled_by=sug.handled_by, note=sug.note,
        missed=sug.missed, missed_by=sug.missed_by, missed_at=sug.missed_at,
        candidate=CandidateBrief.model_validate(sug.candidate) if sug.candidate else None, state=state,
    )


@router.get("/gaps", response_model=GapsOut)
def list_gaps(
    view: Literal["todo", "filled", "skipped", "all"] = "todo",
    limit: int = Query(100, ge=1, le=500),
    session: Session = Depends(get_session),
) -> GapsOut:
    """AI 没答上的问题，最近的在前。整理成的知识审核通过算已补上；个案、无需回复的算不用补。"""
    rows = session.execute(
        select(Suggestion, Message).join(Message, Message.id == Suggestion.message_id)
        .where(Suggestion.missed.is_not(None), Suggestion.status != SuggestionStatus.MERGED)
        .order_by(Suggestion.missed_at.desc())
    ).all()
    states = [(sug, msg, _gap_state(sug)) for sug, msg in rows]
    counts = {k: sum(st == k for *_, st in states) for k in ("todo", "filled", "skipped")}
    picked = [(s, m, st) for s, m, st in states if view == "all" or st == view][:limit]
    ids = {uuid.UUID(v) for s, m, _ in picked for v in (s.covers or [])}
    texts = {i: c for i, c in session.execute(select(Message.id, Message.content).where(Message.id.in_(ids)))} if ids else {}
    return GapsOut(items=[_gap(s, m, texts, st) for s, m, st in picked], counts=counts)


# ---------- 写入售后数字人：本机同步脚本用，凭 CARDS_SYNC_TOKEN 访问（不用登录） ----------


def sync_token(authorization: str | None = Header(default=None)) -> None:
    expected = settings.cards_sync_token
    if not expected:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "没有配置 CARDS_SYNC_TOKEN，不能同步纠错卡")
    if not hmac.compare_digest(authorization or "", f"Bearer {expected}"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "同步口令不对")


sync_router = APIRouter(prefix="/api/digital-human", dependencies=[Depends(sync_token)])


@sync_router.get("/cards", response_model=CardsOut)
def digital_human_cards(session: Session = Depends(get_session)) -> CardsOut:
    """要放进售后数字人卡库的 BI 纠错卡。"""
    rows = session.scalars(
        select(KnowledgeCandidate).where(KnowledgeCandidate.status.in_([C.APPROVED, C.RETIRED]),
                                         KnowledgeCandidate.card_id.is_not(None))
        .order_by(KnowledgeCandidate.reviewed_at)
    ).all()
    keep = [c for c in rows if cards.in_library(c)]
    return CardsOut(cards=[cards.to_card(c) for c in keep], unsynced=sum(cards.needs_sync(c) for c in keep))


@sync_router.post("/cards/synced", response_model=CardsOut)
def cards_synced(body: CardsSyncedIn, session: Session = Depends(get_session)) -> CardsOut:
    """同步脚本把卡片写进卡库后回报：记下版本，之后不再重复写。"""
    now = datetime.now(timezone.utc)
    for cand in session.scalars(select(KnowledgeCandidate).where(KnowledgeCandidate.card_id.in_(body.card_ids))):
        card = cards.to_card(cand)
        if card is None:
            continue
        cand.synced_version, cand.synced_status, cand.synced_at = body.version, card["status"], now
    session.commit()
    return digital_human_cards(session)
