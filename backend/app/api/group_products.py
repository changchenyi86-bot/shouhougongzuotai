"""群产品：每个群查哪个产品的知识（有数 / 知数 / 数据中台）。管理员可以给群指定，优先于群名和 AI 判断。"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai import products
from app.auth import current_staff, require_admin
from app.db import get_session
from app.models import CustomerGroupLink, CustomerProfile, GroupName, GroupOwner, GroupProduct, Message, Staff
from app.schemas import GroupProductIn, GroupProductOut

router = APIRouter(prefix="/api", dependencies=[Depends(current_staff)])


@router.get("/groups/products", response_model=list[GroupProductOut])
def list_group_products(_: Staff = Depends(require_admin),
                        session: Session = Depends(get_session)) -> list[GroupProductOut]:
    """所有外部群（测试群除外）的产品设置，要指定的（群名一个产品都不带）在前，并给出推荐值和依据。

    只给管理员：列的是全部群，技术支持只能看自己负责的群（见 routing）。"""
    latest = (select(Message.channel, Message.group_id, func.max(Message.sent_at).label("last"))
              .where(Message.is_external_group).group_by(Message.channel, Message.group_id))
    names = {(g.channel, g.group_id): g.name for g in session.scalars(select(GroupName))}
    manual = {(g.channel, g.group_id): g for g in session.scalars(select(GroupProduct))}
    owners = {(o.channel, o.group_id): o.owner for o in session.scalars(select(GroupOwner))}
    links = {(c.channel, c.group_id): c.customer for c in session.scalars(select(CustomerGroupLink))}
    purchased = {p.name: p.purchased for p in session.scalars(select(CustomerProfile))}
    stats = products.ai_stats(session)
    out = []
    for ch, gid, last in session.execute(latest):
        key = (ch, gid)
        name = names.get(key) or session.scalar(
            select(Message.group_name).where(Message.channel == ch, Message.group_id == gid,
                                             Message.group_name.is_not(None), Message.group_name != "")
            .order_by(Message.sent_at.desc()).limit(1))
        if name and ("非真实群" in name or name.startswith("【模拟】")):
            continue
        marks = products.by_name(name)
        row = manual.get(key)
        owner: Staff | None = owners.get(key)
        team = owner.team.name if owner and owner.team else None
        customer = links.get(key)
        rec, basis = products.recommend(marks, purchased.get(customer), team, stats.get(key))
        effective = "manual" if row and row.products else ("group" if len(marks) == 1 else "ai")
        out.append(GroupProductOut(
            channel=ch, group_id=gid, group_name=name, by_name=marks, manual=row.products if row else None,
            effective=effective, needs_setting=not marks, recommend=rec, recommend_basis=basis,
            ai_stats=dict(stats.get(key) or {}), owner=owner.name if owner else None, owner_team=team,
            customer=customer, last_message_at=last, updated_by=row.updated_by if row else None))
    out.sort(key=lambda g: (not g.needs_setting, g.effective != "ai",
                            -(g.last_message_at.timestamp() if g.last_message_at else 0)))
    return out


@router.put("/groups/product", status_code=status.HTTP_204_NO_CONTENT)
def set_group_product(body: GroupProductIn, staff: Staff = Depends(require_admin),
                      session: Session = Depends(get_session)) -> None:
    """管理员给群指定产品；products 为空表示取消指定（回到按群名或 AI 判断）。"""
    exists = session.scalar(
        select(Message.id).where(Message.channel == body.channel, Message.group_id == body.group_id).limit(1))
    if exists is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "没有这个群的消息")
    row = session.get(GroupProduct, (body.channel, body.group_id))
    codes = products.normalize(body.products)
    if not codes:
        if row:
            session.delete(row)
    else:
        if row is None:
            row = GroupProduct(channel=body.channel, group_id=body.group_id, products=codes)
            session.add(row)
        row.products, row.updated_by = codes, staff.name
    session.commit()
