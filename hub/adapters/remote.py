"""Adapters that call a GPU worker over HTTP (workers/gpu/server.py implements the other side).

Protocol: POST {base}/generate, multipart form
  model=<model id>, workflow=<image|multiview|text>, prompt=<str>, params=<json>,
  image files named by view (front, back, left, right, top, service, other)
Response: 200 model/gltf-binary body, header X-Worker-Info = JSON provenance
  {model, weights_sha256, repo_commit, seed, seconds, device}
"""
from __future__ import annotations

import json

import httpx

from .base import Adapter, ExecResult, InputError, OutputFile


class RemoteGpuAdapter(Adapter):
    url_setting: str = ""
    model_id: str = ""
    allowed_views: tuple[str, ...] = ()

    def __init__(self, *, url_setting: str, model_id: str, allowed_views: tuple[str, ...] = (), **kw):
        super().__init__(**kw)
        self.url_setting = url_setting
        self.model_id = model_id
        self.allowed_views = allowed_views

    def base_url(self, settings) -> str:
        return getattr(settings, self.url_setting, "")

    def available(self, settings):
        if not self.base_url(settings):
            return False, f"no worker configured ({self.url_setting.upper().replace('_URL', '')} URL unset)"
        return True, ""

    def prepare(self, *, workflow, prompt, images, params):
        payload = super().prepare(workflow=workflow, prompt=prompt, images=images, params=params)
        if workflow == "image" and len(images) > 1:
            # Single-image models get the front view (or the first image) from a view set.
            payload["images"] = [next((i for i in images if i.get("view") == "front"), images[0])]
        if workflow == "multiview" and self.allowed_views:
            bad = [i["view"] for i in images if i.get("view") not in self.allowed_views]
            if bad:
                raise InputError(f"{self.label} accepts views {self.allowed_views}; got {bad}")
        return payload

    def execute(self, payload, settings, read_blob) -> ExecResult:
        files = []
        for i, img in enumerate(payload["images"]):
            files.append(("images", (f"{img.get('view') or 'other'}-{i}{_ext(img)}", read_blob(img["sha256"]),
                                     img.get("mime") or "image/png")))
        data = {
            "model": self.model_id,
            "workflow": payload["workflow"],
            "prompt": payload["prompt"],
            "params": json.dumps(payload["params"]),
            "views": json.dumps([img.get("view") or "other" for img in payload["images"]]),
        }
        timeout = httpx.Timeout(connect=10, read=float(payload["params"].get("timeout_s", 1800)), write=60, pool=10)
        r = httpx.post(f"{self.base_url(settings).rstrip('/')}/generate", data=data, files=files or None, timeout=timeout)
        if r.status_code != 200:
            raise RuntimeError(f"worker returned {r.status_code}: {r.text[:500]}")
        info = json.loads(r.headers.get("x-worker-info", "{}"))
        return ExecResult(files=[OutputFile("model.glb", r.content)], worker_info=info)


def _ext(img: dict) -> str:
    name = img.get("filename") or ""
    return name[name.rfind("."):] if "." in name else ".png"
