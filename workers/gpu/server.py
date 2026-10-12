"""GPU worker for the DGX Spark. One container per model family; set WORKER_BACKEND.

    WORKER_BACKEND=trellis2      microsoft/TRELLIS.2-4B          image -> GLB (PBR)
    WORKER_BACKEND=hunyuan       tencent/Hunyuan3D-2mv (+ 2.1)   multiview / image -> GLB
    WORKER_BACKEND=trellis-text  microsoft/TRELLIS-text-xlarge   text -> GLB

Implements the protocol in hub/adapters/remote.py. The model is loaded on first request and
unloaded after WORKER_IDLE_UNLOAD_S seconds idle, because the GB10's 128 GB is shared with
Ollama, vLLM and ComfyUI. Status: written against the upstream READMEs; NOT yet run on the DGX
(ARM64). Treat the first deployment as the test.
"""
from __future__ import annotations

import gc
import io
import json
import os
import tempfile
import threading
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from PIL import Image

BACKEND = os.environ.get("WORKER_BACKEND", "trellis2")
IDLE_UNLOAD_S = int(os.environ.get("WORKER_IDLE_UNLOAD_S", "600"))

app = FastAPI(title=f"3d-worker-{BACKEND}")
_lock = threading.Lock()
_state: dict = {"pipelines": {}, "last_used": 0.0, "revisions": {}}


def _revision(repo_id: str) -> str:
    """Commit hash of the downloaded snapshot: identifies the exact weights without hashing GBs."""
    try:
        from huggingface_hub import HfApi
        return HfApi().model_info(repo_id).sha or ""
    except Exception:  # noqa: BLE001  offline is fine; record what we can
        return ""


def _load(name: str):
    pipes = _state["pipelines"]
    if name in pipes:
        return pipes[name]
    import torch  # noqa: F401
    if name == "trellis2":
        from trellis2.pipelines import Trellis2ImageTo3DPipeline
        pipe = Trellis2ImageTo3DPipeline.from_pretrained("microsoft/TRELLIS.2-4B")
        pipe.cuda()
        _state["revisions"][name] = _revision("microsoft/TRELLIS.2-4B")
    elif name == "hunyuan-mv":
        from hy3dgen.shapegen import Hunyuan3DDiTFlowMatchingPipeline
        pipe = Hunyuan3DDiTFlowMatchingPipeline.from_pretrained(
            "tencent/Hunyuan3D-2mv", subfolder="hunyuan3d-dit-v2-mv", use_safetensors=True, device="cuda")
        _state["revisions"][name] = _revision("tencent/Hunyuan3D-2mv")
    elif name == "hunyuan-single":
        from hy3dgen.shapegen import Hunyuan3DDiTFlowMatchingPipeline
        pipe = Hunyuan3DDiTFlowMatchingPipeline.from_pretrained("tencent/Hunyuan3D-2.1", device="cuda")
        _state["revisions"][name] = _revision("tencent/Hunyuan3D-2.1")
    elif name == "hunyuan-paint":
        from hy3dgen.texgen import Hunyuan3DPaintPipeline
        pipe = Hunyuan3DPaintPipeline.from_pretrained("tencent/Hunyuan3D-2.1")
        _state["revisions"][name] = _revision("tencent/Hunyuan3D-2.1")
    elif name == "trellis-text":
        from trellis.pipelines import TrellisTextTo3DPipeline
        pipe = TrellisTextTo3DPipeline.from_pretrained("microsoft/TRELLIS-text-xlarge")
        pipe.cuda()
        _state["revisions"][name] = _revision("microsoft/TRELLIS-text-xlarge")
    else:
        raise HTTPException(400, f"unknown pipeline {name}")
    pipes[name] = pipe
    return pipe


def _unload_all():
    _state["pipelines"].clear()
    gc.collect()
    try:
        import torch
        torch.cuda.empty_cache()
    except Exception:  # noqa: BLE001
        pass


def _janitor():
    while True:
        time.sleep(30)
        with _lock:
            if _state["pipelines"] and time.time() - _state["last_used"] > IDLE_UNLOAD_S:
                _unload_all()


threading.Thread(target=_janitor, daemon=True).start()


def _rgba(data: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    img.load()
    return img.convert("RGBA")


def _remove_bg(img: Image.Image) -> Image.Image:
    """Background removal with BiRefNet (MIT). RMBG-2.0 is non-commercial and must not be used."""
    if os.environ.get("WORKER_REMOVE_BG", "1") != "1":
        return img
    if img.getextrema()[3][0] < 255:  # already has transparency
        return img
    from transformers import AutoModelForImageSegmentation
    import torch
    from torchvision import transforms
    model = _state.setdefault("birefnet", AutoModelForImageSegmentation.from_pretrained(
        "ZhengPeng7/BiRefNet", trust_remote_code=True).cuda().eval())
    tf = transforms.Compose([transforms.Resize((1024, 1024)), transforms.ToTensor(),
                             transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])])
    rgb = img.convert("RGB")
    with torch.no_grad():
        mask = model(tf(rgb).unsqueeze(0).cuda())[-1].sigmoid().cpu()[0].squeeze()
    out = rgb.copy()
    out.putalpha(transforms.ToPILImage()(mask).resize(rgb.size))
    return out


