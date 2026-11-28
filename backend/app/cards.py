"""审核通过的工作台知识转成售后数字人的 BI 纠错卡。

字段和售后数字人卡库一致（见数字人自建实例的卡片说明）。卡片编号以 bi-verified-wb- 开头，和技术支持在
运营看板里审核的卡区分开。有数和通用的知识写成 BI 卡；知数、数据中台暂不支持，只在工作台里用。
写入卡库由本机的同步脚本 scripts/sync_digital_human_cards.py 完成：取这里的卡片，和服务器上最新的卡库合并后上传。
"""

from datetime import datetime, timedelta, timezone

from app.models import CandidateStatus, KnowledgeCandidate

CARD_PREFIX = "bi-verified-wb-"
BEIJING = timezone(timedelta(hours=8))


def card_id_for(cand: KnowledgeCandidate) -> str | None:
    """有数、通用的知识写成 BI 纠错卡；知数、数据中台的卡库暂不支持，只在工作台里用。"""
    if cand.branch in ("知数", "数据中台"):
        return None
    return CARD_PREFIX + cand.id.hex[:12]


def _date(dt: datetime | None) -> datetime:
    dt = dt or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(BEIJING)


def to_card(cand: KnowledgeCandidate) -> dict | None:
    if not cand.card_id or cand.status not in (CandidateStatus.APPROVED, CandidateStatus.RETIRED):
        return None
    reviewed = _date(cand.reviewed_at)
    phrasings = [cand.customer_question.strip()] if cand.customer_question.strip() not in ("", cand.question.strip()) else []
    where = f"客户群「{cand.group_name}」" if cand.group_name else "客户群"
    return {
        "card_id": cand.card_id,
        "product": "bi",
        "module": cand.module or "",
        "kind": cand.kind,
        "title": cand.title or cand.question[:80],
        "question": cand.question,
        "phrasings": phrasings,
        "answer": cand.answer,
        "internal_note": "",
        "not_applicable": "",
        "must_not": [],
        "sources": [{
            "type": "workbench",
            "ref": f"售后工作台候选知识 {cand.id}",
            "title": cand.title or cand.question[:80],
            "note": f"{where}里 {cand.author or '技术支持'} 的回复整理，{cand.reviewer} 在工作台审核通过",
        }],
        "deprecate_docs": [],
        "versions": "",
        "visibility": "public",
        "gaps": [],
        "status": "confirmed" if cand.status == CandidateStatus.APPROVED else "retired",
        "confidence": "medium",
        "author": cand.author or "",
        "reviewer": cand.reviewer or "",
        "created": f"{reviewed:%Y-%m-%d}",
        "review_by": f"{reviewed + timedelta(days=180):%Y-%m-%d}",
    }


def in_library(cand: KnowledgeCandidate) -> bool:
    """要放进卡库的：审核通过的，以及写入过、后来停用的（以停用状态保留，数字人不再用它）。"""
    if not cand.card_id:
        return False
    return cand.status == CandidateStatus.APPROVED or (
        cand.status == CandidateStatus.RETIRED and cand.synced_status is not None)


def needs_sync(cand: KnowledgeCandidate) -> bool:
    """卡库里的版本和工作台不一致：通过了还没写入，或写入后又停用。"""
    return in_library(cand) and cand.synced_status != to_card(cand)["status"]
