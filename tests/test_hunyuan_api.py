import base64
import json

import httpx
import pytest

from hub.adapters import ADAPTERS
from hub.config import Settings
from tests.conftest import glb_bytes


def _settings(monkeypatch, url="http://hunyuan.lab:8081", model=""):
    monkeypatch.setenv("HUB_HUNYUAN_API_URL", url)
    monkeypatch.setenv("HUB_HUNYUAN_API_MODEL", model)
    return Settings()


def test_unavailable_without_url(monkeypatch):
    ok, why = ADAPTERS["hunyuan3d-api"].available(_settings(monkeypatch, url=""))
    assert not ok and "HUNYUAN_API" in why


def test_posts_upstream_json_and_returns_glb(monkeypatch):
    settings = _settings(monkeypatch, model="tencent/Hunyuan3D-2")
    sent = {}

    def fake_post(url, json=None, timeout=None, **kw):
        sent["url"], sent["body"] = url, json
        return httpx.Response(200, content=glb_bytes(), headers={"content-type": "model/gltf-binary"})

    monkeypatch.setattr("hub.adapters.hunyuan_api.httpx.post", fake_post)
    adapter = ADAPTERS["hunyuan3d-api"]
    images = [{"sha256": "b", "view": "back"}, {"sha256": "f", "view": "front"}]
    payload = adapter.prepare(workflow="image", prompt="", images=images, params={"seed": 7, "foo": 1})
    result = adapter.execute(payload, settings, read_blob=lambda sha: sha.encode())

    assert sent["url"] == "http://hunyuan.lab:8081/generate"
    assert base64.b64decode(sent["body"]["image"]) == b"f"  # front view chosen
    assert sent["body"]["seed"] == 7 and sent["body"]["texture"] is True
    assert "foo" not in sent["body"]
    assert result.files[0].data.startswith(b"glTF")
    assert result.worker_info["model"] == "tencent/Hunyuan3D-2"
    json.dumps(result.worker_info)


def test_rejects_non_glb_response(monkeypatch):
    settings = _settings(monkeypatch)
    monkeypatch.setattr("hub.adapters.hunyuan_api.httpx.post",
                        lambda *a, **k: httpx.Response(200, json={"error": "busy"}))
    adapter = ADAPTERS["hunyuan3d-api"]
    payload = adapter.prepare(workflow="image", prompt="", images=[{"sha256": "f", "view": "front"}], params={})
    with pytest.raises(RuntimeError, match="did not return a GLB"):
        adapter.execute(payload, settings, read_blob=lambda sha: b"x")
