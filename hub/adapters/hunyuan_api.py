"""Adapter for a Hunyuan3D instance running Tencent's own `api_server.py` (Hunyuan3D-2 / 2.1 repos).

Use this when Hunyuan is already deployed in the lab with the upstream server, instead of the
hub's `workers/gpu` container. Upstream protocol:
  POST {base}/generate, JSON {image: <base64>, texture, seed, octree_resolution,
       num_inference_steps, guidance_scale, face_count, remove_background}
  200 -> GLB body
The upstream server takes one image, so a view set sends its front view. Its multi-view model
(2mv) is not exposed by `api_server.py`; use the hub worker for that.
"""
from __future__ import annotations

import base64
import time

import httpx

from .base import ExecResult, OutputFile
from .remote import RemoteGpuAdapter

# Upstream request fields the hub passes through from run params; anything else stays in the hub.
PASSTHROUGH = ("texture", "seed", "octree_resolution", "num_inference_steps", "guidance_scale",
               "face_count", "num_chunks", "remove_background")
DEFAULTS = {"texture": True, "seed": 1234, "remove_background": True}


class HunyuanApiAdapter(RemoteGpuAdapter):
    def execute(self, payload, settings, read_blob) -> ExecResult:
        img = payload["images"][0]
        params = payload["params"]
        body = {**DEFAULTS, **{k: params[k] for k in PASSTHROUGH if k in params},
                "image": base64.b64encode(read_blob(img["sha256"])).decode()}
        timeout = httpx.Timeout(connect=10, read=float(params.get("timeout_s", 1800)), write=60, pool=10)
        t0 = time.monotonic()
        r = httpx.post(f"{self.base_url(settings).rstrip('/')}/generate", json=body, timeout=timeout)
        if r.status_code != 200:
            raise RuntimeError(f"Hunyuan api_server returned {r.status_code}: {r.text[:500]}")
        if not r.content.startswith(b"glTF"):
            raise RuntimeError(f"Hunyuan api_server did not return a GLB ({r.headers.get('content-type')})")
        info = {
            "model": settings.hunyuan_api_model or self.model_id,
            "server": "upstream api_server.py",
            "seed": body["seed"],
            "texture": body["texture"],
            "seconds": round(time.monotonic() - t0, 1),
            "view": img.get("view") or "other",
        }
        return ExecResult(files=[OutputFile("model.glb", r.content)], worker_info=info)
