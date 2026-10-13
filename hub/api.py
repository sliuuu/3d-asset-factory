"""HTTP API and static UI host."""
from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import or_, select

from . import __version__, account_api, admin_api, auth, convert, inspect_mesh, service, storage
from .adapters import ADAPTERS, InputError, PolicyError, check_egress
from .auth import Principal, audit, require
from .auth import db_dep as db
from .config import get_settings
from .db import init_engine
from .migrate import default_workspace
from .models import (CLASSIFICATIONS, EVIDENCE_CLASSES, Artifact, Asset, InputSet, Review, Run, Suite,
                     SuiteCase, Version, Workspace)
from .runtime_settings import describe_adapters, disabled_adapters

VIEWS = ("front", "back", "left", "right", "top", "service", "other")
IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}
MODEL_EXT = {"glb", "gltf", "obj", "stl", "ply"}
# The hub may move an asset to technical_validated on its own evidence. Visual, legal and approval
# states need a named reviewer, and `approved` is only granted in dt-component-library (Gate C).
HUB_STATES = ("provisional", "technical_validated", "visual_validated", "deprecated")

@asynccontextmanager
async def lifespan(_app):
    settings = get_settings()
    if not settings.auth_enabled and not settings.dev:
        raise RuntimeError("HUB_AUTH=off is for development only; set HUB_DEV=1 as well, or turn auth back on")
    init_engine()
    with auth.new_session() as s:
        auth.bootstrap_admin(s)
    yield


app = FastAPI(title="3D Asset Factory Hub", version=__version__, lifespan=lifespan)


viewer = Depends(require("viewer"))
creator = Depends(require("creator"))
reviewer = Depends(require("reviewer"))


def _asset(s, p: Principal, asset_id: str) -> Asset:
    """Assets outside the caller's workspaces look the same as missing ones."""
    a = s.get(Asset, asset_id)
    if a is None or not p.can_see(a.workspace_id):
        _404("asset")
    return a


def _version(s, p: Principal, version_id: str) -> Version:
    v = s.get(Version, version_id)
    if v is None or not p.can_see(v.asset.workspace_id):
        _404("version")
    return v


def _workspace_for_new(s, p: Principal, workspace_id: str | None) -> Workspace:
    if workspace_id:
        ws = s.get(Workspace, workspace_id)
        if ws is None or not p.can_see(ws.id):
            raise HTTPException(422, "unknown workspace")
        return ws
    if p.workspace_ids is None:
        return default_workspace(s)
    if not p.workspace_ids:
        raise HTTPException(403, "you are not a member of any workspace; ask an admin")
    default = default_workspace(s)
    return default if default.id in p.workspace_ids else s.get(Workspace, sorted(p.workspace_ids)[0])


# ---------- serialisers ----------

def ser_artifact(a: Artifact) -> dict:
    return {"id": a.id, "role": a.role, "format": a.format, "sha256": a.sha256, "size_bytes": a.size_bytes,
            "filename": a.filename, "url": f"/api/blobs/{a.sha256}"}


def ser_version(v: Version, brief: bool = False) -> dict:
    out = {"id": v.id, "asset_id": v.asset_id, "label": v.label, "state": v.state, "evidence_class": v.evidence_class,
           "pipeline": v.pipeline, "run_id": v.run_id, "created_at": v.created_at.isoformat(),
           "intake_status": v.intake.get("status"),
           "artifacts": [ser_artifact(a) for a in v.artifacts]}
    if not brief:
        out.update(metrics=v.metrics, intake=v.intake, lineage=v.lineage,
                   reviews=[{"reviewer": r.reviewer, "verdict": r.verdict, "score": r.score, "notes": r.notes,
                             "created_at": r.created_at.isoformat()} for r in v.reviews])
    else:
        m = v.metrics
        out["metrics"] = {k: m.get(k) for k in ("triangles", "watertight", "shells", "max_map_px")}
    return out


def ser_asset(a: Asset, brief: bool = False) -> dict:
    out = {"id": a.id, "name": a.name, "family": a.family, "site": a.site, "description": a.description,
           "tags": a.tags, "library_ref": a.library_ref, "evidence_refs": a.evidence_refs, "envelope": a.envelope,
           "workspace_id": a.workspace_id, "created_by": a.created_by,
           "created_at": a.created_at.isoformat(), "version_count": len(a.versions)}
    latest = a.versions[-1] if a.versions else None
    out["latest"] = ser_version(latest, brief=True) if latest else None
    if not brief:
        out["versions"] = [ser_version(v) for v in a.versions]
    return out


