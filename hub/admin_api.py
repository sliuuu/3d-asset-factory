"""Admin portal API: users, workspaces and quotas, API keys, models & services, audit, usage, health.

Every route needs the admin role, and every change writes an audit event.
"""
from __future__ import annotations

import re
import shutil
from collections import defaultdict
from datetime import timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from . import __version__, auth
from .auth import Principal, audit, db_dep, require
from .config import get_settings
from .models import (ROLES, ApiKey, Asset, AuditEvent, AuthSession, Membership, Run, SettingOverride, User,
                     Workspace)
from .runtime_settings import (DISABLED_KEY, configurable_fields, describe_adapters, disabled_adapters,
                               effective_settings, overrides, set_override)

router = APIRouter(prefix="/api/admin")
admin = Depends(require("admin"))


def _iso(dt):
    return dt.isoformat() if dt else None


def _404(what):
    raise HTTPException(404, f"{what} not found")


# ---------- users ----------

def ser_user(s, u: User) -> dict:
    ws = list(s.scalars(select(Membership.workspace_id).where(Membership.user_id == u.id)))
    keys = s.scalar(select(func.count()).select_from(ApiKey).where(ApiKey.user_id == u.id,
                                                                   ApiKey.revoked_at.is_(None))) or 0
    return {"id": u.id, "email": u.email, "name": u.name, "role": u.role, "active": bool(u.active),
            "daily_gpu_runs": u.daily_gpu_runs, "workspace_ids": ws, "active_keys": keys,
            "created_at": _iso(u.created_at), "last_login_at": _iso(u.last_login_at),
            "gpu_runs_today": auth.gpu_runs_today(s, user_id=u.id)}


@router.get("/users")
def list_users(s=Depends(db_dep), p: Principal = admin):
    return [ser_user(s, u) for u in s.scalars(select(User).order_by(User.email))]


class UserIn(BaseModel):
    email: str
    name: str = ""
    role: str = "creator"
    password: str = ""  # empty = generate a temporary one, shown once
    workspace_ids: list[str] | None = None
    daily_gpu_runs: int = 0


@router.post("/users")
def create_user(body: UserIn, s=Depends(db_dep), p: Principal = admin):
    password = body.password or auth.temp_password()
    u = auth.create_user(s, email=body.email, name=body.name, role=body.role, password=password,
                         workspace_ids=body.workspace_ids)
    u.daily_gpu_runs = max(0, body.daily_gpu_runs)
    audit(s, p, "user.create", "user", u.id, {"email": u.email, "role": u.role})
    s.commit()
    out = ser_user(s, u)
    if not body.password:
        out["temporary_password"] = password
    return out


class UserPatch(BaseModel):
    name: str | None = None
    role: str | None = None
    active: bool | None = None
    daily_gpu_runs: int | None = None
    workspace_ids: list[str] | None = None


@router.patch("/users/{user_id}")
def update_user(user_id: str, body: UserPatch, s=Depends(db_dep), p: Principal = admin):
    u = s.get(User, user_id) or _404("user")
    changed = body.model_dump(exclude_none=True)
    if body.role is not None and body.role not in ROLES:
        raise HTTPException(422, f"role must be one of {ROLES}")
    losing_admin = u.role == "admin" and u.active and (
        (body.role is not None and body.role != "admin") or body.active is False)
    if losing_admin and auth.active_admins(s) <= 1:
        raise HTTPException(409, "this is the last active admin; make someone else an admin first")
    if body.name is not None:
        u.name = body.name.strip()
    if body.role is not None:
        u.role = body.role
    if body.daily_gpu_runs is not None:
        u.daily_gpu_runs = max(0, body.daily_gpu_runs)
    if body.workspace_ids is not None:
        auth.set_memberships(s, u, body.workspace_ids)
    if body.active is not None:
        u.active = int(body.active)
        if not body.active:
            auth.end_sessions(s, u.id)  # keys stop working too: they check the owner is active
    audit(s, p, "user.update", "user", u.id, changed)
    s.commit()
    return ser_user(s, u)


