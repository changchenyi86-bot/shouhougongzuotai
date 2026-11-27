"""登录：账号密码，登录后浏览器带 Cookie 访问工作台接口。"""

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Cookie, Depends, HTTPException, Response, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_session
from app.models import LoginToken, Role, Staff, StaffStatus, Team

COOKIE = "wb_session"
DEFAULT_TEAMS = ["BI 组", "中台组"]
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    # SQLite 读回的时间不带时区，统一按 UTC 处理
    return dt.replace(tzinfo=timezone.utc) if dt and dt.tzinfo is None else dt


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$")
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), dklen=len(expected), **_SCRYPT)
    except ValueError:
        return False
    return hmac.compare_digest(actual, expected)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def login(session: Session, staff: Staff, response: Response) -> None:
    """发登录凭证（调用方负责 commit）。"""
    token = secrets.token_urlsafe(32)
    max_age = settings.login_days * 86400
    session.add(LoginToken(token_hash=_token_hash(token), staff_id=staff.id, expires_at=_now() + timedelta(seconds=max_age)))
    staff.last_seen = _now()
    response.set_cookie(COOKIE, token, max_age=max_age, httponly=True, samesite="lax", secure=settings.cookie_secure)


def logout(session: Session, token: str | None, response: Response) -> None:
    if token:
        session.execute(delete(LoginToken).where(LoginToken.token_hash == _token_hash(token)))
    response.delete_cookie(COOKIE)


def current_staff(
    wb_session: str | None = Cookie(default=None), session: Session = Depends(get_session)
) -> Staff:
    """当前登录的人。没登录、凭证过期或账号停用时返回 401。"""
    row = session.get(LoginToken, _token_hash(wb_session)) if wb_session else None
    staff = session.get(Staff, row.staff_id) if row else None
    if row is None or staff is None or not staff.active or _aware(row.expires_at) < _now():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "请先登录")
    # 打开着工作台就会不断请求接口，借此判断在不在线；每 30 秒最多记一次，免得频繁写库
    if staff.last_seen is None or _now() - _aware(staff.last_seen) > timedelta(seconds=30):
        staff.last_seen = _now()
        session.commit()
    return staff


def require_admin(staff: Staff = Depends(current_staff)) -> Staff:
    if staff.role != Role.ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "只有管理员能做这个操作")
    return staff


def is_online(staff: Staff) -> bool:
    """自己设成在线，并且最近打开着工作台。"""
    seen = _aware(staff.last_seen)
    return (staff.active and staff.status == StaffStatus.ONLINE and seen is not None
            and _now() - seen <= timedelta(seconds=settings.staff_online_seconds))


def needs_setup(session: Session) -> bool:
    return session.scalar(select(Staff.id).limit(1)) is None


def seed_teams(session: Session) -> None:
    """第一次启动时建好默认分组。"""
    if session.scalar(select(Team.id).limit(1)) is None:
        session.add_all(Team(name=n, sort=i) for i, n in enumerate(DEFAULT_TEAMS))
        session.commit()