def ser_run(r: Run) -> dict:
    return {"id": r.id, "kind": r.kind, "adapter": r.adapter, "asset_id": r.asset_id, "input_set_id": r.input_set_id,
            "batch_id": r.batch_id, "params": r.params, "status": r.status, "error": r.error,
            "worker_info": r.worker_info, "events": r.events, "created_at": r.created_at.isoformat(),
            "workspace_id": r.workspace_id, "created_by": r.created_by,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None}


# ---------- system ----------

@app.get("/api/health")
def health(s=Depends(db)):
    # Unauthenticated liveness probe: no names, URLs or counts beyond what the UI needs to boot.
    return {"ok": True, "version": __version__, "formats": convert.available_formats(),
            "adapters": {a["name"]: a["available"] for a in describe_adapters(s)}}


@app.get("/api/adapters", dependencies=[viewer])
def adapters(s=Depends(db)):
    return describe_adapters(s)


# ---------- assets ----------

class AssetIn(BaseModel):
    name: str
    family: str = ""
    site: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    library_ref: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    envelope: dict = Field(default_factory=dict)
    workspace_id: str | None = None


@app.post("/api/assets")
def create_asset(body: AssetIn, s=Depends(db), p: Principal = creator):
    ws = _workspace_for_new(s, p, body.workspace_id)
    a = Asset(**body.model_dump(exclude={"workspace_id"}), workspace_id=ws.id, created_by=p.user_id)
    s.add(a)
    s.flush()
    audit(s, p, "asset.create", "asset", a.id, {"name": a.name, "workspace": ws.slug})
    s.commit()
    return ser_asset(a)


@app.patch("/api/assets/{asset_id}")
def update_asset(asset_id: str, body: dict, s=Depends(db), p: Principal = creator):
    a = _asset(s, p, asset_id)
    changed = {}
    for key in ("name", "family", "site", "description", "tags", "library_ref", "evidence_refs", "envelope"):
        if key in body:
            setattr(a, key, body[key])
            changed[key] = body[key]
    if body.get("workspace_id") and body["workspace_id"] != a.workspace_id:
        ws = _workspace_for_new(s, p, body["workspace_id"])
        a.workspace_id = ws.id
        changed["workspace"] = ws.slug
    audit(s, p, "asset.update", "asset", a.id, changed)
    s.commit()
    return ser_asset(a)


@app.get("/api/assets")
def list_assets(q: str = "", family: str = "", site: str = "", state: str = "", pipeline: str = "",
                evidence_class: str = "", workspace_id: str = "", s=Depends(db), p: Principal = viewer):
    stmt = select(Asset).order_by(Asset.created_at.desc())
    if p.workspace_ids is not None:
        stmt = stmt.where(Asset.workspace_id.in_(p.workspace_ids))
    if workspace_id:
        stmt = stmt.where(Asset.workspace_id == workspace_id)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Asset.name.ilike(like), Asset.description.ilike(like), Asset.family.ilike(like),
                              Asset.site.ilike(like)))
    if family:
        stmt = stmt.where(Asset.family == family)
    if site:
        stmt = stmt.where(Asset.site == site)
    assets = list(s.scalars(stmt))
    if state or pipeline or evidence_class:
        def keep(a):
            return any((not state or v.state == state) and (not pipeline or v.pipeline == pipeline)
                       and (not evidence_class or v.evidence_class == evidence_class) for v in a.versions)
        assets = [a for a in assets if keep(a)]
    return [ser_asset(a, brief=True) for a in assets]


@app.get("/api/assets/{asset_id}")
def get_asset(asset_id: str, s=Depends(db), p: Principal = viewer):
    a = _asset(s, p, asset_id)
    out = ser_asset(a)
    out["runs"] = [ser_run(r) for r in s.scalars(select(Run).where(Run.asset_id == a.id).order_by(Run.created_at.desc()))]
    return out


# ---------- inputs ----------

