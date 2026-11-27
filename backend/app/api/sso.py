"""公司单点登录（网易 OpenID）：发起登录、回调，以及管理员把公司账号绑定到成员。"""

import hashlib
import hmac
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Cookie, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import auth, sso
from app.config import settings
from app.db import get_session
from app.models import LoginToken, SsoAccount, SsoState, Staff
from app.schemas import SsoAccountOut, SsoBindIn

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

STATE_COOKIE = "wb_sso_state"
STATE_COOKIE_PATH = "/api/auth/sso"
MAX_PENDING_STATES = 1000  # 同时在登录中的人数上限，防止被刷满库
SUBJECT_RE = re.compile(r"[a-z0-9][a-z0-9._@+-]{0,254}")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _back_to_login(code: str, **extra: str) -> RedirectResponse:
    """回登录页，并带上原因给页面显示提示。"""
    resp = RedirectResponse("/?" + urlencode({"sso_error": code, **extra}), status.HTTP_302_FOUND)
    resp.delete_cookie(STATE_COOKIE, path=STATE_COOKIE_PATH)
    return resp


# ---------- 登录 ----------


@router.get("/auth/sso/login")
def sso_login(session: Session = Depends(get_session)) -> RedirectResponse:
    """点「用网易账号登录」：记下一次性的 state、nonce，跳到公司登录页。

    登录页的按钮总是指向登记的回调地址那个网址（sso.login_url），从 127.0.0.1 等别的地址打开工作台时，
    state Cookie 也记在回调回来的那个网址上，回调时才对得上。
    """
    if not settings.sso_enabled:
        return _back_to_login("off")
    now = _now()
    session.execute(delete(SsoState).where(SsoState.expires_at < now))
    if session.scalar(select(func.count()).select_from(SsoState)) >= MAX_PENDING_STATES:
        session.commit()
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "登录的人太多，请稍后再试")
    state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    session.add(SsoState(state_hash=_hash(state), nonce=nonce, created_at=now,
                         expires_at=now + timedelta(seconds=sso.STATE_TTL)))
    session.commit()

    resp = RedirectResponse(sso.authorize_url(state, nonce), status.HTTP_302_FOUND)
    resp.set_cookie(STATE_COOKIE, state, max_age=sso.STATE_TTL, path=STATE_COOKIE_PATH, httponly=True,
                    samesite="lax", secure=settings.cookie_secure)
    return resp


def _take_state(session: Session, state: str, cookie: str | None) -> SsoState:
    """核对回调带回的 state 和这个浏览器发起时记下的是同一个，用过即删。"""
    if not state or not cookie or len(state) > 128 or not hmac.compare_digest(state.encode(), cookie.encode()):
        raise sso.SsoError("expired", "state 和 Cookie 对不上")
    row = session.get(SsoState, _hash(state))
    if row is None:
        raise sso.SsoError("expired", "state 不存在或已用过")
    session.delete(row)
    session.commit()  # 先删再换 token：同一个 state 只能用一次
    if auth._aware(row.expires_at) < _now():
        raise sso.SsoError("expired", "state 过期")
    return row


def _auto_match(session: Session, identity: sso.Identity) -> Staff | None:
    """第一次用公司账号登录时，工作台账号和公司账号一样、姓名也和公司登记的一样，才自动认作同一个人。"""
    if not identity.fullname:
        return None
    return session.scalar(_unbound_staff().where(func.lower(Staff.username) == identity.subject,
                                                 Staff.name == identity.fullname))


@router.get("/auth/sso/callback")
def sso_callback(code: str = "", state: str = "", error: str = "",
                 wb_sso_state: str | None = Cookie(default=None),
                 session: Session = Depends(get_session)) -> RedirectResponse:
    """公司登录完回到这里：校验后登录绑定的成员；没绑定的记下来，等管理员绑定。"""
    if not settings.sso_enabled:
        return _back_to_login("off")
    try:
        if error:
            raise sso.SsoError("denied", error[:200])
        row = _take_state(session, state, wb_sso_state)
        identity = sso.exchange(code, row.nonce, auth._aware(row.created_at).timestamp())
    except sso.SsoError as exc:
        log.info("公司账号登录没成功（%s）：%s", exc.code, exc)
        return _back_to_login(exc.code)

    account = session.get(SsoAccount, identity.subject)
    if account is None:
        account = SsoAccount(subject=identity.subject)
        session.add(account)
    account.fullname = identity.fullname or account.fullname
    account.email = identity.email or account.email
    account.last_login_at = _now()
    if account.staff_id is None and (match := _auto_match(session, identity)) is not None:
        account.staff_id = match.id
        log.info("公司账号 %s 自动绑定到成员 %s", identity.subject, match.name)
    staff = session.get(Staff, account.staff_id) if account.staff_id else None
    if staff is None:
        session.commit()
        return _back_to_login("unbound", account=identity.subject)
    if not staff.active:
        session.commit()
        return _back_to_login("disabled")

    resp = RedirectResponse("/", status.HTTP_302_FOUND)
    resp.delete_cookie(STATE_COOKIE, path=STATE_COOKIE_PATH)
    auth.login(session, staff, resp)
    session.commit()
    return resp


