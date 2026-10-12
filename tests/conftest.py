import io

import pytest
import trimesh


def glb_bytes(extents=(0.98, 0.57, 0.46), shells=1) -> bytes:
    """A box mesh (x=length, y=height, z=width; glTF Y-up), optionally with extra loose shells."""
    meshes = [trimesh.creation.box(extents=extents)]
    for i in range(shells - 1):
        meshes.append(trimesh.creation.box(extents=(0.01, 0.01, 0.01)).apply_translation((0.6 + i * 0.02, 0, 0)))
    scene = trimesh.Scene(meshes)
    return scene.export(file_type="glb")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("HUB_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HUB_TRELLIS2_URL", "http://fake-dgx:9001")
    monkeypatch.setenv("HUB_HUNYUAN_URL", "http://fake-dgx:9002")
    monkeypatch.delenv("HUB_DATABASE_URL", raising=False)
    monkeypatch.delenv("HUB_API_TOKEN", raising=False)
    from hub import db
    db.init_engine(f"sqlite:///{tmp_path / 'hub.db'}")
    from fastapi.testclient import TestClient
    from hub.api import app
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def fake_gpu(monkeypatch):
    """Replace the HTTP call to the DGX worker with a local mesh."""
    from hub.adapters.base import ExecResult, OutputFile
    from hub.adapters.remote import RemoteGpuAdapter
    calls = []

    def execute(self, payload, settings, read_blob):
        calls.append((self.name, payload))
        for img in payload["images"]:
            read_blob(img["sha256"])
        return ExecResult(files=[OutputFile("model.glb", glb_bytes())],
                          worker_info={"model": self.model_id, "weights_sha256": "x" * 64, "seconds": 1.5})

    monkeypatch.setattr(RemoteGpuAdapter, "execute", execute)
    return calls


def png(color=(200, 30, 30)) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buf, format="PNG")
    return buf.getvalue()