@app.post("/api/inputs")
async def create_inputs(images: list[UploadFile] = File(default=[]), views: str = Form("[]"), prompt: str = Form(""),
                        classification: str = Form("internal"), provenance: str = Form(""), licence: str = Form(""),
                        s=Depends(db), p: Principal = creator):
    if classification not in CLASSIFICATIONS:
        raise HTTPException(422, f"classification must be one of {CLASSIFICATIONS}")
    view_list = json.loads(views or "[]")
    stored = []
    for i, up in enumerate(images):
        if up.content_type not in IMAGE_TYPES:
            raise HTTPException(415, f"{up.filename}: images must be PNG, JPEG or WebP")
        view = view_list[i] if i < len(view_list) else "other"
        if view not in VIEWS:
            raise HTTPException(422, f"view must be one of {VIEWS}")
        sha, size = storage.put_bytes(await up.read())
        stored.append({"sha256": sha, "view": view, "filename": up.filename, "mime": up.content_type, "size": size})
    if not stored and not prompt.strip():
        raise HTTPException(422, "provide images or a prompt")
    inp = InputSet(prompt=prompt, images=stored, classification=classification, provenance=provenance, licence=licence)
    s.add(inp)
    s.flush()
    audit(s, p, "input.upload", "input_set", inp.id, {"images": len(stored), "classification": classification})
    s.commit()
    return {"id": inp.id, "images": stored, "prompt": prompt, "classification": classification}


# ---------- runs ----------

class RunIn(BaseModel):
    asset_id: str
    input_set_id: str | None = None
    workflow: str
    adapters: list[str]  # several = side-by-side comparison batch
    params: dict = Field(default_factory=dict)


@app.post("/api/runs")
def create_runs(body: RunIn, s=Depends(db), p: Principal = creator):
    asset = _asset(s, p, body.asset_id)
    _check_runs_allowed(s, p, asset, body.adapters)
    inp = s.get(InputSet, body.input_set_id) if body.input_set_id else InputSet(prompt="", classification="internal")
    if body.input_set_id is None:
        s.add(inp)
        s.flush()
    batch = uuid.uuid4().hex[:16] if len(body.adapters) > 1 else ""
    try:
        runs = [service.submit_generation(s, asset=asset, adapter_name=name, workflow=body.workflow, input_set=inp,
                                          params=body.params, batch_id=batch, created_by=p.user_id)
                for name in body.adapters]
    except InputError as exc:
        raise HTTPException(422, str(exc)) from exc
    for r in runs:
        audit(s, p, "run.submit", "run", r.id, {"adapter": r.adapter, "asset": asset.id, "status": r.status,
                                                 "workflow": r.params.get("workflow"), "batch": batch})
    s.commit()
    return {"batch_id": batch, "runs": [ser_run(r) for r in runs]}


def _check_runs_allowed(s, p: Principal, asset: Asset, adapter_names: list[str]) -> None:
    off = disabled_adapters(s) & set(adapter_names)
    if off:
        raise HTTPException(409, f"switched off by an admin: {sorted(off)}")
    gpu = sum(1 for n in adapter_names if n in ADAPTERS and ADAPTERS[n].needs_gpu)
    ws = s.get(Workspace, asset.workspace_id) if asset.workspace_id else default_workspace(s)
    auth.check_quota(s, p, ws, gpu_runs=gpu, total_runs=len(adapter_names))


@app.get("/api/runs")
def list_runs(status: str = "", batch_id: str = "", mine: bool = False, limit: int = Query(100, le=500),
              s=Depends(db), p: Principal = viewer):
    stmt = select(Run).order_by(Run.created_at.desc()).limit(limit)
    if p.workspace_ids is not None:
        stmt = stmt.where(Run.workspace_id.in_(p.workspace_ids))
    if mine:
        stmt = stmt.where(Run.created_by == p.user_id)
    if status:
        stmt = stmt.where(Run.status == status)
    if batch_id:
        stmt = stmt.where(Run.batch_id == batch_id)
    return [ser_run(r) for r in s.scalars(stmt)]


@app.get("/api/runs/{run_id}")
def get_run(run_id: str, s=Depends(db), p: Principal = viewer):
    r = s.get(Run, run_id)
    if r is None or not p.can_see(r.workspace_id):
        _404("run")
    return ser_run(r)


# ---------- import ----------

