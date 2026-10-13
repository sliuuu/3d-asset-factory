"""Mesh inspection and the donor intake gate.

Metrics mirror the measurement table in the Hunyuan3D 3.1 C3250 write-up so hub output can be
compared with that record directly. Everything here is geometry maths; no renderer is needed.
"""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import trimesh
from trimesh.graph import connected_component_labels

# Hero budgets from the digital-twin-asset-pipeline skill (LOD0 hero <= 20k tris, 2048² maps).
HERO_TRI_BUDGET = 20_000
HERO_MAP_MAX = 2048


def load_scene(path: Path | str, file_type: str | None = None) -> trimesh.Scene:
    return trimesh.load(str(path), file_type=file_type, force="scene", process=False)


def _concat(scene: trimesh.Scene) -> trimesh.Trimesh:
    meshes = scene.dump(concatenate=False) if hasattr(scene, "dump") else [scene]
    meshes = [m for m in meshes if isinstance(m, trimesh.Trimesh) and len(m.faces)]
    if not meshes:
        raise ValueError("no triangle geometry found")
    return trimesh.util.concatenate(meshes) if len(meshes) > 1 else meshes[0]


def _materials(scene: trimesh.Scene) -> list:
    mats = []
    for geom in scene.geometry.values():
        mat = getattr(getattr(geom, "visual", None), "material", None)
        if mat is not None and mat not in mats:
            mats.append(mat)
    return mats


def _image_size(img) -> list[int] | None:
    return list(img.size) if img is not None else None


def _normal_strength(img) -> float | None:
    """Mean |xy| of a tangent-space normal map. ~0 means the map is effectively flat."""
    if img is None:
        return None
    arr = np.asarray(img.convert("RGB").resize((min(512, img.size[0]), min(512, img.size[1]))), dtype=np.float32)
    xy = arr[..., :2] / 255.0 * 2.0 - 1.0
    return float(np.mean(np.linalg.norm(xy, axis=-1)))


def _uv_area(mesh: trimesh.Trimesh) -> float | None:
    uv = getattr(mesh.visual, "uv", None)
    if uv is None or len(uv) != len(mesh.vertices):
        return None
    tri = uv[mesh.faces]
    a = tri[:, 1] - tri[:, 0]
    b = tri[:, 2] - tri[:, 0]
    return float(np.sum(np.abs(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0])) / 2.0)


