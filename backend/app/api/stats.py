"""AI 建议采纳情况：按人按周统计（北京时间，周一为一周开始）。以后算积分排名也用这些数据。"""

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.auth import current_staff
from app.db import get_session
from app.models import CandidateStatus, KnowledgeCandidate, Suggestion, SuggestionStatus
from app.schemas import AdoptionRow, AdoptionStats, AdoptionWeek

router = APIRouter(prefix="/api", dependencies=[Depends(current_staff)])

BEIJING = timezone(timedelta(hours=8))
S, C = SuggestionStatus, CandidateStatus
COUNTED = ("replied", "exact", "minor", "major", "unused", "via_chat", "missed", "dismissed",
           "knowledge_submitted", "knowledge_approved", "reviews")


def _aware(dt: datetime | None) -> datetime | None:
    """SQLite 读出来的时间不带时区（存的是 UTC）。"""
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


def _week(dt: datetime) -> date:
    d = _aware(dt).astimezone(BEIJING).date()
    return d - timedelta(days=d.weekday())


def _who(name: str | None) -> str:
    return (name or "").strip() or "未填写"  # 上线登录之前处理的


@router.get("/stats/adoption", response_model=AdoptionStats)
def adoption(weeks: int = Query(8, ge=1, le=52), session: Session = Depends(get_session)) -> AdoptionStats:
    this_week = _week(datetime.now(timezone.utc))
    first = this_week - timedelta(weeks=weeks - 1)
    since = datetime.combine(first, datetime.min.time(), BEIJING).astimezone(timezone.utc)
    counts: dict[date, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(lambda: dict.fromkeys(COUNTED, 0)))

    sugs = session.scalars(select(Suggestion).where(
        Suggestion.handled_at >= since,
        Suggestion.status.in_([S.ADOPTED, S.EDITED, S.DISMISSED]),
    ))
    for s in sugs:
        row = counts[_week(s.handled_at)]["AI" if s.auto_dismissed else _who(s.handled_by)]
        if s.status in (S.ADOPTED, S.EDITED):
            row["replied"] += 1
            row[s.adoption or ("exact" if s.status == S.ADOPTED else "major")] += 1
            row["via_chat"] += s.via_chat
        else:
            row["dismissed"] += 1
    # AI 没答上：算在改写、自己写、转给同事求助的人头上（早期转人工的算转的人）
    for s in session.scalars(select(Suggestion).where(Suggestion.missed_at >= since, Suggestion.status != S.MERGED)):
        counts[_week(s.missed_at)][_who(s.missed_by)]["missed"] += 1

    cands = session.scalars(select(KnowledgeCandidate).where(or_(
        KnowledgeCandidate.confirmed_at >= since, KnowledgeCandidate.reviewed_at >= since)))
    for c in cands:
        if c.confirmed_at and _aware(c.confirmed_at) >= since and c.status not in (C.DECLINED, C.PROPOSED, C.NOT_GENERAL):
            counts[_week(c.confirmed_at)][_who(c.confirmed_by)]["knowledge_submitted"] += 1
        if c.reviewed_at and _aware(c.reviewed_at) >= since:
            counts[_week(c.reviewed_at)][_who(c.reviewer)]["reviews"] += 1
            if c.status in (C.APPROVED, C.RETIRED):
                counts[_week(c.reviewed_at)][_who(c.author)]["knowledge_approved"] += 1

    out = []
    for i in range(weeks):
        wk = this_week - timedelta(weeks=i)
        people = [AdoptionRow(operator=name, **vals) for name, vals in counts.get(wk, {}).items()]
        people.sort(key=lambda r: (r.operator in ("AI", "未填写"), -r.replied, r.operator))
        total = {k: sum(getattr(r, k) for r in people) for k in COUNTED}
        out.append(AdoptionWeek(week=wk.isoformat(), total=AdoptionRow(operator="全部", **total), people=people))
    return AdoptionStats(weeks=out)