@app.post("/api/assets/{asset_id}/import")
async def import_model(asset_id: str, file: UploadFile = File(...), source: str = Form("upload"),
                       evidence_class: str = Form(""), label: str = Form(""), provenance: str = Form(""),
                       licence: str = Form(""), input_classification: str = Form("internal"),
                       source_tool: str = Form(""), derived_from: str = Form(""), s=Depends(db),
                       p: Principal = creator):
    asset = _asset(s, p, asset_id)
    adapter = ADAPTERS.get(source)
    if adapter is None or "import" not in adapter.workflows:
        raise HTTPException(422, "source must be 'external' or 'upload'")
    try:
        check_egress(adapter, input_classification)
    except PolicyError as exc:
        raise HTTPException(403, str(exc)) from exc
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    if ext not in MODEL_EXT:
        raise HTTPException(415, f"model must be one of {sorted(MODEL_EXT)}")
    evidence = evidence_class or adapter.evidence_class
    if evidence not in EVIDENCE_CLASSES:
        raise HTTPException(422, f"evidence_class must be one of {EVIDENCE_CLASSES}")
    run = Run(kind="import", adapter=source, asset_id=asset.id, status="succeeded",
              params={"workflow": "import", "source_tool": source_tool, "input_classification": input_classification},
              worker_info={"model": source_tool or source}, workspace_id=asset.workspace_id, created_by=p.user_id)
    s.add(run)
    s.flush()
    try:
        v = service.ingest_model(s, asset=asset, data=await file.read(), filename=file.filename, pipeline=source,
                                 evidence_class=evidence, run=run, label=label or source_tool or file.filename,
                                 derived_from=[d for d in derived_from.split(",") if d], provenance=provenance,
                                 licence=licence)
    except ValueError as exc:
        raise HTTPException(422, f"could not read model: {exc}") from exc
    service.log(run, f"imported {file.filename}")
    audit(s, p, "asset.import", "version", v.id, {"asset": asset.id, "source": source, "filename": file.filename,
                                                   "evidence_class": evidence})
    s.commit()
    return ser_version(v)


# ---------- versions ----------

@app.get("/api/versions/{version_id}")
def get_version(version_id: str, s=Depends(db), p: Principal = viewer):
    return ser_version(_version(s, p, version_id))


class ReviewIn(BaseModel):
    reviewer: str = ""  # ignored for signed-in users: the review is recorded under their own name
    verdict: str  # pass | fail | notes
    score: float | None = None
    notes: str = ""
    logo_or_text_present: bool | None = None


@app.post("/api/versions/{version_id}/reviews")
def add_review(version_id: str, body: ReviewIn, s=Depends(db), p: Principal = reviewer):
    v = _version(s, p, version_id)
    if body.verdict not in ("pass", "fail", "notes"):
        raise HTTPException(422, "verdict must be pass, fail or notes")
    name = p.reviewer_name or body.reviewer
    if not name:
        raise HTTPException(422, "a review needs a named reviewer")
    s.add(Review(version_id=v.id, reviewer=name, verdict=body.verdict, score=body.score, notes=body.notes))
    if body.logo_or_text_present is not None:
        intake = dict(v.intake)
        checks = dict(intake.get("checks", {}))
        checks["logo_or_text_free"] = {"pass": not body.logo_or_text_present, "note": f"set by {name}"}
        intake["checks"] = checks
        hard = [c["pass"] for c in checks.values() if c.get("pass") is not None]
        pending = [k for k, c in checks.items() if c.get("pass") is None and k not in ("shells", "hero_budget")]
        intake["status"] = "fail" if not all(hard) else ("pending" if pending else "pass")
        intake["pending"] = pending
        v.intake = intake
    audit(s, p, "version.review", "version", v.id, {"verdict": body.verdict, "score": body.score, "reviewer": name})
    s.commit()
    return ser_version(v)


class StateIn(BaseModel):
    state: str
    reviewer: str = ""


@app.post("/api/versions/{version_id}/state")
def set_state(version_id: str, body: StateIn, s=Depends(db), p: Principal = reviewer):
    v = _version(s, p, version_id)
    name = p.reviewer_name or body.reviewer
    if body.state not in HUB_STATES:
        raise HTTPException(422, "legal_cleared and approved are granted in dt-component-library (Gate C), not the hub")
    if body.state == "technical_validated" and v.intake.get("status") != "pass":
        raise HTTPException(409, "intake gate must pass first")
    if body.state == "visual_validated":
        if not name:
            raise HTTPException(422, "visual_validated needs a named reviewer")
        if not any(r.verdict == "pass" and r.reviewer == name for r in v.reviews):
            raise HTTPException(409, "the named reviewer must have recorded a pass review")
    audit(s, p, "version.state", "version", v.id, {"from": v.state, "to": body.state})
    v.state = body.state
    s.commit()
    return ser_version(v)