# ---------- 管理员：绑定公司账号 ----------


def _subject(raw: str) -> str:
    subject = raw.strip().lower()
    if not SUBJECT_RE.fullmatch(subject):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "公司账号填邮箱前缀，比如 zhangsan 或 wb.zhangsan")
    return subject


def _out(session: Session, a: SsoAccount, free_names: dict[str, Staff]) -> SsoAccountOut:
    staff = session.get(Staff, a.staff_id) if a.staff_id else None
    suggested = free_names.get(a.fullname) if staff is None and a.fullname else None
    return SsoAccountOut(subject=a.subject, fullname=a.fullname, email=a.email, staff_id=a.staff_id,
                         staff_name=staff.name if staff else None,
                         suggested_staff_id=suggested.id if suggested else None, last_login_at=a.last_login_at)


def _unbound_staff():
    """还没绑定公司账号的在用成员（查询条件）。"""
    bound = select(SsoAccount.staff_id).where(SsoAccount.staff_id.is_not(None))
    return select(Staff).where(Staff.active.is_(True), Staff.id.not_in(bound))


def _free_names(session: Session) -> dict[str, Staff]:
    return {s.name: s for s in session.scalars(_unbound_staff())}


@router.get("/sso/accounts", response_model=list[SsoAccountOut], dependencies=[Depends(auth.require_admin)])
def list_accounts(session: Session = Depends(get_session)) -> list[SsoAccountOut]:
    """用公司账号登录过、或管理员绑定过的账号；没绑定的排在前面。"""
    rows = session.scalars(select(SsoAccount).order_by(SsoAccount.staff_id.is_(None).desc(),
                                                       SsoAccount.last_login_at.desc().nulls_last(),
                                                       SsoAccount.subject))
    free = _free_names(session)
    return [_out(session, a, free) for a in rows]


def _revoke(session: Session, staff_id, keep_token: str | None) -> None:
    """绑定变了（可能是之前绑错了人），让这个成员已有的登录失效；正在操作的管理员自己这次登录保留。"""
    q = delete(LoginToken).where(LoginToken.staff_id == staff_id)
    if keep_token:
        q = q.where(LoginToken.token_hash != auth._token_hash(keep_token))
    session.execute(q)


@router.put("/sso/accounts/{subject}", response_model=SsoAccountOut)
def bind_account(subject: str, body: SsoBindIn, _: Staff = Depends(auth.require_admin),
                 wb_session: str | None = Cookie(default=None),
                 session: Session = Depends(get_session)) -> SsoAccountOut:
    """把公司账号绑定到成员（成员原来绑的账号会换成这个），或解除绑定。还没登录过的账号也可以先绑好。"""
    subject = _subject(subject)
    account = session.get(SsoAccount, subject)
    if body.staff_id is None:
        if account is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "没有这个公司账号")
        if account.staff_id is not None:
            _revoke(session, account.staff_id, wb_session)
            account.staff_id = None
        session.commit()
        return _out(session, account, _free_names(session))

    staff = session.get(Staff, body.staff_id)
    if staff is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "成员不存在")
    if account is not None and account.staff_id not in (None, staff.id):
        other = session.get(Staff, account.staff_id)
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"公司账号 {subject} 已经绑定给{other.name if other else '别人'}了，先解除那边的绑定")
    if account is None:
        account = SsoAccount(subject=subject)
        session.add(account)
    previous = staff.sso_account
    if previous is not None and previous.subject != subject:
        previous.staff_id = None
        session.flush()  # 先放开旧账号，唯一约束才不冲突
        _revoke(session, staff.id, wb_session)
    account.staff_id = staff.id
    session.commit()
    return _out(session, account, _free_names(session))


@router.delete("/sso/accounts/{subject}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(auth.require_admin)])
def dismiss_account(subject: str, session: Session = Depends(get_session)) -> None:
    """忽略一个没绑定的公司账号（比如不是团队里的人误点了登录）。绑定着的要先解除。"""
    account = session.get(SsoAccount, _subject(subject))
    if account is None:
        return
    if account.staff_id is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "这个公司账号绑定着成员，先解除绑定")
    session.delete(account)
    session.commit()
