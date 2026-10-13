"""Hub records.

The hub owns runs, comparisons and benchmarks. It does not own engineering truth (VDC / evidence
registry) or approved components (dt-component-library); it links to them by ID.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _id() -> str:
    return uuid.uuid4().hex[:16]


def _now() -> datetime:
    return datetime.now(timezone.utc)


# Lifecycle from the Tech Spec §6. The hub may move an asset up to `technical_validated`;
# anything above that needs a named reviewer and happens in the component library.
STATES = ("provisional", "technical_validated", "visual_validated", "legal_cleared", "approved", "deprecated")

# Evidence classes. `generated-visual` is the lowest and is never dimensional.
EVIDENCE_CLASSES = ("generated-visual", "reconstruction", "parametric", "authored", "library")

CLASSIFICATIONS = ("public", "internal")


class Asset(Base):
    __tablename__ = "assets"
    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=_id)
    name: Mapped[str] = mapped_column(String(200))
    family: Mapped[str] = mapped_column(String(80), default="")  # genset, crah, rpp, chiller, ups ...
    site: Mapped[str] = mapped_column(String(40), default="")  # HK6, SV11 ...
    description: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    # Links out to the systems of record. Never copied in.
    library_ref: Mapped[str] = mapped_column(String(200), default="")  # dt-component-library id@rev
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list)  # registry fact/assertion IDs
    # VDC envelope used for fit checks: {"L": m, "W": m, "H": m, "source": "...", "confirmed": bool}
    envelope: Mapped[dict] = mapped_column(JSON, default=dict)
    workspace_id: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    created_by: Mapped[str | None] = mapped_column(String(16), nullable=True)  # user id
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    versions: Mapped[list[Version]] = relationship(back_populates="asset", order_by="Version.created_at")


class Version(Base):
    __tablename__ = "versions"
    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=_id)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"))
    label: Mapped[str] = mapped_column(String(120), default="")
    state: Mapped[str] = mapped_column(String(32), default="provisional")
    evidence_class: Mapped[str] = mapped_column(String(32), default="generated-visual")
    pipeline: Mapped[str] = mapped_column(String(60), default="")  # adapter name
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    # Lineage edges: {"derived_from": [version ids], "donor_of": [...], "supersedes": version id}
    lineage: Mapped[dict] = mapped_column(JSON, default=dict)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    intake: Mapped[dict] = mapped_column(JSON, default=dict)  # intake-gate results
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    asset: Mapped[Asset] = relationship(back_populates="versions")
    artifacts: Mapped[list[Artifact]] = relationship(back_populates="version")
    reviews: Mapped[list[Review]] = relationship(back_populates="version")


class InputSet(Base):
    __tablename__ = "input_sets"
    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=_id)
    prompt: Mapped[str] = mapped_column(Text, default="")
    # [{"sha256", "view": "front|back|left|right|top|service|other", "filename", "mime"}]
    images: Mapped[list] = mapped_column(JSON, default=list)
    classification: Mapped[str] = mapped_column(String(16), default="internal")
    provenance: Mapped[str] = mapped_column(Text, default="")
    licence: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Run(Base):
    """One job. Status: queued -> running -> succeeded | failed | rejected."""
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=_id)
    kind: Mapped[str] = mapped_column(String(32))  # generate | import | convert | inspect | fit
    adapter: Mapped[str] = mapped_column(String(60), default="")
    asset_id: Mapped[str | None] = mapped_column(ForeignKey("assets.id"), nullable=True)
    input_set_id: Mapped[str | None] = mapped_column(ForeignKey("input_sets.id"), nullable=True)
    batch_id: Mapped[str] = mapped_column(String(16), default="")
    suite_case_id: Mapped[str | None] = mapped_column(ForeignKey("suite_cases.id"), nullable=True)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    needs_gpu: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    error: Mapped[str] = mapped_column(Text, default="")
    # Provenance returned by the worker (model, weights hash, repo commit, seed, timings).
    worker_info: Mapped[dict] = mapped_column(JSON, default=dict)
    lease_owner: Mapped[str] = mapped_column(String(80), default="")
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    events: Mapped[list] = mapped_column(JSON, default=list)  # append-only [{at, msg}]
    workspace_id: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    created_by: Mapped[str | None] = mapped_column(String(16), nullable=True)  # user id
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Artifact(Base):
    __tablename__ = "artifacts"
    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=_id)
    version_id: Mapped[str] = mapped_column(ForeignKey("versions.id"))
    role: Mapped[str] = mapped_column(String(32), default="model")  # model | lod | texture | preview | source
    format: Mapped[str] = mapped_column(String(12))  # glb obj fbx usdz stl ply blend png
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    filename: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    version: Mapped[Version] = relationship(back_populates="artifacts")


class Review(Base):
    __tablename__ = "reviews"
    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=_id)
    version_id: Mapped[str] = mapped_column(ForeignKey("versions.id"))
    reviewer: Mapped[str] = mapped_column(String(120))
    verdict: Mapped[str] = mapped_column(String(16))  # pass | fail | notes
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    version: Mapped[Version] = relationship(back_populates="reviews")


class Suite(Base):
    """A benchmark: fixed assets x fixed inputs x pipelines."""
    __tablename__ = "suites"
    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=_id)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    pipelines: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    cases: Mapped[list[SuiteCase]] = relationship(back_populates="suite")


class SuiteCase(Base):
    __tablename__ = "suite_cases"
    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=_id)
    suite_id: Mapped[str] = mapped_column(ForeignKey("suites.id"))
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"))
    input_set_id: Mapped[str | None] = mapped_column(ForeignKey("input_sets.id"), nullable=True)
    # Version used as ground truth for deviation scoring (reconstruction or library LOD), if any.
    reference_version_id: Mapped[str | None] = mapped_column(ForeignKey("versions.id"), nullable=True)
    suite: Mapped[Suite] = relationship(back_populates="cases")


# ---------- accounts and governance (docs/ADMIN_AND_AUTH.md) ----------

# Ordered: each role has the permissions of the ones before it.
ROLES = ("viewer", "creator", "reviewer", "admin")


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=_id)
    email: Mapped[str] = mapped_column(String(254), unique=True)  # stored lower-case
    name: Mapped[str] = mapped_column(String(120), default="")
    password_hash: Mapped[str] = mapped_column(String(255), default="")
    role: Mapped[str] = mapped_column(String(16), default="viewer")
    active: Mapped[int] = mapped_column(Integer, default=1)
    daily_gpu_runs: Mapped[int] = mapped_column(Integer, default=0)  # 0 = unlimited
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=_id)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ip: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(255), default="")
    revoked: Mapped[int] = mapped_column(Integer, default=0)


class ApiKey(Base):
    """`haf_<id>_<secret>`. Only the SHA-256 of the whole key is kept."""
    __tablename__ = "api_keys"
    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=_id)
    name: Mapped[str] = mapped_column(String(120))
    secret_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)  # the key acts as this user
    role: Mapped[str] = mapped_column(String(16), default="viewer")  # never above the owner's role
    workspace_id: Mapped[str | None] = mapped_column(String(16), nullable=True)  # pin to one workspace
    created_by: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_ip: Mapped[str] = mapped_column(String(64), default="")
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=_id)
    slug: Mapped[str] = mapped_column(String(60), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    daily_gpu_runs: Mapped[int] = mapped_column(Integer, default=0)  # 0 = unlimited
    max_queued: Mapped[int] = mapped_column(Integer, default=0)  # 0 = unlimited
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Membership(Base):
    __tablename__ = "memberships"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AuditEvent(Base):
    """Append-only. Nothing in the hub updates or deletes these rows."""
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    actor: Mapped[str] = mapped_column(String(254), default="")  # email, key name or "legacy-token"
    user_id: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    key_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    action: Mapped[str] = mapped_column(String(60), index=True)  # e.g. auth.login, run.submit
    target_type: Mapped[str] = mapped_column(String(40), default="")
    target_id: Mapped[str] = mapped_column(String(64), default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    ip: Mapped[str] = mapped_column(String(64), default="")


class SettingOverride(Base):
    """Admin overrides of environment settings (worker URLs, model ids) and pipeline switches."""
    __tablename__ = "setting_overrides"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)  # {"v": ...}
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_by: Mapped[str] = mapped_column(String(254), default="")
