"""Mock GPU worker for development and demos without the DGX. Same protocol as workers/gpu.

Returns a procedural genset-like mesh (proportions vary by model id) with a PBR material, so the
gallery, compare and benchmark views have something real to inspect. Output is labelled mock.

    uvicorn workers.mock.server:app --port 9001
"""
import hashlib
import io
import json
import time

import numpy as np
import trimesh
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import Response
from PIL import Image

app = FastAPI(title="3d-worker-mock")

PROPORTIONS = {  # length, height, width (glTF Y-up), roughly the variation seen across tools
    "microsoft/TRELLIS.2-4B": (1.0, 0.56, 0.45),
    "tencent/Hunyuan3D-2mv": (1.0, 0.50, 0.44),
    "tencent/Hunyuan3D-2.1": (1.0, 0.58, 0.47),
    "microsoft/TRELLIS-text-xlarge": (0.6, 1.0, 0.6),
}


def _texture(seed: int, size=512) -> Image.Image:
    rng = np.random.default_rng(seed)
    base = np.array([60, 110, 80], dtype=np.float32)
    noise = rng.normal(0, 12, (size, size, 1))
    img = np.clip(base + noise, 0, 255).astype(np.uint8)
    return Image.fromarray(img)


def build(model: str, seed: int) -> bytes:
    L, H, W = PROPORTIONS.get(model, (1.0, 0.55, 0.45))
    parts = [trimesh.creation.box(extents=(L, H * 0.12, W)).apply_translation((0, H * 0.06, 0))]  # skid
    parts.append(trimesh.creation.box(extents=(L * 0.55, H * 0.6, W * 0.8)).apply_translation((-L * 0.15, H * 0.45, 0)))  # engine
    parts.append(trimesh.creation.cylinder(radius=W * 0.35, height=L * 0.28, sections=48)
                 .apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, (0, 0, 1)))
                 .apply_translation((L * 0.3, H * 0.45, 0)))  # alternator
    parts.append(trimesh.creation.box(extents=(L * 0.08, H * 0.85, W * 0.95)).apply_translation((-L * 0.46, H * 0.52, 0)))  # radiator
    mesh = trimesh.util.concatenate(parts)
    mesh = mesh.subdivide().subdivide()
    rng = np.random.default_rng(seed)
    mesh.vertices += rng.normal(0, 0.002, mesh.vertices.shape)
    uv = (mesh.vertices[:, [0, 2]] - mesh.vertices[:, [0, 2]].min(0)) / np.ptp(mesh.vertices[:, [0, 2]], axis=0)
    material = trimesh.visual.material.PBRMaterial(baseColorTexture=_texture(seed), metallicFactor=0.3, roughnessFactor=0.6)
    mesh.visual = trimesh.visual.TextureVisuals(uv=uv, material=material)
    return trimesh.Scene(mesh).export(file_type="glb")


@app.get("/health")
def health():
    return {"ok": True, "backend": "mock"}


@app.post("/generate")
async def generate(model: str = Form(...), workflow: str = Form(...), prompt: str = Form(""),
                   params: str = Form("{}"), views: str = Form("[]"), images: list[UploadFile] = File(default=[])):
    p = json.loads(params or "{}")
    seed = int(p.get("seed", 12345))
    digest = hashlib.sha256(b"".join([await f.read() for f in images]) + prompt.encode()).hexdigest()
    t0 = time.time()
    glb = build(model, seed + int(digest[:6], 16) % 1000)
    info = {"model": model, "repo_revision": "mock", "backend": "mock", "seed": seed,
            "seconds": round(time.time() - t0, 2), "inputs_sha256": digest, "mock": True}
    return Response(glb, media_type="model/gltf-binary", headers={"x-worker-info": json.dumps(info)})
