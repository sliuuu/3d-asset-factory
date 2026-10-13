"""img2threejs-service (ai-server :8099): parametric racks. Despite its name it is not image-to-3D."""
from __future__ import annotations

import httpx

from .base import Adapter, ExecResult, InputError, OutputFile

ENDPOINTS = {"rack": "/v1/rack", "orv3": "/v1/orv3", "ai-rack": "/v1/ai-rack"}


class Img2ThreejsAdapter(Adapter):
    def available(self, settings):
        return (bool(settings.img2threejs_url), "" if settings.img2threejs_url else "HUB_IMG2THREEJS_URL unset")

    def prepare(self, *, workflow, prompt, images, params):
        payload = super().prepare(workflow=workflow, prompt=prompt, images=images, params=params)
        if params.get("family", "ai-rack") not in ENDPOINTS:
            raise InputError(f"family must be one of {sorted(ENDPOINTS)}")
        return payload

    def execute(self, payload, settings, read_blob) -> ExecResult:
        params = dict(payload["params"])
        family = params.pop("family", "ai-rack")
        base = settings.img2threejs_url.rstrip("/")
        with httpx.Client(timeout=120) as client:
            r = client.post(base + ENDPOINTS[family], json=params)
            r.raise_for_status()
            job = r.json()
            glb_path = job["artifacts"]["glb"]
            glb = client.get(glb_path if glb_path.startswith("http") else base + glb_path)
            glb.raise_for_status()
        info = {"model": f"img2threejs-service/{family}", "upstream_job": job.get("jobId"),
                "mode": job.get("mode", params.get("mode", "gated"))}
        return ExecResult(files=[OutputFile("model.glb", glb.content)], worker_info=info)
