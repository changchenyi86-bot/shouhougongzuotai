"""客户档案：会话页右边的档案卡。同步来的资料只读；群对应哪家客户、团队补充、资料有误可以在工作台里改。"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import customers, routing
from app.ai import worker
from app.auth import current_staff, require_admin
from app.db import get_session
from app.models import Channel, CustomerGroupLink, CustomerNote, CustomerProfile, Message, Role, ServiceRecord, Staff
from app.schemas import (CustomerBrief, CustomerCardOut, CustomerLinkIn, CustomerLinkOut, CustomerNoteEdit,
                         CustomerNoteIn, CustomerNoteOut, CustomerProfileOut, CustomerSyncOut, ServiceRecordOut)

router = APIRouter(prefix="/api/customers", dependencies=[Depends(current_staff)])

RECENT_RECORDS = 3


def _ensure_group(session: Session, channel: Channel, group_id: str) -> None:
    exists = session.scalar(select(Message.id).where(Message.channel == channel, Message.group_id == group_id).limit(1))
    if exists is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "没有这个群的消息")


def _can_link(session: Session, staff: Staff, channel: Channel, group_id: str) -> bool:
    if staff.role == Role.ADMIN:
        return True
    owner = routing.owner_of(session, channel, group_id)
    return owner is not None and owner.id == staff.id


@router.get("/card", response_model=CustomerCardOut)
def card(channel: Channel, group_id: str, staff: Staff = Depends(current_staff),
         session: Session = Depends(get_session)) -> CustomerCardOut:
    """一个群的客户档案：对应哪家客户、同步来的资料、内部提醒、团队补充、最近服务记录。"""
    routing.ensure_access(session, staff, channel, group_id)
    link = customers.link_of(session, channel, group_id)
    customer = link.customer if link else None
    profile = session.get(CustomerProfile, customer) if customer else None
    name = customers.group_name(session, channel, group_id)
    return CustomerCardOut(
        group_name=name,
        link=CustomerLinkOut(customer=customer, source=link.source if link else None,
                             candidates=(link.candidates or []) if link else [],
                             updated_by=link.updated_by if link else None),
        profile=CustomerProfileOut.model_validate(profile) if profile else None,
        alerts=customers.alerts(profile, customers.group_lines(name)),
        notes=[CustomerNoteOut.model_validate(n) for n in customers.notes_for(session, channel, group_id, customer)],
        records=[ServiceRecordOut.model_validate(r)
                 for r in customers.recent_records(session, channel, group_id, RECENT_RECORDS)],
        record_total=customers.record_count(session, channel, group_id),
        sync=CustomerSyncOut(**customers.sync_status(session)),
        can_link=_can_link(session, staff, channel, group_id),
    )


@router.get("/records", response_model=list[ServiceRecordOut])
def records(channel: Channel, group_id: str, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
            staff: Staff = Depends(current_staff), session: Session = Depends(get_session)) -> list[ServiceRecordOut]:
    """这个群的服务记录，新的在前。"""
    routing.ensure_access(session, staff, channel, group_id)
    if channel != Channel.WECOM:
        return []
    rows = session.scalars(
        select(ServiceRecord).where(ServiceRecord.chat_id == group_id)
        .order_by(ServiceRecord.served_on.desc(), ServiceRecord.id.desc()).offset(offset).limit(limit))
    return [ServiceRecordOut.model_validate(r) for r in rows]


@router.get("/search", response_model=list[CustomerBrief])
def search(q: str = Query(min_length=1, max_length=50), session: Session = Depends(get_session)) -> list[CustomerBrief]:
    """按客户简称或签约公司找客户，给群选对应客户用。"""
    q = q.strip()
    rows = session.scalars(select(CustomerProfile).order_by(CustomerProfile.name)).all()
    hits = [p for p in rows if q in p.name] + [p for p in rows if q not in p.name and any(q in c for c in p.companies or [])]
    return [CustomerBrief(name=p.name, companies=p.companies or []) for p in hits[:20]]


@router.put("/link", response_model=CustomerLinkOut)
def set_link(body: CustomerLinkIn, staff: Staff = Depends(current_staff),
             session: Session = Depends(get_session)) -> CustomerLinkOut:
    """人工指定群对应的客户（管理员、群负责人）。人工选的以后同步不会覆盖。"""
    _ensure_group(session, body.channel, body.group_id)
    if not _can_link(session, staff, body.channel, body.group_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "只有管理员和群负责人能改这个群对应的客户")
    if body.customer is not None and session.get(CustomerProfile, body.customer) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "售后系统里没有这家客户")
    row = session.get(CustomerGroupLink, (body.channel, body.group_id))
    if row is None:
        row = CustomerGroupLink(channel=body.channel, group_id=body.group_id)
        session.add(row)
    row.customer, row.source, row.candidates, row.updated_by = body.customer, "manual", None, staff.name
    # 没对上客户时写在这个群上的补充，归到选定的客户
    if body.customer:
        session.execute(update(CustomerNote).where(
            CustomerNote.channel == body.channel, CustomerNote.group_id == body.group_id,
            CustomerNote.customer.is_(None)).values(customer=body.customer))
    session.commit()
    return CustomerLinkOut(customer=row.customer, source=row.source, candidates=[], updated_by=row.updated_by)


@router.post("/notes", response_model=CustomerNoteOut, status_code=status.HTTP_201_CREATED)
def add_note(body: CustomerNoteIn, staff: Staff = Depends(current_staff),
             session: Session = Depends(get_session)) -> CustomerNoteOut:
    """写一条团队补充，或报一条资料有误。能看这个群的人都能写。"""
    routing.ensure_access(session, staff, body.channel, body.group_id)
    _ensure_group(session, body.channel, body.group_id)
    link = customers.link_of(session, body.channel, body.group_id)
    note = CustomerNote(customer=link.customer if link else None, channel=body.channel, group_id=body.group_id,
                        kind=body.kind, content=body.content, author=staff.name, author_id=staff.id)
    session.add(note)
    session.commit()
    return CustomerNoteOut.model_validate(note)


def _own_note(session: Session, staff: Staff, note_id: uuid.UUID) -> CustomerNote:
    note = session.get(CustomerNote, note_id)
    if note is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "这条补充不存在")
    if staff.role != Role.ADMIN and note.author_id != staff.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "只能改自己写的；管理员都能改")
    return note


@router.patch("/notes/{note_id}", response_model=CustomerNoteOut)
def edit_note(note_id: uuid.UUID, body: CustomerNoteEdit, staff: Staff = Depends(current_staff),
              session: Session = Depends(get_session)) -> CustomerNoteOut:
    note = _own_note(session, staff, note_id)
    note.content = body.content
    session.commit()
    return CustomerNoteOut.model_validate(note)


@router.delete("/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_note(note_id: uuid.UUID, staff: Staff = Depends(current_staff), session: Session = Depends(get_session)) -> None:
    """删掉补充；资料有误的，管理员去源头改好后在这里删掉。"""
    session.delete(_own_note(session, staff, note_id))
    session.commit()


@router.post("/sync", status_code=status.HTTP_202_ACCEPTED)
def sync_now(_: Staff = Depends(require_admin)) -> dict:
    """马上同步一次（管理员）。在后台跑，档案卡底部的同步时间会更新。"""
    worker.submit(customers.sync_once)
    return {"status": "started"}
