"""Sign-in, sign-out and the signed-in user's own account."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import func, select

from . import auth
from .auth import Principal, audit, db_dep, require
from .config import get_settings
from .models import ROLES, User, Workspace

router = APIRouter()


class LoginIn(BaseModel):
    email: str
    password: str


@router.get("/api/auth/status")
def status(s=Depends(db_dep)):
    """What the login page needs before anyone is signed in."""
    settings = get_settings()
    return {"auth_enabled": settings.auth_enabled,
            "has_users": bool(s.scalar(select(func.count()).select_from(User)))}


@router.post("/api/auth/login")
def login(body: LoginIn, request: Request, response: Response, s=Depends(db_dep)):
    email = body.email.strip().lower()
    ip = auth.client_ip(request)
    keys = (f"ip:{ip}", f"email:{email}")
    if auth.login_throttle.blocked(*keys):
        audit(s, None, "auth.login_blocked", "user", "", {"email": email}, actor=email, ip=ip)
        s.commit()
        raise HTTPException(429, "too many failed sign-ins; try again in a few minutes")
    user = s.scalar(select(User).where(User.email == email))
    ok = user is not None and user.active and auth.verify_password(body.password, user.password_hash)
    if user is None:
        auth.dummy_verify(body.password)
    if not ok:
        auth.login_throttle.fail(*keys)
        audit(s, None, "auth.login_failed", "user", user.id if user else "", {"email": email}, actor=email, ip=ip)
        s.commit()
        raise HTTPException(401, "email or password is wrong")
    auth.login_throttle.clear(*keys)
    token = auth.start_session(s, user, request)
    audit(s, None, "auth.login", "user", user.id, {}, actor=user.email, ip=ip)
    s.commit()
    settings = get_settings()
    response.set_cookie(auth.COOKIE, token, httponly=True, samesite="lax", secure=settings.cookie_secure,
                        max_age=settings.session_hours * 3600, path="/")
    return {"ok": True}


@router.post("/api/auth/logout")
def logout(request: Request, response: Response, s=Depends(db_dep)):
    token = request.cookies.get(auth.COOKIE)
    if token:
        sess = s.scalar(select(auth.AuthSession).where(auth.AuthSession.token_hash == auth.sha256(token)))
        if sess is not None and not sess.revoked:
            sess.revoked = 1
            user = s.get(User, sess.user_id)
            audit(s, None, "auth.logout", "user", sess.user_id, {}, actor=user.email if user else "",
                  ip=auth.client_ip(request))
            s.commit()
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


def workspaces_for(s, p: Principal) -> list[dict]:
    stmt = select(Workspace).order_by(Workspace.name)
    if p.workspace_ids is not None:
        stmt = stmt.where(Workspace.id.in_(p.workspace_ids))
    out = []
    for w in s.scalars(stmt):
        out.append({"id": w.id, "slug": w.slug, "name": w.name, "daily_gpu_runs": w.daily_gpu_runs,
                    "max_queued": w.max_queued, "gpu_runs_today": auth.gpu_runs_today(s, workspace_id=w.id)})
    return out


@router.get("/api/me")
def me(s=Depends(db_dep), p: Principal = Depends(require("viewer"))):
    u = p.user
    return {
        "user": {"id": u.id, "email": u.email, "name": u.name, "daily_gpu_runs": u.daily_gpu_runs,
                 "gpu_runs_today": auth.gpu_runs_today(s, user_id=u.id)} if u else None,
        "role": p.role, "via": p.via, "actor": p.actor,
        "can": {r: auth.role_at_least(p.role, r) for r in ROLES},
        "workspaces": workspaces_for(s, p),
    }


class PasswordIn(BaseModel):
    current: str
    new: str


@router.post("/api/me/password")
def change_password(body: PasswordIn, request: Request, response: Response, s=Depends(db_dep),
                    p: Principal = Depends(require("viewer"))):
    if p.user is None or p.via != "session":
        raise HTTPException(400, "sign in with a password to change it")
    user = s.get(User, p.user.id)
    if not auth.verify_password(body.current, user.password_hash):
        raise HTTPException(403, "current password is wrong")
    auth.check_password_policy(body.new)
    user.password_hash = auth.hash_password(body.new)
    user.password_changed_at = auth._now()
    auth.end_sessions(s, user.id)  # sign out everywhere else
    token = auth.start_session(s, user, request)
    audit(s, p, "user.password_change", "user", user.id)
    s.commit()
    settings = get_settings()
    response.set_cookie(auth.COOKIE, token, httponly=True, samesite="lax", secure=settings.cookie_secure,
                        max_age=settings.session_hours * 3600, path="/")
    return {"ok": True}