def inspect(path: Path | str, file_type: str | None = None) -> dict:
    scene = load_scene(path, file_type)
    mesh = _concat(scene)
    faces = len(mesh.faces)
    labels = connected_component_labels(mesh.face_adjacency, node_count=faces)
    counts = np.bincount(labels) if faces else np.array([0])
    largest_share = float(counts.max() / faces) if faces else 0.0

    ext = mesh.bounds[1] - mesh.bounds[0]  # x, y, z ; glTF is Y-up
    horiz = sorted([float(ext[0]), float(ext[2])], reverse=True)
    lwh = (horiz[0], horiz[1], float(ext[1]))

    areas = mesh.area_faces
    degenerate = int(np.sum(areas <= 1e-12))
    edges = mesh.edges_unique_length
    # Triangle aspect ratio ~ longest edge / shortest altitude, p99.
    tri_edges = np.linalg.norm(mesh.triangles[:, [1, 2, 0]] - mesh.triangles, axis=2)
    longest = tri_edges.max(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        aspect = np.where(areas > 0, longest**2 / (2 * areas), np.inf)
    aspect_p99 = float(np.percentile(aspect[np.isfinite(aspect)], 99)) if np.isfinite(aspect).any() else None

    textures = []
    normal_strength = None
    max_map = 0
    for mat in _materials(scene):
        entry = {"name": getattr(mat, "name", "") or ""}
        for key in ("baseColorTexture", "normalTexture", "metallicRoughnessTexture", "occlusionTexture", "emissiveTexture"):
            img = getattr(mat, key, None)
            size = _image_size(img)
            if size:
                entry[key] = size
                max_map = max(max_map, *size)
        if getattr(mat, "normalTexture", None) is not None and normal_strength is None:
            normal_strength = _normal_strength(mat.normalTexture)
        textures.append(entry)

    uv_area = _uv_area(mesh)
    base = next((t.get("baseColorTexture") for t in textures if t.get("baseColorTexture")), None)
    texel_density = None
    if base and uv_area and mesh.area > 0:
        texel_density = float(np.sqrt(base[0] * base[1] * uv_area / mesh.area))  # px per model unit

    return {
        "triangles": faces,
        "vertices": int(len(mesh.vertices)),
        "watertight": bool(mesh.is_watertight),
        "winding_consistent": bool(mesh.is_winding_consistent),
        "degenerate_faces": degenerate,
        "shells": int(len(counts)),
        "largest_shell_share": largest_share,
        "extents_xyz": [float(x) for x in ext],
        "extents_lwh": list(lwh),
        "proportions_lwh": [x / lwh[2] if lwh[2] else None for x in lwh],
        "surface_area": float(mesh.area),
        "edge_length_median": float(np.median(edges)) if len(edges) else None,
        "triangle_aspect_p99": aspect_p99,
        "materials": textures,
        "max_map_px": max_map,
        "uv_area_ratio": uv_area,
        "normal_map_strength": normal_strength,
        "texel_density_px_per_unit": texel_density,
    }


def intake_gate(metrics: dict, *, fit: dict | None, provenance: str, licence: str, logo_flag: bool | None) -> dict:
    """Donor intake gate (HO-029 proposal). Reports; never mutates the asset."""
    checks = {
        "parses": {"pass": True},
        "watertight": {"pass": metrics["watertight"], "value": metrics["watertight"]},
        "no_degenerate_faces": {"pass": metrics["degenerate_faces"] == 0, "value": metrics["degenerate_faces"]},
        "shells": {
            "pass": None,  # informational: a fused single shell needs a part-split before twin use
            "value": metrics["shells"],
            "largest_share": metrics["largest_shell_share"],
            "note": "fused shell, split parts before twin use" if metrics["largest_shell_share"] > 0.9 else "",
        },
        "envelope_fit": {"pass": None if not fit or fit.get("status") == "skipped" else fit["status"] == "pass", "value": fit},
        "provenance_recorded": {"pass": bool(provenance.strip())},
        "licence_recorded": {"pass": bool(licence.strip())},
        "logo_or_text_free": {"pass": None if logo_flag is None else not logo_flag, "note": "set by reviewer"},
        "hero_budget": {
            "pass": None,  # donors are expected to exceed it; reported for bake planning
            "value": {"triangles": metrics["triangles"], "max_map_px": metrics["max_map_px"]},
            "budget": {"triangles": HERO_TRI_BUDGET, "max_map_px": HERO_MAP_MAX},
        },
    }
    hard = [v["pass"] for v in checks.values() if v["pass"] is not None]
    pending = [k for k, v in checks.items() if v["pass"] is None and k not in ("shells", "hero_budget")]
    status = "fail" if not all(hard) else ("pending" if pending else "pass")
    return {"status": status, "pending": pending, "checks": checks}


def deviation(a: trimesh.Trimesh, b: trimesh.Trimesh, samples: int = 50_000, align: bool = True) -> dict:
    """Approximate point-to-surface deviation of `a` against `b`, as % of b's length.

    `b` is densely sampled and queried with a KD-tree, so this slightly overestimates true
    point-to-surface distance. Good enough as a Gate B check; labelled approximate.
    """
    from scipy.spatial import cKDTree

    pa, _ = trimesh.sample.sample_surface(a, samples, seed=1)
    pb, _ = trimesh.sample.sample_surface(b, samples * 4, seed=2)
    if align:
        pa = pa - a.bounds.mean(axis=0) + b.bounds.mean(axis=0)
    d, _ = cKDTree(pb).query(pa)
    length = float(np.max(b.bounds[1] - b.bounds[0]))
    return {
        "median_pct": float(np.median(d) / length * 100),
        "p99_pct": float(np.percentile(d, 99) / length * 100),
        "length": length,
        "approximate": True,
    }


def mesh_from_blob(data: bytes, file_type: str) -> trimesh.Trimesh:
    return _concat(trimesh.load(io.BytesIO(data), file_type=file_type, force="scene", process=False))
