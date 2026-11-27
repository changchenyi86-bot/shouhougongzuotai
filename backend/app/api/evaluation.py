"""回复建议评测：导入题库、一键跑一轮、看各块通过率和逐题结果、和上一轮对比。

评测在后台直接走生成建议的流程，不发到任何群，不进会话，不算采纳统计（见 app/ai/evaluate.py）。
所有人能看结果；导入题目、开始和停止只有管理员能做（每轮要调几百次模型）。
"""

import json
import uuid
from collections import Counter

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai import evaluate
from app.ai.evaluate import EvalError
from app.auth import current_staff, require_admin
from app.db import get_session
from app.models import EvalCase, EvalResult, EvalRun, Staff
from app.schemas import (EvalBucket, EvalCompare, EvalImportResult, EvalResultOut, EvalRunDetail, EvalRunOut,
                         EvalSetOut, EvalStartIn)

router = APIRouter(prefix="/api/eval", dependencies=[Depends(current_staff)])

MAX_IMPORT_BYTES = 20 * 1024 * 1024


def _run(session: Session, run_id: uuid.UUID) -> EvalRun:
    run = session.get(EvalRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "没有这轮评测")
    return run


def _done(session: Session, run_id: uuid.UUID) -> int:
    return session.scalar(select(func.count()).select_from(EvalResult).where(EvalResult.run_id == run_id)) or 0


def _out(session: Session, run: EvalRun) -> EvalRunOut:
    summary = evaluate.summarize(session, run)
    return EvalRunOut.model_validate(run).model_copy(update={
        "done": _done(session, run.id), "summary": [EvalBucket(**b) for b in summary["areas"]]})


@router.get("/sets", response_model=list[EvalSetOut])
def list_sets(session: Session = Depends(get_session)) -> list[EvalSetOut]:
    cases = session.scalars(select(EvalCase).where(EvalCase.active.is_(True))).all()
    sets: dict[str, list[EvalCase]] = {}
    for c in cases:
        sets.setdefault(c.set_name, []).append(c)
    return [EvalSetOut(name=name, total=len(cs), areas=dict(Counter(c.area for c in cs)),
                       kinds=dict(Counter(c.kind for c in cs)), updated_at=max(c.updated_at for c in cs))
            for name, cs in sorted(sets.items())]


@router.post("/sets/{name}/import", response_model=EvalImportResult)
async def import_set(name: str, file: UploadFile = File(...), _: Staff = Depends(require_admin),
                     session: Session = Depends(get_session)) -> EvalImportResult:
    """导入评测集文件（.jsonl，一行一题；也接受 JSON 数组）。同编号的题覆盖，文件里没有了的题停用。"""
    name = name.strip()
    if not name or len(name) > 32:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "题库名 1～32 个字")
    raw = await file.read(MAX_IMPORT_BYTES + 1)
    if len(raw) > MAX_IMPORT_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "文件太大")
    try:
        text = raw.decode("utf-8-sig").strip()
        records = json.loads(text) if text.startswith("[") else [json.loads(ln) for ln in text.splitlines() if ln.strip()]
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"文件不是有效的 JSON Lines：{exc}") from exc
    if not all(isinstance(r, dict) for r in records):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "每一题应是一个 JSON 对象")
    try:
        counts = evaluate.import_cases(session, name, records)
    except EvalError as exc:
        session.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return EvalImportResult(name=name, **counts)


@router.get("/runs", response_model=list[EvalRunOut])
def list_runs(limit: int = 20, session: Session = Depends(get_session)) -> list[EvalRunOut]:
    runs = session.scalars(select(EvalRun).order_by(EvalRun.started_at.desc()).limit(min(max(limit, 1), 100)))
    return [_out(session, r) for r in runs]


@router.post("/runs", response_model=EvalRunOut, status_code=status.HTTP_201_CREATED)
def start_run(body: EvalStartIn, me: Staff = Depends(require_admin),
              session: Session = Depends(get_session)) -> EvalRunOut:
    try:
        run = evaluate.start(session, body.set_name, body.areas, me.name, body.note)
    except EvalError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    session.refresh(run)
    return _out(session, run)


@router.post("/runs/{run_id}/stop", response_model=EvalRunOut)
def stop_run(run_id: uuid.UUID, _: Staff = Depends(require_admin),
             session: Session = Depends(get_session)) -> EvalRunOut:
    return _out(session, evaluate.stop(session, _run(session, run_id)))


@router.get("/runs/{run_id}", response_model=EvalRunDetail)
def run_detail(run_id: uuid.UUID, session: Session = Depends(get_session)) -> EvalRunDetail:
    run = _run(session, run_id)
    summary = evaluate.summarize(session, run)
    previous = None
    prev = evaluate.previous_run(session, run)
    if prev is not None:
        now, before = evaluate.passed_map(session, run.id), evaluate.passed_map(session, prev.id)
        both = now.keys() & before.keys()
        previous = EvalCompare(
            run_id=prev.id, started_at=prev.started_at,
            summary=[EvalBucket(**b) for b in evaluate.summarize(session, prev)["areas"]],
            better=sum(1 for k in both if now[k] and not before[k]),
            worse=sum(1 for k in both if before[k] and not now[k]))
    return EvalRunDetail.model_validate(run).model_copy(update={
        "done": _done(session, run.id), "summary": [EvalBucket(**b) for b in summary["areas"]],
        "kinds": [EvalBucket(**b) for b in summary["kinds"]], "previous": previous})


@router.get("/runs/{run_id}/results", response_model=list[EvalResultOut])
def run_results(run_id: uuid.UUID, session: Session = Depends(get_session)) -> list[EvalResultOut]:
    """这一轮的逐题结果，按题目顺序；还没跑到的题也列出来（done=false）。"""
    run = _run(session, run_id)
    results = {r.case_id: r for r in session.scalars(select(EvalResult).where(EvalResult.run_id == run.id))}
    cases = evaluate.selected_cases(session, run)
    listed = {c.id for c in cases}
    extra = results.keys() - listed  # 跑过之后被停用的题
    if extra:
        cases += list(session.scalars(select(EvalCase).where(EvalCase.id.in_(extra)).order_by(EvalCase.sort)))
    prev = evaluate.previous_run(session, run)
    before = evaluate.passed_map(session, prev.id) if prev else {}
    out = []
    for c in cases:
        r = results.get(c.id)
        fields = {}
        if r is not None:
            fields = {k: getattr(r, k) for k in ("error", "reply", "can_answer", "confidence", "needs_reply", "reason",
                                                  "knowledge", "knowledge_error", "retrieval_rank", "verdict",
                                                  "verdict_reason", "passed", "seconds")}
        out.append(EvalResultOut(
            case_id=c.id, code=c.code, kind=c.kind, area=c.area, branch=c.branch, question=c.question, understood_question=r.question if r else None,
            context=c.context, expected=c.expected, must_not=c.must_not, reference=c.reference,
            expect_reply=c.expect_reply, data=c.data, done=r is not None, has_expected_docs=bool(c.expected_keys),
            previous_passed=before.get(c.id), **fields))
    return out