@router.post("/users/{user_id}/reset-password")
def reset_password(user_id: str, s=Depends(db_dep), p: Principal = admin):
    u = s.get(User, user_id) or _404("user")
    password = auth.temp_password()
    u.password_hash = auth.hash_password(password)
    u.password_changed_at = auth._now()
    auth.end_sessions(s, u.id)
    audit(s, p, "user.password_reset", "user", u.id, {"email": u.email})
    s.commit()
    return {"temporary_password": password}


@router.get("/users/{user_id}/sessions")
def user_sessions(user_id: str, s=Depends(db_dep), p: Principal = admin):
    now = auth._now()
    rows = s.scalars(select(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked == 0)
                     .order_by(AuthSession.last_seen_at.desc()))
    return [{"id": r.id, "created_at": _iso(r.created_at), "last_seen_at": _iso(r.last_seen_at),
             "expires_at": _iso(r.expires_at), "ip": r.ip, "user_agent": r.user_agent}
            for r in rows if auth._aware(r.expires_at) > now]


@router.post("/users/{user_id}/sign-out")
def sign_out_user(user_id: str, s=Depends(db_dep), p: Principal = admin):
    u = s.get(User, user_id) or _404("user")
    auth.end_sessions(s, u.id)
    audit(s, p, "user.sign_out_all", "user", u.id)
    s.commit()
    return {"ok": True}


# ---------- workspaces ----------

def ser_workspace(s, w: Workspace) -> dict:
    members = s.scalar(select(func.count()).select_from(Membership).where(Membership.workspace_id == w.id)) or 0
    assets = s.scalar(select(func.count()).select_from(Asset).where(Asset.workspace_id == w.id)) or 0
    return {"id": w.id, "slug": w.slug, "name": w.name, "description": w.description,
            "daily_gpu_runs": w.daily_gpu_runs, "max_queued": w.max_queued, "members": members, "assets": assets,
            "gpu_runs_today": auth.gpu_runs_today(s, workspace_id=w.id), "queued": auth.queued(s, w.id),
            "created_at": _iso(w.created_at)}


@router.get("/workspaces")
def list_workspaces(s=Depends(db_dep), p: Principal = admin):
    return [ser_workspace(s, w) for w in s.scalars(select(Workspace).order_by(Workspace.name))]


class WorkspaceIn(BaseModel):
    slug: str
    name: str
    description: str = ""
    daily_gpu_runs: int = 0
    max_queued: int = 0


SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,59}$")


@router.post("/workspaces")
def create_workspace(body: WorkspaceIn, s=Depends(db_dep), p: Principal = admin):
    if not SLUG.match(body.slug):
        raise HTTPException(422, "slug: lower-case letters, digits and dashes")
    if s.scalar(select(Workspace).where(Workspace.slug == body.slug)):
        raise HTTPException(409, "a workspace with that slug exists")
    w = Workspace(slug=body.slug, name=body.name.strip() or body.slug, description=body.description,
                  daily_gpu_runs=max(0, body.daily_gpu_runs), max_queued=max(0, body.max_queued))
    s.add(w)
    s.flush()
    audit(s, p, "workspace.create", "workspace", w.id, body.model_dump())
    s.commit()
    return ser_workspace(s, w)


class WorkspacePatch(BaseModel):
    name: str | None = None
    description: str | None = None
    daily_gpu_runs: int | None = None
    max_queued: int | None = None
    member_ids: list[str] | None = None


