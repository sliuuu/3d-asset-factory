"""Domain operations shared by the API and the worker."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from . import inspect_mesh, storage
from .adapters import ADAPTERS, InputError, PolicyError, check_egress
from .config import get_settings
from .envelope import fit as envelope_fit
from .models import Artifact, Asset, InputSet, Run, Version
from .runtime_settings import disabled_adapters, effective_settings


def _now():
    return datetime.now(timezone.utc)


def log(run: Run, msg: str) -> None:
    run.events = [*run.events, {"at": _now().isoformat(), "msg": msg}]


def ingest_model(db: Session, *, asset: Asset, data: bytes, filename: str, pipeline: str,
                 evidence_class: str, run: Run | None = None, label: str = "",
                 derived_from: list[str] | None = None, provenance: str = "", licence: str = "") -> Version:
    """Store a model file, inspect it, run the intake gate, and record a provisional version."""
    fmt = filename.rsplit(".", 1)[-1].lower()
    sha, size = storage.put_bytes(data)
    metrics = inspect_mesh.inspect(storage.path_for(sha), file_type=fmt)
    fit = envelope_fit(tuple(metrics["extents_lwh"]), asset.envelope) if asset.envelope else None
    intake = inspect_mesh.intake_gate(metrics, fit=fit, provenance=provenance, licence=licence, logo_flag=None)
    version = Version(
        asset_id=asset.id, label=label or filename, pipeline=pipeline, evidence_class=evidence_class,
        run_id=run.id if run else None, metrics=metrics, intake=intake,
        lineage={"derived_from": derived_from or []},
    )
    db.add(version)
    db.flush()
    db.add(Artifact(version_id=version.id, role="model", format=fmt, sha256=sha, size_bytes=size, filename=filename))
    return version


def submit_generation(db: Session, *, asset: Asset, adapter_name: str, workflow: str,
                      input_set: InputSet, params: dict, batch_id: str = "", suite_case_id: str | None = None,
                      created_by: str | None = None) -> Run:
    adapter = ADAPTERS.get(adapter_name)
    if adapter is None:
        raise InputError(f"unknown adapter {adapter_name}")
    if workflow == "multiview" and "multiview" not in adapter.workflows and "image" in adapter.workflows:
        workflow = "image"  # comparison batches: single-image models take the front view
    run = Run(kind="generate", adapter=adapter_name, asset_id=asset.id, input_set_id=input_set.id,
              params={"workflow": workflow, **params}, needs_gpu=int(adapter.needs_gpu),
              batch_id=batch_id, suite_case_id=suite_case_id, workspace_id=asset.workspace_id, created_by=created_by)
    try:
        check_egress(adapter, input_set.classification)
        adapter.prepare(workflow=workflow, prompt=input_set.prompt, images=input_set.images, params=params)
    except (InputError, PolicyError) as exc:
        run.status = "rejected"
        run.error = str(exc)
    db.add(run)
    db.flush()
    log(run, "rejected: " + run.error if run.status == "rejected" else f"queued for {adapter.label}")
    return run


def claim(db: Session, worker_id: str, *, gpu: bool | None = None) -> Run | None:
    """Claim the oldest queued run (or one whose lease expired). One GPU run at a time:
    the DGX's unified memory is shared with Ollama, vLLM and ComfyUI."""
    now = _now()
    settings = get_settings()
    if gpu:
        busy = db.scalar(select(Run).where(Run.status == "running", Run.needs_gpu == 1, Run.lease_until > now))
        if busy is not None:
            return None
    q = select(Run).where(or_(Run.status == "queued",
                              (Run.status == "running") & (Run.lease_until < now)))
    if gpu is not None:
        q = q.where(Run.needs_gpu == int(gpu))
    run = db.scalar(q.order_by(Run.created_at).limit(1).with_for_update(skip_locked=True))
    if run is None:
        return None
    run.status = "running"
    run.lease_owner = worker_id
    run.lease_until = now + timedelta(seconds=settings.lease_seconds)
    run.started_at = run.started_at or now
    log(run, f"claimed by {worker_id}")
    db.commit()
    return run


def execute(db: Session, run: Run) -> None:
    settings = effective_settings(db)  # admin overrides of worker URLs apply without a redeploy
    adapter = ADAPTERS[run.adapter]
    asset = db.get(Asset, run.asset_id)
    input_set = db.get(InputSet, run.input_set_id) if run.input_set_id else None
    params = {k: v for k, v in run.params.items() if k != "workflow"}
    try:
        if adapter.name in disabled_adapters(db):
            raise PolicyError(f"{adapter.label} was switched off by an admin after this run was queued")
        check_egress(adapter, input_set.classification if input_set else "internal")
        payload = adapter.prepare(workflow=run.params["workflow"], prompt=input_set.prompt if input_set else "",
                                  images=input_set.images if input_set else [], params=params)
        result = adapter.execute(payload, settings, lambda sha: storage.path_for(sha).read_bytes())
        run.worker_info = result.worker_info
        for out in result.files:
            if out.role == "model":
                ingest_model(db, asset=asset, data=out.data, filename=out.filename, pipeline=adapter.name,
                             evidence_class=adapter.evidence_class, run=run,
                             label=f"{adapter.label} · {run.params['workflow']}",
                             provenance=input_set.provenance if input_set else "", licence=adapter.licence)
        run.status = "succeeded"
        log(run, "succeeded")
    except Exception as exc:  # noqa: BLE001  the run records the failure; the worker keeps going
        db.rollback()
        run = db.get(Run, run.id)
        run.status = "failed"
        run.error = f"{type(exc).__name__}: {exc}"
        log(run, "failed: " + run.error)
    run.finished_at = _now()
    run.lease_until = None
    db.commit()
