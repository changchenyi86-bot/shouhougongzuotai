"""容器内运行：python -m app.import_candidates 文件.jsonl [--by 真实回复复盘] [--dry-run]

把线下整理好的候选知识草稿（如从群里技术支持的真实回复整理出的）导进知识库「待审核」，由技术支持审核通过才生效。

每行一条 JSON，字段同 knowledge_candidates：title / question / answer / branch / kind / module / reason /
customer_question / customer_reply / ai_suggestion / group_name / author，外加 suggestion_id（这条问答对应的那一轮建议）。
同一轮已经有候选知识的跳过；suggestion_id 不存在的跳过。导入的记录确认人写成 --by 的名字，所以任何技术支持都能审核。
"""

import argparse
import json
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app import db
from app.models import CandidateStatus, KnowledgeCandidate, Suggestion

BRANCHES = ("有数", "知数", "数据中台", "通用")
KINDS = ("how_to", "fact", "troubleshoot", "not_supported")


def _text(v, size: int | None = None) -> str:
    s = "" if v is None else str(v).strip()
    return s[:size] if size else s


def build(row: dict, by: str, now: datetime) -> KnowledgeCandidate:
    question, answer = _text(row.get("question")), _text(row.get("answer"))
    if not question or not answer:
        raise ValueError("问题和答案都要有")
    branch = _text(row.get("branch"))
    if "中台" in branch:
        branch = "数据中台"
    return KnowledgeCandidate(
        suggestion_id=uuid.UUID(str(row["suggestion_id"])), status=CandidateStatus.PENDING,
        title=_text(row.get("title"), 256) or question[:80], question=question, answer=answer,
        branch=branch if branch in BRANCHES else "通用",
        kind=row.get("kind") if row.get("kind") in KINDS else "how_to",
        module=_text(row.get("module"), 64), reason=_text(row.get("reason")) or None,
        customer_question=_text(row.get("customer_question")), customer_reply=_text(row.get("customer_reply")),
        ai_suggestion=_text(row.get("ai_suggestion")) or None, group_name=_text(row.get("group_name"), 256) or None,
        author=_text(row.get("author"), 64) or None, confirmed_by=by, confirmed_at=now,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="导入候选知识草稿到「待审核」")
    parser.add_argument("file")
    parser.add_argument("--by", default="真实回复复盘", help="记为确认人的名字（不要用会审核的同事的名字）")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    lines = [ln for ln in Path(args.file).read_text(encoding="utf-8").splitlines() if ln.strip()]
    now, counts = datetime.now(timezone.utc), Counter()
    with db.SessionLocal() as session:
        for i, line in enumerate(lines, 1):
            try:
                cand = build(json.loads(line), args.by, now)
            except (ValueError, KeyError, json.JSONDecodeError) as exc:
                print(f"第 {i} 行跳过：{exc}")
                counts["bad"] += 1
                continue
            if session.get(Suggestion, cand.suggestion_id) is None:
                print(f"第 {i} 行跳过：对应的建议不存在")
                counts["missing"] += 1
                continue
            if session.scalar(select(KnowledgeCandidate.id).where(KnowledgeCandidate.suggestion_id == cand.suggestion_id)):
                counts["exists"] += 1
                continue
            session.add(cand)
            session.flush()
            counts[cand.branch] += 1
        if args.dry_run:
            session.rollback()
        else:
            session.commit()
    added = sum(v for k, v in counts.items() if k in BRANCHES)
    print(("试运行，未写入。" if args.dry_run else "") + f"导入 {added} 条（"
          + "，".join(f"{b} {counts[b]}" for b in BRANCHES if counts[b]) + f"）；已有候选知识跳过 {counts['exists']}，"
          f"建议不存在 {counts['missing']}，格式不对 {counts['bad']}")


if __name__ == "__main__":
    main()