@router.patch("/workspaces/{workspace_id}")
def update_workspace(workspace_id: str, body: WorkspacePatch, s=Depends(db_dep), p: Principal = admin):
    w = s.get(Workspace, workspace_id) or _404("workspace")
    for k in ("name", "description"):
        if getattr(body, k) is not None:
            setattr(w, k, getattr(body, k))
    for k in ("daily_gpu_runs", "max_queued"):
        if getattr(body, k) is not None:
            setattr(w, k, max(0, getattr(body, k)))
    if body.member_ids is not None:
        wanted = set(body.member_ids)
        for m in s.scalars(select(Membership).where(Membership.workspace_id == w.id)):
            if m.user_id not in wanted:
                s.delete(m)
        have = set(s.scalars(select(Membership.user_id).where(Membership.workspace_id == w.id)))
        for uid in wanted - have:
            if s.get(User, uid) is None:
                raise HTTPException(422, f"unknown user {uid}")
            s.add(Membership(user_id=uid, workspace_id=w.id))
    audit(s, p, "workspace.update", "workspace", w.id, body.model_dump(exclude_none=True))
    s.commit()
    return ser_workspace(s, w)


@router.get("/workspaces/{workspace_id}/members")
def workspace_members(workspace_id: str, s=Depends(db_dep), p: Principal = admin):
    ids = s.scalars(select(Membership.user_id).where(Membership.workspace_id == workspace_id))
    return [{"id": u.id, "email": u.email, "name": u.name, "role": u.role}
            for u in (s.get(User, i) for i in ids) if u]


# ---------- API keys ----------

def ser_key(s, k: ApiKey) -> dict:
    owner = s.get(User, k.user_id)
    now = auth._now()
    state = ("revoked" if k.revoked_at else "expired" if k.expires_at and auth._aware(k.expires_at) < now
             else "owner disabled" if owner is None or not owner.active else "active")
    return {"id": k.id, "name": k.name, "prefix": f"{auth.KEY_PREFIX}{k.id}_…", "user_id": k.user_id,
            "owner": owner.email if owner else None, "role": k.role, "workspace_id": k.workspace_id,
            "created_at": _iso(k.created_at), "expires_at": _iso(k.expires_at),
            "last_used_at": _iso(k.last_used_at), "last_used_ip": k.last_used_ip,
            "revoked_at": _iso(k.revoked_at), "state": state}


@router.get("/keys")
def list_keys(s=Depends(db_dep), p: Principal = admin):
    return [ser_key(s, k) for k in s.scalars(select(ApiKey).order_by(ApiKey.created_at.desc()))]


class KeyIn(BaseModel):
    name: str
    user_id: str | None = None  # default: the admin issuing it
    role: str = "creator"
    workspace_id: str | None = None
    expires_days: int | None = 90


@router.post("/keys")
def create_key(body: KeyIn, s=Depends(db_dep), p: Principal = admin):
    owner_id = body.user_id or p.user_id
    if owner_id is None:
        raise HTTPException(422, "pick the user this key acts as")
    owner = s.get(User, owner_id) or _404("user")
    if body.role not in ROLES:
        raise HTTPException(422, f"role must be one of {ROLES}")
    if not auth.role_at_least(owner.role, body.role):
        raise HTTPException(422, f"a key cannot outrank its owner ({owner.email} is {owner.role})")
    if body.workspace_id and s.get(Workspace, body.workspace_id) is None:
        raise HTTPException(422, "unknown workspace")
    if not body.name.strip():
        raise HTTPException(422, "name the key after what uses it")
    key, secret = auth.issue_key(s, name=body.name.strip(), user=owner, role=body.role,
                                 workspace_id=body.workspace_id or None,
                                 expires_days=body.expires_days if body.expires_days and body.expires_days > 0 else None,
                                 created_by=p.user_id)
    s.flush()
    audit(s, p, "key.create", "api_key", key.id, {"name": key.name, "owner": owner.email, "role": key.role,
                                                    "workspace_id": key.workspace_id,
                                                    "expires_at": _iso(key.expires_at)})
    s.commit()
    return {**ser_key(s, key), "secret": secret}


@router.delete("/keys/{key_id}")
def revoke_key(key_id: str, s=Depends(db_dep), p: Principal = admin):
    k = s.get(ApiKey, key_id) or _404("key")
    if k.revoked_at is None:
        k.revoked_at = auth._now()
        audit(s, p, "key.revoke", "api_key", k.id, {"name": k.name})
        s.commit()
    return ser_key(s, k)


