"""同事的个人微信名单（管理员维护）。见 app/colleagues.py。"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import auth, colleagues
from app.db import get_session
from app.models import Channel, ColleagueAccount, Message, Staff
from app.schemas import ColleagueAccountIn, ColleagueAccountOut, ColleagueAdded, ColleagueCandidate

router = APIRouter(prefix="/api/colleague-accounts", dependencies=[Depends(auth.require_admin)])


@router.get("", response_model=list[ColleagueAccountOut])
def list_accounts(session: Session = Depends(get_session)) -> list[ColleagueAccount]:
    return session.query(ColleagueAccount).order_by(ColleagueAccount.created_at).all()


@router.get("/candidates", response_model=list[ColleagueCandidate])
def list_candidates(session: Session = Depends(get_session)) -> list[dict]:
    return colleagues.candidates(session)


@router.post("", response_model=ColleagueAdded, status_code=status.HTTP_201_CREATED)
def add_account(body: ColleagueAccountIn, staff: Staff = Depends(auth.require_admin),
                session: Session = Depends(get_session)) -> ColleagueAdded:
    sender_id = body.sender_id.strip()
    acc = session.get(ColleagueAccount, (body.channel, sender_id))
    if acc is None:
        name = body.name.strip() or session.query(Message.sender_name).filter(
            Message.channel == body.channel, Message.sender_id == sender_id, Message.sender_name.is_not(None)
        ).order_by(Message.sent_at.desc()).limit(1).scalar() or ""
        acc = ColleagueAccount(channel=body.channel, sender_id=sender_id, name=name[:128], added_by=staff.name)
        session.add(acc)
    closed = colleagues.close_open_suggestions(session, body.channel, sender_id, staff.name)
    session.commit()
    colleagues.refresh()
    return ColleagueAdded(account=ColleagueAccountOut.model_validate(acc), closed=closed)


@router.delete("/{channel}/{sender_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_account(channel: Channel, sender_id: str, session: Session = Depends(get_session)) -> None:
    acc = session.get(ColleagueAccount, (channel, sender_id))
    if acc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "名单里没有这个账号")
    session.delete(acc)
    session.commit()
    colleagues.refresh()
