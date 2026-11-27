"""容器内运行：python -m app.extract_media --since 2026-11-20 [--limit N] [--retry-failed] [--dry-run]。"""

import argparse
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, or_, select

from app import db
from app.ai import media
from app.models import Attachment, Message


def main() -> None:
    parser = argparse.ArgumentParser(description='补充识别客户历史附件')
    parser.add_argument('--since', required=True, help='起始日期，格式 YYYY-MM-DD')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--retry-failed', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    try:
        since = datetime.strptime(args.since, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    except ValueError:
        parser.error('--since 请使用 YYYY-MM-DD 格式')
    if args.limit is not None and args.limit <= 0:
        parser.error('--limit 必须大于零')
    with db.SessionLocal() as session:
        states = ['pending'] + (['failed'] if args.retry_failed else [])
        query = (select(Attachment).join(Message, Message.attachment_id == Attachment.id)
                 .where(Attachment.source == 'channel', Message.sent_at >= since,
                        or_(Attachment.extract_status.is_(None), Attachment.extract_status.in_(states)))
                 .group_by(Attachment.id).order_by(func.min(Message.sent_at), Attachment.id))
        if args.limit:
            query = query.limit(args.limit)
        items = list(session.scalars(query).unique())
        if args.dry_run:
            counts = Counter(Path(a.filename).suffix.lower() or '无扩展名' for a in items)
            print(f'待处理 {len(items)} 个：' + '，'.join(f'{k}: {v}' for k, v in sorted(counts.items())))
            return
        counts = Counter()
        for i, attachment in enumerate(items, 1):
            media.extract(session, attachment, force=args.retry_failed and attachment.extract_status == 'failed')
            session.refresh(attachment)
            counts[attachment.extract_status] += 1
            print(f'[{i}/{len(items)}] {attachment.filename}: {attachment.extract_status}')
        print('完成：' + '，'.join(f'{status} {counts[status]}' for status in ('done', 'failed', 'skipped')))


if __name__ == '__main__':
    main()