# ---------- models & services ----------

@router.get("/settings")
def get_service_settings(s=Depends(db_dep), p: Principal = admin):
    env = get_settings()
    ov = overrides(s)
    rows = {r.key: r for r in s.scalars(select(SettingOverride))}
    fields = []
    for name in configurable_fields():
        row = rows.get(f"env.{name}")
        fields.append({"key": name, "env": f"HUB_{name.upper()}", "env_value": getattr(env, name),
                       "override": ov.get(name), "effective": ov.get(name, getattr(env, name)),
                       "updated_by": row.updated_by if row else None,
                       "updated_at": _iso(row.updated_at) if row else None})
    return {"adapters": describe_adapters(s), "fields": fields}


class SettingsIn(BaseModel):
    disabled_adapters: list[str] | None = None
    overrides: dict[str, str | None] | None = None  # null clears an override


@router.put("/settings")
def put_service_settings(body: SettingsIn, s=Depends(db_dep), p: Principal = admin):
    from .adapters import ADAPTERS
    allowed = set(configurable_fields())
    changes = {}
    if body.disabled_adapters is not None:
        unknown = set(body.disabled_adapters) - set(ADAPTERS)
        if unknown:
            raise HTTPException(422, f"unknown pipelines {sorted(unknown)}")
        before = disabled_adapters(s)
        after = set(body.disabled_adapters)
        if before != after:
            set_override(s, DISABLED_KEY, sorted(after) or None, p.actor)
            changes["switched_off"] = sorted(after - before)
            changes["switched_on"] = sorted(before - after)
    for name, value in (body.overrides or {}).items():
        if name not in allowed:
            raise HTTPException(422, f"{name} is not configurable here")
        if value is not None:
            value = value.strip()
            if name.endswith("_url") and value and not re.match(r"^https?://[^\s]+$", value):
                raise HTTPException(422, f"{name} must be an http(s) URL")
        set_override(s, name, value, p.actor)
        changes[name] = value
    if changes:
        audit(s, p, "settings.update", "settings", "", changes)
    s.commit()
    return get_service_settings(s, p)


# ---------- audit ----------

@router.get("/audit")
def list_audit(action: str = "", actor: str = "", target_id: str = "", before_id: int | None = None,
               limit: int = Query(100, le=500), s=Depends(db_dep), p: Principal = admin):
    stmt = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit)
    if action:
        stmt = stmt.where(AuditEvent.action.startswith(action))
    if actor:
        stmt = stmt.where(AuditEvent.actor.ilike(f"%{actor}%"))
    if target_id:
        stmt = stmt.where(AuditEvent.target_id == target_id)
    if before_id:
        stmt = stmt.where(AuditEvent.id < before_id)
    return [{"id": e.id, "at": _iso(e.at), "actor": e.actor, "user_id": e.user_id, "key_id": e.key_id,
             "action": e.action, "target_type": e.target_type, "target_id": e.target_id, "detail": e.detail,
             "ip": e.ip} for e in s.scalars(stmt)]


# ---------- usage ----------

