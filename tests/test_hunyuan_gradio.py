import pytest

from hub.adapters import ADAPTERS
from hub.adapters.base import InputError
from hub.adapters.hunyuan_gradio import find_endpoint
from hub.config import Settings
from tests.conftest import glb_bytes

PARAMS = ["caption", "image", "mv_image_front", "mv_image_back", "mv_image_left", "mv_image_right", "steps",
          "guidance_scale", "seed", "octree_resolution", "check_box_rembg", "num_chunks", "randomize_seed"]
API = {"named_endpoints": {"/lambda": {"parameters": []},
                           "/shape_generation": {"parameters": [{"parameter_name": p} for p in PARAMS]}}}


class FakeClient:
    def __init__(self, out_path):
        self.out_path, self.calls = out_path, []

    def view_api(self, **kw):
        return API

    def predict(self, api_name=None, **kwargs):
        self.calls.append((api_name, kwargs))
        return (str(self.out_path), "<html>", {"faces": 12}, 1234)


@pytest.fixture()
def settings(monkeypatch):
    monkeypatch.setenv("HUB_HUNYUAN_GRADIO_URL", "http://10.0.1.106:8080")
    return Settings()


def test_find_endpoint_prefers_shape_generation():
    name, params = find_endpoint(API, "/missing")
    assert name == "/shape_generation" and "mv_image_front" in params


def test_multiview_maps_views_and_params(settings, monkeypatch, tmp_path):
    out = tmp_path / "white_mesh.glb"
    out.write_bytes(glb_bytes())
    fake = FakeClient(out)
    monkeypatch.setattr("hub.adapters.hunyuan_gradio._client", lambda url: fake)
    adapter = ADAPTERS["hunyuan3d-lab"]
    images = [{"sha256": v, "view": v} for v in ("front", "left", "back", "right")]
    payload = adapter.prepare(workflow="multiview", prompt="", images=images, params={"steps": 30, "seed": 7})
    result = adapter.execute(payload, settings, read_blob=lambda sha: sha.encode())

    api_name, kwargs = fake.calls[0]
    assert api_name == "/shape_generation"
    assert {"mv_image_front", "mv_image_left", "mv_image_back", "mv_image_right"} <= set(kwargs)
    assert "image" not in kwargs
    assert kwargs["steps"] == 30 and kwargs["seed"] == 7 and kwargs["randomize_seed"] is False
    assert kwargs["check_box_rembg"] is True
    assert result.files[0].data.startswith(b"glTF")
    assert result.worker_info["model"] == "tencent/Hunyuan3D-2mv" and result.worker_info["texture"] is False


def test_obj_output_is_converted_to_glb(settings, monkeypatch, tmp_path):
    import trimesh
    out = tmp_path / "mesh.obj"
    out.write_text(trimesh.creation.box().export(file_type="obj"))
    monkeypatch.setattr("hub.adapters.hunyuan_gradio._client", lambda url: FakeClient(out))
    adapter = ADAPTERS["hunyuan3d-lab"]
    images = [{"sha256": "f", "view": "front"}, {"sha256": "b", "view": "back"}]
    payload = adapter.prepare(workflow="multiview", prompt="", images=images, params={})
    assert adapter.execute(payload, settings, read_blob=lambda s: b"x").files[0].data.startswith(b"glTF")


def test_requires_front_view():
    images = [{"sha256": "l", "view": "left"}, {"sha256": "b", "view": "back"}]
    with pytest.raises(InputError, match="front"):
        ADAPTERS["hunyuan3d-lab"].prepare(workflow="multiview", prompt="", images=images, params={})