@app.get("/health")
def health():
    return {"ok": True, "backend": BACKEND, "loaded": list(_state["pipelines"]), "idle_unload_s": IDLE_UNLOAD_S}


@app.post("/unload")
def unload():
    with _lock:
        _unload_all()
    return {"ok": True}


@app.post("/generate")
async def generate(model: str = Form(...), workflow: str = Form(...), prompt: str = Form(""),
                   params: str = Form("{}"), views: str = Form("[]"), images: list[UploadFile] = File(default=[])):
    p = json.loads(params or "{}")
    view_names = json.loads(views or "[]")
    blobs = [await f.read() for f in images]
    seed = int(p.get("seed", 12345))
    t0 = time.time()
    with _lock:  # one generation at a time per container
        _state["last_used"] = time.time()
        glb, info = _run(model, workflow, prompt, p, view_names, blobs, seed)
        _state["last_used"] = time.time()
    info.update(seconds=round(time.time() - t0, 2), seed=seed, backend=BACKEND, params=p)
    return Response(glb, media_type="model/gltf-binary", headers={"x-worker-info": json.dumps(info)})


def _run(model, workflow, prompt, p, view_names, blobs, seed) -> tuple[bytes, dict]:
    import torch
    if BACKEND == "trellis2":
        if workflow != "image" or not blobs:
            raise HTTPException(400, "trellis2 takes one image")
        import o_voxel
        pipe = _load("trellis2")
        mesh = pipe.run(_remove_bg(_rgba(blobs[0])), seed=seed)[0]
        mesh.simplify(16777216)
        glb = o_voxel.postprocess.to_glb(
            vertices=mesh.vertices, faces=mesh.faces, attr_volume=mesh.attrs, coords=mesh.coords,
            attr_layout=mesh.layout, voxel_size=mesh.voxel_size, aabb=[[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]],
            decimation_target=int(p.get("decimation_target", 1_000_000)),
            texture_size=int(p.get("texture_size", 4096)), remesh=True, remesh_band=1, remesh_project=0)
        return _export(glb), {"model": "microsoft/TRELLIS.2-4B", "repo_revision": _state["revisions"].get("trellis2")}

    if BACKEND == "hunyuan":
        if workflow == "multiview":
            views = {v: _remove_bg(_rgba(b)) for v, b in zip(view_names, blobs) if v in ("front", "left", "back", "right")}
            if not views:
                raise HTTPException(400, "multiview needs front/left/back/right views")
            shape_pipe, key, repo = _load("hunyuan-mv"), "hunyuan-mv", "tencent/Hunyuan3D-2mv"
            image_arg = views
        else:
            shape_pipe, key, repo = _load("hunyuan-single"), "hunyuan-single", "tencent/Hunyuan3D-2.1"
            image_arg = _remove_bg(_rgba(blobs[0]))
        mesh = shape_pipe(image=image_arg, num_inference_steps=int(p.get("steps", 30)),
                          octree_resolution=int(p.get("octree_resolution", 380)), num_chunks=20000,
                          generator=torch.manual_seed(seed), output_type="trimesh")[0]
        info = {"model": repo, "repo_revision": _state["revisions"].get(key), "textured": False}
        if p.get("texture", True):
            paint = _load("hunyuan-paint")
            ref = image_arg["front"] if isinstance(image_arg, dict) and "front" in image_arg else (
                next(iter(image_arg.values())) if isinstance(image_arg, dict) else image_arg)
            mesh = paint(mesh, image=ref)
            info.update(textured=True, paint_model="tencent/Hunyuan3D-2.1",
                        paint_revision=_state["revisions"].get("hunyuan-paint"))
        return _export(mesh), info

    if BACKEND == "trellis-text":
        if not prompt.strip():
            raise HTTPException(400, "prompt required")
        from trellis.utils import postprocessing_utils
        pipe = _load("trellis-text")
        out = pipe.run(prompt, seed=seed)
        glb = postprocessing_utils.to_glb(out["gaussian"][0], out["mesh"][0], simplify=0.95, texture_size=1024)
        return _export(glb), {"model": "microsoft/TRELLIS-text-xlarge",
                              "repo_revision": _state["revisions"].get("trellis-text")}

    raise HTTPException(500, f"unknown backend {BACKEND}")


def _export(obj) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "model.glb"
        obj.export(str(path))
        return path.read_bytes()
