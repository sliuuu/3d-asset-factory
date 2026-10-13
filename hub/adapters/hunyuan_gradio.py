"""Adapter for Hunyuan3D running Tencent's `gradio_app.py` (the lab's live setup since 2026-10-12:
DGX 10.0.1.106:8080, Hunyuan3D-2mv, `--disable_tex`, i.e. shape only).

The Gradio app has no fixed HTTP API, so this adapter asks the app for its endpoint list at call
time and fills whichever of these parameters the shape endpoint declares:
  image, mv_image_front/back/left/right, steps, guidance_scale, seed, octree_resolution,
  num_chunks, check_box_rembg, randomize_seed
That keeps it working across upstream app versions that rename or add inputs.
"""
from __future__ import annotations

import io
import tempfile
import time
from pathlib import Path

import trimesh

from .base import ExecResult, InputError, OutputFile
from .remote import RemoteGpuAdapter

VIEW_PARAM = {"front": "mv_image_front", "back": "mv_image_back", "left": "mv_image_left", "right": "mv_image_right"}
PARAM_MAP = {"steps": "steps", "guidance_scale": "guidance_scale", "seed": "seed",
             "octree_resolution": "octree_resolution", "num_chunks": "num_chunks"}
MESH_EXT = (".glb", ".gltf", ".obj", ".ply", ".stl")


def _client(url: str):
    from gradio_client import Client  # imported lazily: only the GPU dispatcher needs it
    return Client(url, verbose=False, download_files=tempfile.mkdtemp(prefix="hy3d-"))


def find_endpoint(api: dict, preferred: str) -> tuple[str, list[str]]:
    """Return (api_name, parameter names) for the shape-generation endpoint."""
    named = api.get("named_endpoints", {})
    names = [preferred] + sorted(n for n in named if "shape" in n) + sorted(named)
    for name in names:
        if name in named:
            params = [p.get("parameter_name") for p in named[name].get("parameters", [])]
            if any(p in ("image", "mv_image_front") for p in params):
                return name, params
    raise RuntimeError(f"no shape-generation endpoint found; app exposes {sorted(named)}")


def first_mesh_path(result) -> Path:
    items = result if isinstance(result, (list, tuple)) else [result]
    for item in items:
        path = item.get("path") or item.get("value") if isinstance(item, dict) else item
        if isinstance(path, str) and path.lower().endswith(MESH_EXT) and Path(path).exists():
            return Path(path)
    raise RuntimeError(f"Hunyuan Gradio app returned no mesh file: {str(result)[:300]}")


class HunyuanGradioAdapter(RemoteGpuAdapter):
    def prepare(self, *, workflow, prompt, images, params):
        payload = super().prepare(workflow=workflow, prompt=prompt, images=images, params=params)
        if workflow == "multiview" and not any(i.get("view") == "front" for i in payload["images"]):
            raise InputError("Hunyuan3D-2mv needs a front view")
        return payload

    def execute(self, payload, settings, read_blob) -> ExecResult:
        from gradio_client import handle_file

        client = _client(self.base_url(settings))
        api_name, accepted = find_endpoint(client.view_api(print_info=False, return_format="dict"),
                                           settings.hunyuan_gradio_api)
        params = payload["params"]
        kwargs: dict = {}
        with tempfile.TemporaryDirectory(prefix="hy3d-in-") as tmp:
            for i, img in enumerate(payload["images"]):
                view = img.get("view") or "other"
                key = VIEW_PARAM.get(view) if payload["workflow"] == "multiview" else "image"
                if key not in accepted or key in kwargs:
                    continue
                path = Path(tmp) / f"{view}-{i}.png"
                path.write_bytes(read_blob(img["sha256"]))
                kwargs[key] = handle_file(str(path))
            if not kwargs:
                raise InputError(f"none of the views fit the app's inputs {accepted}")
            for ours, theirs in PARAM_MAP.items():
                if ours in params and theirs in accepted:
                    kwargs[theirs] = params[ours]
            if "seed" in accepted:
                kwargs.setdefault("seed", 1234)
            if "check_box_rembg" in accepted:
                kwargs["check_box_rembg"] = bool(params.get("remove_background", True))
            if "randomize_seed" in accepted:
                kwargs["randomize_seed"] = False
            t0 = time.monotonic()
            mesh_path = first_mesh_path(client.predict(api_name=api_name, **kwargs))
        data = mesh_path.read_bytes()
        if not data.startswith(b"glTF"):
            data = trimesh.load(io.BytesIO(data), file_type=mesh_path.suffix.lstrip(".")).export(file_type="glb")
        info = {
            "model": settings.hunyuan_gradio_model or self.model_id,
            "server": "upstream gradio_app.py",
            "endpoint": api_name,
            "seed": kwargs.get("seed"),
            "views": sorted(k for k in kwargs if k.startswith(("image", "mv_image"))),
            "texture": False,
            "seconds": round(time.monotonic() - t0, 1),
        }
        return ExecResult(files=[OutputFile("model.glb", data)], worker_info=info)
