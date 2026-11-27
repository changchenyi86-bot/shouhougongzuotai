"""登录、成员和分组。"""

import uuid

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import auth
from app.db import get_session
from app.models import Role, Staff, Team
from app.schemas import (AuthState, LoginIn, PasswordIn, SetupIn, StaffIn, StaffOut, StaffPatch, StatusIn,
                         TeamOut)

router = APIRouter(prefix="/api")


def staff_out(s: Staff) -> StaffOut:
    return StaffOut(id=s.id, name=s.name, username=s.username, role=s.role,
                    team=TeamOut.model_validate(s.team) if s.team else None, active=s.active, status=s.status,
                    online=auth.is_online(s), last_seen=s.last_seen)


# ---------- 登录 ----------


@router.get("/auth/state", response_model=AuthState)
def auth_state(session: Session = Depends(get_session)) -> AuthState:
    return AuthState(needs_setup=auth.needs_setup(session))


@router.post("/auth/setup", response_model=StaffOut, status_code=status.HTTP_201_CREATED)
def setup(body: SetupIn, response: Response, session: Session = Depends(get_session)) -> StaffOut:
    """第一次使用：设管理员账号并直接登录。已经有账号后不能再用。"""
    if not auth.needs_setup(session):
        raise HTTPException(status.HTTP_409_CONFLICT, "已经设置过管理员了，请直接登录")
    staff = Staff(name=body.name, username=body.username, password_hash=auth.hash_password(body.password),
                  role=Role.ADMIN)
    session.add(staff)
    session.flush()
    auth.login(session, staff, response)
    session.commit()
    return staff_out(staff)


@router.post("/auth/login", response_model=StaffOut)
def login(body: LoginIn, response: Response, session: Session = Depends(get_session)) -> StaffOut:
    staff = session.scalar(select(Staff).where(Staff.username == body.username.strip()))
    if staff is None or not auth.verify_password(body.password, staff.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "账号或密码不对")
    if not staff.active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "这个账号已停用，请联系管理员")
    auth.login(session, staff, response)
    session.commit()
    return staff_out(staff)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response, wb_session: str | None = Cookie(default=None),
           session: Session = Depends(get_session)) -> None:
    auth.logout(session, wb_session, response)
    session.commit()


@router.get("/auth/me", response_model=StaffOut)
def me(staff: Staff = Depends(auth.current_staff)) -> StaffOut:
    return staff_out(staff)


@router.put("/auth/me/status", response_model=StaffOut)
def set_status(body: StatusIn, staff: Staff = Depends(auth.current_staff),
               session: Session = Depends(get_session)) -> StaffOut:
    """切换在线 / 离开。离开时不接新会话。"""
    staff.status = body.status
    session.commit()
    return staff_out(staff)


@router.put("/auth/me/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(body: PasswordIn, staff: Staff = Depends(auth.current_staff),
                    session: Session = Depends(get_session)) -> None:
    if not auth.verify_password(body.old_password, staff.password_hash):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "原密码不对")
    staff.password_hash = auth.hash_password(body.new_password)
    session.commit()


# ---------- 成员、分组 ----------


@router.get("/teams", response_model=list[TeamOut], dependencies=[Depends(auth.current_staff)])
def list_teams(session: Session = Depends(get_session)) -> list[Team]:
    return list(session.scalars(select(Team).order_by(Team.sort, Team.name)))


@router.get("/staff", response_model=list[StaffOut], dependencies=[Depends(auth.current_staff)])
def list_staff(session: Session = Depends(get_session)) -> list[StaffOut]:
    """全部成员（转给同事时要看谁在线）。"""
    rows = session.scalars(select(Staff).order_by(Staff.active.desc(), Staff.role, Staff.created_at))
    return [staff_out(s) for s in rows]


def _team(session: Session, team_id: uuid.UUID | None) -> Team | None:
    if team_id is None:
        return None
    team = session.get(Team, team_id)
    if team is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "分组不存在")
    return team


def _commit_unique(session: Session) -> None:
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "姓名或账号已经有人用了") from exc


@router.post("/staff", response_model=StaffOut, status_code=status.HTTP_201_CREATED)
def add_staff(body: StaffIn, _: Staff = Depends(auth.require_admin),
              session: Session = Depends(get_session)) -> StaffOut:
    staff = Staff(name=body.name, username=body.username, password_hash=auth.hash_password(body.password),
                  role=body.role, team_id=_team(session, body.team_id).id if body.team_id else None)
    session.add(staff)
    _commit_unique(session)
    session.refresh(staff)
    return staff_out(staff)


def _other_active_admins(session: Session, staff: Staff) -> int:
    return session.scalar(select(func.count(Staff.id)).where(
        Staff.role == Role.ADMIN, Staff.active.is_(True), Staff.id != staff.id))


@router.patch("/staff/{staff_id}", response_model=StaffOut)
def edit_staff(staff_id: uuid.UUID, body: StaffPatch, _: Staff = Depends(auth.require_admin),
               session: Session = Depends(get_session)) -> StaffOut:
    staff = session.get(Staff, staff_id)
    if staff is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "成员不存在")
    losing_admin = staff.role == Role.ADMIN and (body.role == Role.SUPPORT or body.active is False)
    if losing_admin and not _other_active_admins(session, staff):
        raise HTTPException(status.HTTP_409_CONFLICT, "至少要留一个能用的管理员")
    if body.name is not None:
        staff.name = body.name
    if body.role is not None:
        staff.role = body.role
    if body.clear_team:
        staff.team_id = None
    elif body.team_id is not None:
        staff.team_id = _team(session, body.team_id).id
    if body.active is not None:
        staff.active = body.active
    if body.password is not None:
        staff.password_hash = auth.hash_password(body.password)
    _commit_unique(session)
    session.refresh(staff)
    return staff_out(staff)
