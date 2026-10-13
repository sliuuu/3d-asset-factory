"""Format export. GLB is the master format; others are derived on request.

OBJ / STL / PLY go through trimesh. FBX and USDZ need Blender (set BLENDER_BIN); without it the
export reports itself unavailable rather than producing a lossy substitute.
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

import trimesh

TRIMESH_FORMATS = {"glb", "obj", "stl", "ply"}
BLENDER_FORMATS = {"fbx", "usdz", "blend"}
ALL_FORMATS = TRIMESH_FORMATS | BLENDER_FORMATS

_BLENDER_SCRIPT = r"""
import bpy, sys
src, dst, fmt = sys.argv[sys.argv.index('--') + 1:]
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=src)
if fmt == 'fbx':
    bpy.ops.export_scene.fbx(filepath=dst, path_mode='COPY', embed_textures=True)
elif fmt == 'usdz':
    bpy.ops.wm.usd_export(filepath=dst)
elif fmt == 'blend':
    bpy.ops.wm.save_as_mainfile(filepath=dst)
"""


def blender_bin() -> str | None:
    return os.environ.get("BLENDER_BIN") or shutil.which("blender")


def available_formats() -> dict[str, bool]:
    has_blender = blender_bin() is not None
    return {f: (f in TRIMESH_FORMATS or has_blender) for f in sorted(ALL_FORMATS)}


def export(glb_path: Path, fmt: str) -> tuple[bytes, str]:
    """Return (bytes, filename_suffix). OBJ is zipped with its MTL and textures."""
    fmt = fmt.lower()
    if fmt not in ALL_FORMATS:
        raise ValueError(f"unsupported format {fmt}")
    if fmt == "glb":
        return glb_path.read_bytes(), ".glb"
    if fmt in TRIMESH_FORMATS:
        scene = trimesh.load(str(glb_path), file_type="glb", force="scene")
        if fmt == "obj":
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / "model.obj"
                scene.export(str(out), file_type="obj")
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                    for p in Path(tmp).iterdir():
                        zf.write(p, p.name)
                return buf.getvalue(), ".obj.zip"
        data = scene.export(file_type=fmt)
        return (data if isinstance(data, bytes) else data.encode()), f".{fmt}"
    exe = blender_bin()
    if not exe:
        raise RuntimeError(f"{fmt} export needs Blender; set BLENDER_BIN on the forge worker")
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "export.py"
        script.write_text(_BLENDER_SCRIPT)
        src = Path(tmp) / "source.glb"  # blobs have no extension; the glTF importer wants one
        shutil.copyfile(glb_path, src)
        dst = Path(tmp) / f"model.{fmt}"
        subprocess.run([exe, "--background", "--factory-startup", "--python", str(script), "--",
                        str(src), str(dst), fmt], check=True, capture_output=True, timeout=600)
        return dst.read_bytes(), f".{fmt}"
