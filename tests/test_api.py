import json

from hub.worker import loop

from .conftest import glb_bytes, png


def make_asset(client, **kw):
    body = {"name": "C3250", "family": "genset", "site": "HK6",
            "envelope": {"L": 8.9, "W": 3.9, "H": 3.6, "unconfirmed": ["H"]}, **kw}
    r = client.post("/api/assets", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def make_inputs(client, classification="internal", views=("front", "left", "back", "right")):
    files = [("images", (f"{v}.png", png(), "image/png")) for v in views]
    r = client.post("/api/inputs", files=files, data={"views": json.dumps(list(views)),
                                                      "classification": classification,
                                                      "provenance": "brand library TR6", "licence": "internal use"})
    assert r.status_code == 200, r.text
    return r.json()


def test_adapters_listed_with_licences(client):
    names = {a["name"]: a for a in client.get("/api/adapters").json()}
    assert names["trellis2"]["licence"] == "MIT"
    assert "Hunyuan" in names["hunyuan3d-2mv"]["licence"]
    assert names["external"]["data_egress"] is True
    assert names["trellis2"]["available"] is True
    assert names["trellis-text"]["available"] is False  # no worker URL configured


def test_multiview_compare_batch_runs_and_records_provenance(client, fake_gpu):
    asset = make_asset(client)
    inp = make_inputs(client)
    r = client.post("/api/runs", json={"asset_id": asset["id"], "input_set_id": inp["id"], "workflow": "multiview",
                                       "adapters": ["trellis2", "hunyuan3d-2mv"]})
    assert r.status_code == 200, r.text
    batch = r.json()
    assert batch["batch_id"] and len(batch["runs"]) == 2
    assert all(x["status"] == "queued" for x in batch["runs"])
    assert batch["runs"][0]["params"]["workflow"] == "image"  # TRELLIS.2 takes the front view
    assert loop(gpu=True, once=True) == 2
    trellis_payload = next(p for name, p in fake_gpu if name == "trellis2")
    assert [i["view"] for i in trellis_payload["images"]] == ["front"]
    runs = client.get(f"/api/runs?batch_id={batch['batch_id']}").json()
    assert {x["status"] for x in runs} == {"succeeded"}
    assert runs[0]["worker_info"]["weights_sha256"]
    detail = client.get(f"/api/assets/{asset['id']}").json()
    assert len(detail["versions"]) == 2
    v = detail["versions"][0]
    assert v["state"] == "provisional" and v["evidence_class"] == "generated-visual"
    assert v["metrics"]["watertight"] is True
    assert v["intake"]["checks"]["envelope_fit"]["value"]["status"] == "fail"  # same proportions as the C3250 donor
    cmp = client.post("/api/compare", json={"version_ids": [x["id"] for x in detail["versions"]],
                                            "reference_version_id": detail["versions"][0]["id"]}).json()
    assert cmp["rows"][1]["deviation_vs_reference"]["median_pct"] < 1.0


def test_internal_photos_never_go_to_hosted_tools(client):
    asset = make_asset(client)
    r = client.post(f"/api/assets/{asset['id']}/import",
                    files={"file": ("hy31.glb", glb_bytes(), "model/gltf-binary")},
                    data={"source": "external", "source_tool": "Hunyuan3D 3.1", "input_classification": "internal"})
    assert r.status_code == 403
    r = client.post(f"/api/assets/{asset['id']}/import",
                    files={"file": ("hy31.glb", glb_bytes(shells=12), "model/gltf-binary")},
                    data={"source": "external", "source_tool": "Hunyuan3D 3.1", "input_classification": "public",
                          "provenance": "public brand photo TR6", "licence": "Tencent hosted terms"})
    assert r.status_code == 200, r.text
    v = r.json()
    assert v["evidence_class"] == "generated-visual"
    assert v["metrics"]["shells"] == 12


def test_multiview_rejects_views_the_model_cannot_take(client, fake_gpu):
    asset = make_asset(client)
    inp = make_inputs(client, views=("front", "top"))
    run = client.post("/api/runs", json={"asset_id": asset["id"], "input_set_id": inp["id"], "workflow": "multiview",
                                         "adapters": ["hunyuan3d-2mv"]}).json()["runs"][0]
    assert run["status"] == "rejected" and "views" in run["error"]
    assert fake_gpu == []


def test_state_rules(client):
    asset = make_asset(client, envelope={"L": 0.98, "W": 0.46, "H": 0.57})
    v = client.post(f"/api/assets/{asset['id']}/import",
                    files={"file": ("lod0.glb", glb_bytes(), "model/gltf-binary")},
                    data={"source": "upload", "evidence_class": "authored", "provenance": "Blender loop pilot",
                          "licence": "internal"}).json()
    assert v["intake"]["status"] == "pending"  # logo check needs a reviewer
    assert client.post(f"/api/versions/{v['id']}/state", json={"state": "approved"}).status_code == 422
    assert client.post(f"/api/versions/{v['id']}/state", json={"state": "technical_validated"}).status_code == 409
    client.post(f"/api/versions/{v['id']}/reviews", json={"reviewer": "Samuel", "verdict": "pass",
                                                          "logo_or_text_present": False})
    r = client.post(f"/api/versions/{v['id']}/state", json={"state": "technical_validated"})
    assert r.status_code == 200, r.text
    assert client.post(f"/api/versions/{v['id']}/state", json={"state": "visual_validated"}).status_code == 422
    assert client.post(f"/api/versions/{v['id']}/state",
                       json={"state": "visual_validated", "reviewer": "Samuel"}).status_code == 200


def test_exports(client):
    asset = make_asset(client)
    v = client.post(f"/api/assets/{asset['id']}/import", files={"file": ("m.glb", glb_bytes(), "model/gltf-binary")},
                    data={"source": "upload"}).json()
    for fmt in ("glb", "obj", "stl", "ply"):
        r = client.get(f"/api/versions/{v['id']}/download?format={fmt}")
        assert r.status_code == 200, (fmt, r.text)
        assert len(r.content) > 100
    fbx = client.get(f"/api/versions/{v['id']}/download?format=fbx")
    assert fbx.status_code in (200, 422)  # 422 without Blender on the host


def test_benchmark_suite(client, fake_gpu):
    asset = make_asset(client)
    inp = make_inputs(client)
    ref = client.post(f"/api/assets/{asset['id']}/import", files={"file": ("ref.glb", glb_bytes(), "model/gltf-binary")},
                      data={"source": "upload", "evidence_class": "library"}).json()
    suite = client.post("/api/suites", json={"name": "FB-1 Arm B", "pipelines": ["trellis2", "hunyuan3d-2mv"]}).json()
    client.post(f"/api/suites/{suite['id']}/cases", json={"asset_id": asset["id"], "input_set_id": inp["id"],
                                                          "reference_version_id": ref["id"]})
    assert len(client.post(f"/api/suites/{suite['id']}/run").json()["runs"]) == 2
    loop(gpu=True, once=True)
    report = client.get(f"/api/suites/{suite['id']}/report").json()
    assert [r["pipeline"] for r in report["rows"]] == ["trellis2", "hunyuan3d-2mv"]
    assert all("deviation_vs_reference" in r for r in report["rows"])


def test_auth_token(client, monkeypatch):
    monkeypatch.setenv("HUB_API_TOKEN", "s3cret")
    assert client.get("/api/assets").status_code == 401
    assert client.get("/api/assets", headers={"authorization": "Bearer s3cret"}).status_code == 200
    assert client.get("/api/health").status_code == 200


def test_search(client):
    make_asset(client)
    make_asset(client, name="CRAH", family="crah")
    assert [a["name"] for a in client.get("/api/assets?q=crah").json()] == ["CRAH"]
    assert len(client.get("/api/assets?site=HK6").json()) == 2