@router.get("/usage")
def usage(days: int = Query(30, ge=1, le=366), s=Depends(db_dep), p: Principal = admin):
    since = auth._now() - timedelta(days=days)
    users = {u.id: u.email for u in s.scalars(select(User))}
    spaces = {w.id: w.slug for w in s.scalars(select(Workspace))}
    rows: dict[tuple, dict] = defaultdict(lambda: {"runs": 0, "succeeded": 0, "failed": 0, "rejected": 0,
                                                   "gpu_seconds": 0.0})
    totals = defaultdict(lambda: {"runs": 0, "gpu_seconds": 0.0})
    for r in s.scalars(select(Run).where(Run.created_at >= since, Run.kind == "generate")):
        day = auth._aware(r.created_at).date().isoformat()
        key = (day, users.get(r.created_by, "—"), spaces.get(r.workspace_id, "—"), r.adapter)
        row = rows[key]
        row["runs"] += 1
        if r.status in ("succeeded", "failed", "rejected"):
            row[r.status] += 1
        secs = r.worker_info.get("seconds") if r.needs_gpu else None
        if secs:
            row["gpu_seconds"] += float(secs)
        for k in (("user", key[1]), ("workspace", key[2]), ("pipeline", key[3])):
            totals[k]["runs"] += 1
            totals[k]["gpu_seconds"] += float(secs or 0)
    detail = [{"day": k[0], "user": k[1], "workspace": k[2], "pipeline": k[3], **v}
              for k, v in sorted(rows.items(), reverse=True)]
    grouped = defaultdict(list)
    for (kind, name), v in totals.items():
        grouped[kind].append({"name": name, **v})
    for v in grouped.values():
        v.sort(key=lambda x: -x["runs"])
    return {"days": days, "rows": detail, "by_user": grouped["user"], "by_workspace": grouped["workspace"],
            "by_pipeline": grouped["pipeline"]}


# ---------- health ----------

def _probe(url: str) -> dict:
    try:
        r = httpx.get(url, timeout=3.0)
        return {"reachable": True, "status": r.status_code}
    except httpx.HTTPError as exc:
        return {"reachable": False, "error": type(exc).__name__}


@router.get("/health")
def health(probe: bool = False, s=Depends(db_dep), p: Principal = admin):
    settings = get_settings()
    now = auth._now()
    counts = dict(s.execute(select(Run.status, func.count()).group_by(Run.status)).all())
    oldest = s.scalar(select(func.min(Run.created_at)).where(Run.status == "queued"))
    running = [{"id": r.id, "adapter": r.adapter, "lease_owner": r.lease_owner,
                "started_at": _iso(r.started_at), "lease_until": _iso(r.lease_until)}
               for r in s.scalars(select(Run).where(Run.status == "running"))]
    last_ok = s.scalar(select(func.max(Run.finished_at)).where(Run.status == "succeeded"))
    blob_dir = settings.blob_dir
    blob_bytes = sum(f.stat().st_size for f in blob_dir.rglob("*") if f.is_file()) if blob_dir.exists() else 0
    disk = shutil.disk_usage(settings.data_dir if settings.data_dir.exists() else "/")
    probes = []
    if probe:  # opt-in: each unreachable worker costs up to 3 s
        eff = effective_settings(s)
        probes = [{"key": n, "url": getattr(eff, n), **_probe(getattr(eff, n))}
                  for n in configurable_fields() if n.endswith("_url") and getattr(eff, n)]
    sessions = s.scalar(select(func.count()).select_from(AuthSession).where(
        AuthSession.revoked == 0, AuthSession.expires_at > now)) or 0
    warnings = []
    if settings.api_token:
        warnings.append("HUB_API_TOKEN is set: anyone with it has admin rights. Issue API keys and remove it.")
    if not settings.cookie_secure:
        warnings.append("Session cookies are not marked Secure (HUB_COOKIE_SECURE=1 once the hub is behind TLS).")
    if not settings.auth_enabled:
        warnings.append("Authentication is off (HUB_AUTH=off).")
    return {
        "version": __version__, "auth_enabled": settings.auth_enabled, "warnings": warnings,
        "database": "postgres" if settings.db_url.startswith("postgres") else "sqlite",
        "runs": counts, "oldest_queued_at": _iso(oldest), "running": running, "last_success_at": _iso(last_ok),
        "storage": {"blob_bytes": blob_bytes, "disk_total": disk.total, "disk_free": disk.free},
        "users": s.scalar(select(func.count()).select_from(User).where(User.active == 1)) or 0,
        "active_sessions": sessions,
        "active_keys": s.scalar(select(func.count()).select_from(ApiKey).where(ApiKey.revoked_at.is_(None))) or 0,
        "adapters": describe_adapters(s), "probes": probes,
    }