@app.get("/api/versions/{version_id}/download")
def download(version_id: str, format: str = "glb", s=Depends(db), p: Principal = viewer):
    v = _version(s, p, version_id)
    model = next((a for a in v.artifacts if a.role == "model"), None) or _404("model artifact")
    src = storage.path_for(model.sha256)
    if model.format != "glb" and format == model.format:
        return FileResponse(src, filename=model.filename)
    if model.format != "glb":
        raise HTTPException(409, "conversion needs a GLB master; this version was imported as " + model.format)
    try:
        data, suffix = convert.export(src, format)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    stem = "".join(c if c.isalnum() or c in "-_" else "_" for c in (v.asset.name + "-" + v.id))
    return Response(data, media_type="application/octet-stream",
                    headers={"content-disposition": f'attachment; filename="{stem}{suffix}"'})


# Blobs are content-addressed (SHA-256) and readable by any signed-in user; access to the records that
# name a hash is what workspaces scope.
@app.get("/api/blobs/{sha256}", dependencies=[viewer])
def blob(sha256: str):
    try:
        p = storage.path_for(sha256)
    except ValueError:
        raise HTTPException(400, "bad hash") from None
    if not p.exists():
        _404("blob")
    return FileResponse(p, media_type="model/gltf-binary" if p.read_bytes()[:4] == b"glTF" else "application/octet-stream")


# ---------- compare ----------

class CompareIn(BaseModel):
    version_ids: list[str]
    reference_version_id: str | None = None


COMPARE_FIELDS = ("triangles", "vertices", "watertight", "degenerate_faces", "shells", "largest_shell_share",
                  "extents_lwh", "triangle_aspect_p99", "max_map_px", "uv_area_ratio", "normal_map_strength",
                  "texel_density_px_per_unit")


@app.post("/api/compare")
def compare(body: CompareIn, s=Depends(db), p: Principal = viewer):
    if not 2 <= len(body.version_ids) <= 6:
        raise HTTPException(422, "compare 2 to 6 versions")
    versions = [_version(s, p, vid) for vid in body.version_ids]
    ref = _version(s, p, body.reference_version_id) if body.reference_version_id else None
    ref_mesh = _mesh(ref) if ref else None
    rows = []
    for v in versions:
        model = next(a for a in v.artifacts if a.role == "model")
        row = {"version_id": v.id, "asset": v.asset.name, "label": v.label, "pipeline": v.pipeline,
               "evidence_class": v.evidence_class, "state": v.state, "size_bytes": model.size_bytes,
               "glb_url": f"/api/blobs/{model.sha256}", "intake_status": v.intake.get("status"),
               "envelope_fit": v.intake.get("checks", {}).get("envelope_fit", {}).get("value"),
               "seconds": (v.run_id and _run_seconds(s, v.run_id)) or None,
               **{k: v.metrics.get(k) for k in COMPARE_FIELDS}}
        if ref_mesh is not None and v.id != ref.id:
            row["deviation_vs_reference"] = inspect_mesh.deviation(_mesh(v), ref_mesh)
        rows.append(row)
    return {"reference_version_id": ref.id if ref else None, "rows": rows}


def _mesh(v: Version):
    model = next(a for a in v.artifacts if a.role == "model")
    return inspect_mesh.mesh_from_blob(storage.path_for(model.sha256).read_bytes(), model.format)


def _run_seconds(s, run_id: str):
    r = s.get(Run, run_id)
    if r and r.worker_info.get("seconds"):
        return r.worker_info["seconds"]
    if r and r.started_at and r.finished_at:
        return (r.finished_at - r.started_at).total_seconds()
    return None


# ---------- benchmarks ----------

class SuiteIn(BaseModel):
    name: str
    description: str = ""
    pipelines: list[str]


class CaseIn(BaseModel):
    asset_id: str
    input_set_id: str | None = None
    reference_version_id: str | None = None


@app.post("/api/suites")
def create_suite(body: SuiteIn, s=Depends(db), p: Principal = creator):
    unknown = [p for p in body.pipelines if p not in ADAPTERS]
    if unknown:
        raise HTTPException(422, f"unknown pipelines {unknown}")
    suite = Suite(**body.model_dump())
    s.add(suite)
    s.flush()
    audit(s, p, "suite.create", "suite", suite.id, {"name": suite.name, "pipelines": suite.pipelines})
    s.commit()
    return {"id": suite.id, **body.model_dump()}


