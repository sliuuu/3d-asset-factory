"""Accounts, sessions, API keys, roles, quotas and the audit log (docs/ADMIN_AND_AUTH.md)."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import session as new_session
from .migrate import default_workspace
from .models import ROLES, ApiKey, AuditEvent, AuthSession, Membership, Run, User, Workspace

COOKIE = "hub_session"
KEY_PREFIX = "haf_"
MIN_PASSWORD = 12


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    # SQLite hands back naive datetimes; everything the hub writes is UTC.
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def role_at_least(role: str, needed: str) -> bool:
    return role in ROLES and ROLES.index(role) >= ROLES.index(needed)


# ---------- passwords (scrypt, stdlib) ----------

_N, _R, _P = 2 ** 14, 8, 1


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(pw.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, dk = stored.split("$")
        if algo != "scrypt":
            return False
        got = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=len(dk) // 2)
        return hmac.compare_digest(got.hex(), dk)
    except (ValueError, TypeError):
        return False


_DUMMY_HASH = None


def dummy_verify(pw: str) -> None:
    """Spend the same time on unknown emails as on wrong passwords."""
    global _DUMMY_HASH
    _DUMMY_HASH = _DUMMY_HASH or hash_password("not-a-real-password")
    verify_password(pw, _DUMMY_HASH)


def check_password_policy(pw: str) -> None:
    if len(pw) < MIN_PASSWORD:
        raise HTTPException(422, f"password must be at least {MIN_PASSWORD} characters")


def temp_password() -> str:
    return secrets.token_urlsafe(12)


# ---------- sign-in throttle (per process; the hub runs one API process) ----------

class Throttle:
    def __init__(self, limit: int = 5, window: float = 300.0):
        self.limit, self.window = limit, window
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def blocked(self, *keys: str) -> bool:
        now = time.monotonic()
        with self._lock:
            for k in keys:
                recent = [t for t in self._fails.get(k, []) if now - t < self.window]
                self._fails[k] = recent
                if len(recent) >= self.limit:
                    return True
        return False

    def fail(self, *keys: str) -> None:
        with self._lock:
            for k in keys:
                self._fails.setdefault(k, []).append(time.monotonic())

    def clear(self, *keys: str) -> None:
        with self._lock:
            for k in keys:
                self._fails.pop(k, None)


login_throttle = Throttle()


# ---------- principal ----------

@dataclass
class Principal:
    role: str
    actor: str  # what the audit log shows
    user: User | None = None
    key: ApiKey | None = None
    # None = every workspace (admins, auth off, legacy token)
    workspace_ids: set[str] | None = field(default=None)
    via: str = "session"  # session | key | legacy-token | auth-off
    ip: str = ""

    @property
    def user_id(self) -> str | None:
        return self.user.id if self.user else None

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def can_see(self, workspace_id: str | None) -> bool:
        return self.workspace_ids is None or workspace_id in self.workspace_ids

    @property
    def reviewer_name(self) -> str:
        return self.user.email if self.user else ""  # stable and unique, unlike display names


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def user_workspace_ids(db: Session, user: User) -> set[str]:
    return set(db.scalars(select(Membership.workspace_id).where(Membership.user_id == user.id)))


def _principal_for_user(db: Session, user: User, role: str, via: str, ip: str, key: ApiKey | None = None) -> Principal:
    ws = None
    if role != "admin":
        ws = user_workspace_ids(db, user)
    if key is not None and key.workspace_id:
        ws = {key.workspace_id} if ws is None or key.workspace_id in ws else set()
    actor = f"{user.email} (key {key.name})" if key else user.email
    return Principal(role=role, actor=actor, user=user, key=key, workspace_ids=ws, via=via, ip=ip)


def resolve(request: Request, db: Session) -> Principal | None:
    settings = get_settings()
    ip = client_ip(request)
    if not settings.auth_enabled:
        return Principal(role="admin", actor="dev (auth off)", via="auth-off", ip=ip)
    bearer = request.headers.get("authorization", "")
    if bearer.lower().startswith("bearer "):
        token = bearer[7:].strip()
        if token.startswith(KEY_PREFIX):
            return _from_key(db, token, ip)
        if settings.api_token and hmac.compare_digest(token, settings.api_token):
            return Principal(role="admin", actor="legacy-token", via="legacy-token", ip=ip)
        return None
    cookie = request.cookies.get(COOKIE)
    if cookie:
        p = _from_session(db, cookie, ip)
        if p is not None and request.method not in ("GET", "HEAD", "OPTIONS") and not _same_origin(request):
            raise HTTPException(403, "cross-site request refused")
        return p
    return None


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin:
        return True  # SameSite=Lax already keeps the cookie off cross-site POSTs
    return urlsplit(origin).netloc == request.headers.get("host", "")


def _from_session(db: Session, token: str, ip: str) -> Principal | None:
    settings = get_settings()
    sess = db.scalar(select(AuthSession).where(AuthSession.token_hash == sha256(token)))
    now = _now()
    if sess is None or sess.revoked or _aware(sess.expires_at) < now:
        return None
    if _aware(sess.last_seen_at) + timedelta(hours=settings.session_idle_hours) < now:
        return None
    user = db.get(User, sess.user_id)
    if user is None or not user.active:
        return None
    if now - _aware(sess.last_seen_at) > timedelta(minutes=1):
        sess.last_seen_at = now
        db.commit()
    return _principal_for_user(db, user, user.role, "session", ip)


def _from_key(db: Session, token: str, ip: str) -> Principal | None:
    key = db.scalar(select(ApiKey).where(ApiKey.secret_hash == sha256(token)))
    now = _now()
    if key is None or key.revoked_at is not None or (key.expires_at and _aware(key.expires_at) < now):
        return None
    user = db.get(User, key.user_id)
    if user is None or not user.active:
        return None
    # A key never outranks its owner, even if the owner was demoted after it was issued.
    role = key.role if ROLES.index(key.role) <= ROLES.index(user.role) else user.role
    if key.last_used_at is None or now - _aware(key.last_used_at) > timedelta(minutes=1) or key.last_used_ip != ip:
        key.last_used_at, key.last_used_ip = now, ip
        db.commit()
    return _principal_for_user(db, user, role, "key", ip, key=key)


def db_dep():
    s = new_session()
    try:
        yield s
    finally:
        s.close()


def current(request: Request, db: Session = Depends(db_dep)) -> Principal:
    p = resolve(request, db)
    if p is None:
        raise HTTPException(401, "sign in required")
    request.state.principal = p
    return p


def require(role: str):
    def dep(p: Principal = Depends(current)) -> Principal:
        if not role_at_least(p.role, role):
            raise HTTPException(403, f"needs the {role} role")
        return p
    return dep


# ---------- sessions and keys ----------

def start_session(db: Session, user: User, request: Request) -> str:
    settings = get_settings()
    token = secrets.token_urlsafe(32)
    now = _now()
    db.add(AuthSession(token_hash=sha256(token), user_id=user.id, created_at=now, last_seen_at=now,
                       expires_at=now + timedelta(hours=settings.session_hours), ip=client_ip(request),
                       user_agent=request.headers.get("user-agent", "")[:255]))
    user.last_login_at = now
    return token


def end_sessions(db: Session, user_id: str) -> None:
    for sess in db.scalars(select(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked == 0)):
        sess.revoked = 1


def issue_key(db: Session, *, name: str, user: User, role: str, workspace_id: str | None,
              expires_days: int | None, created_by: str | None) -> tuple[ApiKey, str]:
    key = ApiKey(name=name, user_id=user.id, role=role, workspace_id=workspace_id, created_by=created_by,
                 secret_hash="")
    key.id = secrets.token_hex(6)
    secret = f"{KEY_PREFIX}{key.id}_{secrets.token_urlsafe(32)}"
    key.secret_hash = sha256(secret)
    if expires_days:
        key.expires_at = _now() + timedelta(days=expires_days)
    db.add(key)
    return key, secret


# ---------- users ----------

def create_user(db: Session, *, email: str, name: str, role: str, password: str,
                workspace_ids: list[str] | None = None) -> User:
    email = email.strip().lower()
    if "@" not in email:
        raise HTTPException(422, "email looks wrong")
    if role not in ROLES:
        raise HTTPException(422, f"role must be one of {ROLES}")
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "a user with that email exists")
    check_password_policy(password)
    user = User(email=email, name=name.strip(), role=role, password_hash=hash_password(password),
                password_changed_at=_now())
    db.add(user)
    db.flush()
    set_memberships(db, user, workspace_ids if workspace_ids is not None else [default_workspace(db).id])
    return user


def set_memberships(db: Session, user: User, workspace_ids: list[str]) -> None:
    wanted = set(workspace_ids)
    for wid in wanted:
        if db.get(Workspace, wid) is None:
            raise HTTPException(422, f"unknown workspace {wid}")
    have = {m.workspace_id: m for m in db.scalars(select(Membership).where(Membership.user_id == user.id))}
    for wid, m in have.items():
        if wid not in wanted:
            db.delete(m)
    for wid in wanted - have.keys():
        db.add(Membership(user_id=user.id, workspace_id=wid))


def active_admins(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(User).where(User.role == "admin", User.active == 1)) or 0


def bootstrap_admin(db: Session) -> None:
    s = get_settings()
    if not (s.admin_email and s.admin_password):
        return
    if db.scalar(select(func.count()).select_from(User)):
        return
    user = create_user(db, email=s.admin_email, name="", role="admin", password=s.admin_password)
    audit(db, None, "user.bootstrap", "user", user.id, {"email": user.email})
    db.commit()


# ---------- audit ----------

def audit(db: Session, p: Principal | None, action: str, target_type: str = "", target_id: str = "",
          detail: dict | None = None, *, actor: str = "", ip: str = "") -> None:
    db.add(AuditEvent(actor=actor or (p.actor if p else "system"), user_id=p.user_id if p else None,
                      key_id=p.key.id if p and p.key else None, action=action, target_type=target_type,
                      target_id=str(target_id or ""), detail=detail or {}, ip=ip or (p.ip if p else "")))


# ---------- quotas ----------

def _day_start() -> datetime:
    return _now().replace(hour=0, minute=0, second=0, microsecond=0)


def gpu_runs_today(db: Session, *, workspace_id: str | None = None, user_id: str | None = None) -> int:
    q = select(func.count()).select_from(Run).where(Run.needs_gpu == 1, Run.kind == "generate",
                                                    Run.status != "rejected", Run.created_at >= _day_start())
    if workspace_id:
        q = q.where(Run.workspace_id == workspace_id)
    if user_id:
        q = q.where(Run.created_by == user_id)
    return db.scalar(q) or 0


def queued(db: Session, workspace_id: str) -> int:
    return db.scalar(select(func.count()).select_from(Run).where(Run.workspace_id == workspace_id,
                                                                 Run.status.in_(("queued", "running")))) or 0


def check_quota(db: Session, p: Principal, workspace: Workspace, gpu_runs: int, total_runs: int) -> None:
    """Refuse (429) when the new runs would go over a workspace or user limit."""
    if workspace.daily_gpu_runs and gpu_runs:
        used = gpu_runs_today(db, workspace_id=workspace.id)
        if used + gpu_runs > workspace.daily_gpu_runs:
            raise HTTPException(429, f"workspace '{workspace.name}' daily GPU run quota reached "
                                     f"({used}/{workspace.daily_gpu_runs} used today)")
    if workspace.max_queued and total_runs:
        q = queued(db, workspace.id)
        if q + total_runs > workspace.max_queued:
            raise HTTPException(429, f"workspace '{workspace.name}' allows {workspace.max_queued} queued runs "
                                     f"({q} waiting)")
    if p.user is not None and p.user.daily_gpu_runs and gpu_runs:
        used = gpu_runs_today(db, user_id=p.user.id)
        if used + gpu_runs > p.user.daily_gpu_runs:
            raise HTTPException(429, f"your daily GPU run quota is reached ({used}/{p.user.daily_gpu_runs} used today)")
