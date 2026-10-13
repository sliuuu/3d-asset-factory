"""Adapter registry. To add a model: add one entry here and deploy its worker."""
from .base import Adapter, InputError, PolicyError, check_egress
from .hunyuan_api import HunyuanApiAdapter
from .hunyuan_gradio import HunyuanGradioAdapter
from .img2threejs import Img2ThreejsAdapter
from .remote import RemoteGpuAdapter

HUNYUAN_LICENCE = "Tencent Hunyuan Community Licence (excludes EU, UK, South Korea)"

ADAPTERS: dict[str, Adapter] = {a.name: a for a in [
    RemoteGpuAdapter(
        name="trellis2", label="TRELLIS.2-4B", workflows=("image",), licence="MIT",
        host="DGX Spark", url_setting="trellis2_url", model_id="microsoft/TRELLIS.2-4B",
        notes="Single image in (uses the front view when given a set). O-Voxel 512³–1536³, PBR. "
              "Tested upstream on x86 A100/H100 only; the DGX ARM64 build is unverified.",
    ),
    RemoteGpuAdapter(
        name="trellis-text", label="TRELLIS text-xlarge", workflows=("text",), licence="MIT",
        host="DGX Spark", url_setting="trellis_text_url", model_id="microsoft/TRELLIS-text-xlarge",
        notes="Props, robots and people only (Tech Spec §5.1); not equipment truth.",
    ),
    RemoteGpuAdapter(
        name="hunyuan3d-2.1", label="Hunyuan3D-2.1", workflows=("image",), licence=HUNYUAN_LICENCE,
        host="DGX Spark", url_setting="hunyuan_url", model_id="tencent/Hunyuan3D-2.1",
        notes="Shape + PBR paint.",
    ),
    HunyuanGradioAdapter(
        name="hunyuan3d-lab", label="Hunyuan3D-2mv (DGX, live)", workflows=("multiview",),
        licence=HUNYUAN_LICENCE, host="DGX Spark :8080 (gradio_app.py)", url_setting="hunyuan_gradio_url",
        model_id="tencent/Hunyuan3D-2mv", allowed_views=("front", "left", "back", "right"),
        notes="The lab's running Hunyuan3D (since 2026-10-12): multi-view front/left/back/right, shape only "
              "(started with --disable_tex). Runs in tmux, so it is down after a DGX reboot.",
    ),
    HunyuanApiAdapter(
        name="hunyuan3d-api", label="Hunyuan3D (existing lab server)", workflows=("image",),
        licence=HUNYUAN_LICENCE, host="lab (upstream api_server.py)", url_setting="hunyuan_api_url",
        model_id="tencent/Hunyuan3D-2.1",
        notes="Calls an already-running Hunyuan3D api_server.py. One image in (front view of a set); "
              "set HUB_HUNYUAN_API_MODEL to the checkpoint it serves so provenance is right.",
    ),
    RemoteGpuAdapter(
        name="hunyuan3d-2mv", label="Hunyuan3D-2mv", workflows=("multiview",), licence=HUNYUAN_LICENCE,
        host="DGX Spark", url_setting="hunyuan_url", model_id="tencent/Hunyuan3D-2mv",
        allowed_views=("front", "left", "back", "right"),
        notes="Shape only from up to four orthogonal views; texture via 2.1 paint.",
    ),
    Img2ThreejsAdapter(
        name="img2threejs", label="img2threejs-service (parametric)", workflows=("parametric",),
        licence="Apache-2.0 upstream", host="ai-server :8099", evidence_class="parametric", needs_gpu=False,
        notes="Parametric racks (rack, orv3, ai-rack). Diagram tier; not exec-facing.",
    ),
    Adapter(
        name="external", label="External import (Hunyuan3D 3.x, Hi3D, vendor)", workflows=("import",),
        licence="per source", host="manual upload", needs_gpu=False, data_egress=True,
        notes="Results from hosted tools. Inputs must have been public brand-library photos.",
    ),
    Adapter(
        name="upload", label="Upload (Blender loop, FreeCAD, library LOD, reconstruction)", workflows=("import",),
        licence="per source", host="manual upload", evidence_class="authored", needs_gpu=False,
        notes="Lab-produced files. Set evidence class on import.",
    ),
]}

__all__ = ["ADAPTERS", "Adapter", "InputError", "PolicyError", "check_egress"]