@app.post("/api/suites/{suite_id}/cases")
def add_case(suite_id: str, body: CaseIn, s=Depends(db), p: Principal = creator):
    suite = s.get(Suite, suite_id) or _404("suite")
    _asset(s, p, body.asset_id)
    case = SuiteCase(suite_id=suite.id, **body.model_dump())
    s.add(case)
    s.commit()
    return {"id": case.id, **body.model_dump()}


@app.get("/api/suites", dependencies=[viewer])
def list_suites(s=Depends(db)):
    return [{"id": x.id, "name": x.name, "description": x.description, "pipelines": x.pipelines,
             "cases": len(x.cases)} for x in s.scalars(select(Suite).order_by(Suite.created_at.desc()))]


@app.post("/api/suites/{suite_id}/run")
def run_suite(suite_id: str, workflow: str = "multiview", s=Depends(db), p: Principal = creator):
    suite = s.get(Suite, suite_id) or _404("suite")
    batch = uuid.uuid4().hex[:16]
    pipelines = [n for n in suite.pipelines if "import" not in ADAPTERS[n].workflows]
    cases = [c for c in suite.cases if c.input_set_id]
    by_ws: dict[str, tuple[Asset, list[str]]] = {}  # quota is checked once per workspace for the whole batch
    for case in cases:
        asset = _asset(s, p, case.asset_id)
        by_ws.setdefault(asset.workspace_id, (asset, []))[1].extend(pipelines)
    for asset, names in by_ws.values():
        _check_runs_allowed(s, p, asset, names)
    runs = []
    for case in cases:
        inp = s.get(InputSet, case.input_set_id)
        asset = s.get(Asset, case.asset_id)
        for name in pipelines:
            wf = workflow if workflow in ADAPTERS[name].workflows else ADAPTERS[name].workflows[0]
            runs.append(service.submit_generation(s, asset=asset, adapter_name=name, workflow=wf, input_set=inp,
                                                  params={}, batch_id=batch, suite_case_id=case.id,
                                                  created_by=p.user_id))
    audit(s, p, "suite.run", "suite", suite.id, {"batch": batch, "runs": len(runs)})
    s.commit()
    return {"batch_id": batch, "runs": [ser_run(r) for r in runs]}


@app.get("/api/suites/{suite_id}/report")
def suite_report(suite_id: str, s=Depends(db), p: Principal = viewer):
    """Latest version per (case, pipeline), with metrics and deviation against the case reference."""
    suite = s.get(Suite, suite_id) or _404("suite")
    table = []
    for case in suite.cases:
        asset = s.get(Asset, case.asset_id)
        if not p.can_see(asset.workspace_id):
            continue
        ref = s.get(Version, case.reference_version_id) if case.reference_version_id else None
        ref_mesh = _mesh(ref) if ref else None
        for p in suite.pipelines:
            v = next((v for v in reversed(asset.versions) if v.pipeline == p), None)
            row = {"asset": asset.name, "asset_id": asset.id, "pipeline": p, "version_id": v.id if v else None}
            if v:
                row.update({k: v.metrics.get(k) for k in ("triangles", "watertight", "shells", "max_map_px",
                                                          "normal_map_strength", "texel_density_px_per_unit")})
                row["envelope_fit"] = v.intake.get("checks", {}).get("envelope_fit", {}).get("value")
                row["intake_status"] = v.intake.get("status")
                row["reviews"] = [{"reviewer": r.reviewer, "verdict": r.verdict, "score": r.score} for r in v.reviews]
                row["seconds"] = _run_seconds(s, v.run_id) if v.run_id else None
                if ref_mesh is not None and v.id != ref.id:
                    row["deviation_vs_reference"] = inspect_mesh.deviation(_mesh(v), ref_mesh)
            table.append(row)
    return {"suite": suite.name, "pipelines": suite.pipelines, "rows": table}


def _404(what: str):
    raise HTTPException(404, f"{what} not found")


# ---------- accounts and admin ----------

app.include_router(account_api.router)
app.include_router(admin_api.router)


# ---------- UI ----------
# Three pages: the creation app (/), sign-in (/login) and the Admin portal (/admin). The pages are static;
# the API decides what each person may see or do.

_dist = Path(__file__).resolve().parent.parent / "web" / "dist"


@app.get("/login", include_in_schema=False)
def login_page():
    return FileResponse(_dist / "login.html") if (_dist / "login.html").exists() else RedirectResponse("/")


@app.get("/admin", include_in_schema=False)
def admin_page():
    return FileResponse(_dist / "admin.html") if (_dist / "admin.html").exists() else RedirectResponse("/")


if _dist.exists():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="ui")
