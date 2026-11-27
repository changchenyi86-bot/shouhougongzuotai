"""模型用量：工作台自己调用模型消耗的 token，按天（北京时间）、用途、模型、线上 / 评测分开统计。只给管理员看。"""

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.usage import PURPOSES
from app.auth import require_admin
from app.db import get_session
from app.models import ModelPrice, Staff, TokenUsage

router = APIRouter(prefix="/api/usage", dependencies=[Depends(require_admin)])

BEIJING = timezone(timedelta(hours=8))


class Totals(BaseModel):
    calls: int = 0
    failed: int = 0
    estimated: int = Field(0, description="接口没返回用量、按字数估（或缺失）的调用次数")
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    total_tokens: int = 0
    cost: float | None = Field(None, description="估算费用（元）；用到的模型都没填单价时为空")
    unpriced_tokens: int = Field(0, description="没填单价的模型用掉的 token，不在 cost 里")


class PurposeRow(BaseModel):
    key: str
    label: str
    online: Totals
    eval: Totals


class ModelRow(BaseModel):
    model: str
    priced: bool
    totals: Totals


class GroupRow(BaseModel):
    group_name: str
    totals: Totals


class DayRow(BaseModel):
    date: date
    online: Totals
    eval: Totals


class Price(BaseModel):
    model: str
    input_price: float = Field(ge=0, description="元 / 百万 token")
    output_price: float = Field(ge=0)
    cached_price: float | None = Field(None, ge=0, description="命中缓存的输入单价；空表示按输入单价")
    updated_by: str | None = None
    updated_at: datetime | None = None


class PriceIn(BaseModel):
    input_price: float = Field(ge=0)
    output_price: float = Field(ge=0)
    cached_price: float | None = Field(None, ge=0)


class UsageSummary(BaseModel):
    day: date
    today: date
    online: Totals
    eval: Totals
    purposes: list[PurposeRow]
    models: list[ModelRow]
    groups: list[GroupRow]
    days: list[DayRow]
    prices: list[Price]
    tracking_since: datetime | None = Field(None, description="最早一条记录的时间；之前的调用没有统计")


def _aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _day(dt: datetime) -> date:
    return _aware(dt).astimezone(BEIJING).date()


class _Acc:
    """边加边算费用。"""

    def __init__(self, prices: dict[str, ModelPrice]):
        self.prices, self.t = prices, Totals()
        self._cost = 0.0
        self._priced = False

    def add(self, r: TokenUsage) -> None:
        t = self.t
        t.calls += 1
        t.failed += 0 if r.ok else 1
        t.estimated += 1 if r.estimated else 0
        t.input_tokens += r.input_tokens
        t.output_tokens += r.output_tokens
        t.cached_tokens += r.cached_tokens
        t.total_tokens += r.input_tokens + r.output_tokens
        p = self.prices.get(r.model)
        if p is None:
            t.unpriced_tokens += r.input_tokens + r.output_tokens
            return
        cached = min(r.cached_tokens, r.input_tokens)
        cached_price = p.cached_price if p.cached_price is not None else p.input_price
        self._cost += ((r.input_tokens - cached) * p.input_price + cached * cached_price
                       + r.output_tokens * p.output_price) / 1_000_000
        self._priced = True

    def out(self) -> Totals:
        self.t.cost = round(self._cost, 4) if self._priced else None
        return self.t


def _price_out(p: ModelPrice) -> Price:
    return Price(model=p.model, input_price=p.input_price, output_price=p.output_price, cached_price=p.cached_price,
                 updated_by=p.updated_by, updated_at=p.updated_at)


@router.get("", response_model=UsageSummary)
def summary(days: int = Query(7, ge=1, le=60), day: date | None = Query(None, description="看哪一天的明细，默认今天"),
            session: Session = Depends(get_session)) -> UsageSummary:
    today = datetime.now(BEIJING).date()
    day = day or today
    first = min(today - timedelta(days=days - 1), day)
    since = datetime.combine(first, datetime.min.time(), BEIJING).astimezone(timezone.utc)
    prices = {p.model: p for p in session.scalars(select(ModelPrice))}
    acc = lambda: _Acc(prices)  # noqa: E731

    online, evaluating = acc(), acc()
    purposes: dict[tuple[str, bool], _Acc] = defaultdict(acc)
    models: dict[str, _Acc] = defaultdict(acc)
    groups: dict[str, _Acc] = defaultdict(acc)
    per_day: dict[tuple[date, bool], _Acc] = defaultdict(acc)

    rows = session.scalars(select(TokenUsage).where(TokenUsage.created_at >= since)
                           .execution_options(yield_per=2000))
    for r in rows:
        d = _day(r.created_at)
        per_day[(d, r.is_eval)].add(r)
        if d != day:
            continue
        (evaluating if r.is_eval else online).add(r)
        purposes[(r.purpose, r.is_eval)].add(r)
        models[r.model].add(r)
        if not r.is_eval and r.group_name:
            groups[r.group_name].add(r)

    keys = [k for k in PURPOSES if (k, False) in purposes or (k, True) in purposes]
    keys += sorted({k for k, _ in purposes} - set(keys))
    purpose_rows = [PurposeRow(key=k, label=PURPOSES.get(k, k), online=purposes[(k, False)].out(),
                               eval=purposes[(k, True)].out()) for k in keys]
    purpose_rows.sort(key=lambda p: -(p.online.total_tokens + p.eval.total_tokens))
    model_rows = sorted((ModelRow(model=m, priced=m in prices, totals=a.out()) for m, a in models.items()),
                        key=lambda m: -m.totals.total_tokens)
    group_rows = sorted((GroupRow(group_name=g, totals=a.out()) for g, a in groups.items()),
                        key=lambda g: -g.totals.total_tokens)[:10]
    day_rows = []
    for i in range(days):
        d = today - timedelta(days=days - 1 - i)
        day_rows.append(DayRow(date=d, online=per_day[(d, False)].out() if (d, False) in per_day else Totals(),
                               eval=per_day[(d, True)].out() if (d, True) in per_day else Totals()))
    first_row = session.scalar(select(TokenUsage.created_at).order_by(TokenUsage.created_at).limit(1))
    # 用过但没填单价的模型也列出来，方便直接填
    used = set(models) | {m for m in session.scalars(select(TokenUsage.model).where(
        TokenUsage.created_at >= since).distinct())}
    price_rows = [_price_out(prices[m]) if m in prices else Price(model=m, input_price=0, output_price=0)
                  for m in sorted(used | set(prices))]
    return UsageSummary(day=day, today=today, online=online.out(), eval=evaluating.out(), purposes=purpose_rows,
                        models=model_rows, groups=group_rows, days=day_rows, prices=price_rows,
                        tracking_since=_aware(first_row) if first_row else None)


@router.put("/prices/{model:path}", response_model=Price)
def set_price(model: str, body: PriceIn, staff: Staff = Depends(require_admin),
              session: Session = Depends(get_session)) -> Price:
    model = model.strip()
    if not model or len(model) > 128:
        raise HTTPException(400, "模型名不对")
    p = session.get(ModelPrice, model) or ModelPrice(model=model)
    p.input_price, p.output_price, p.cached_price = body.input_price, body.output_price, body.cached_price
    p.updated_by = staff.name
    session.add(p)
    session.commit()
    return _price_out(p)


@router.delete("/prices/{model:path}", status_code=204)
def clear_price(model: str, session: Session = Depends(get_session)) -> None:
    p = session.get(ModelPrice, model)
    if p is not None:
        session.delete(p)
        session.commit()
