import json

from .conftest import ADMIN, png


def _user(admin, email, role, **kw):
    r = admin.post("/api/admin/users", json={"email": email, "name": email.split("@")[0], "role": role,
                                             "password": "a-long-enough-password", **kw})
    assert r.status_code == 200, r.text
    return r.json()


def _asset(c, **kw):
    r = c.post("/api/assets", json={"name": "C3250", "family": "genset", **kw})
    assert r.status_code == 200, r.text
    return r.json()


def _inputs(c):
    views = ["front", "left", "back", "right"]
    r = c.post("/api/inputs", files=[("images", (f"{v}.png", png(), "image/png")) for v in views],
               data={"views": json.dumps(views), "classification": "internal"})
    assert r.status_code == 200, r.text
    return r.json()


def test_signed_out_requests_are_refused_and_health_stays_open(hub):
    c = hub()
    assert c.get("/api/assets").status_code == 401
    assert c.get("/api/me").status_code == 401
    assert c.get("/api/admin/users").status_code == 401
    assert c.get("/api/health").status_code == 200
    assert c.get("/api/auth/status").json() == {"auth_enabled": True, "has_users": True}


def test_login_logout_and_bad_password(hub):
    c = hub()
    assert c.post("/api/auth/login", json={"email": ADMIN[0], "password": "wrong"}).status_code == 401
    r = c.post("/api/auth/login", json={"email": ADMIN[0].upper(), "password": ADMIN[1]})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    me = c.get("/api/me").json()
    assert me["role"] == "admin" and me["user"]["email"] == ADMIN[0] and me["can"]["admin"]
    assert [w["slug"] for w in me["workspaces"]] == ["default"]
    c.post("/api/auth/logout")
    assert c.get("/api/me").status_code == 401
    actions = [e["action"] for e in hub(*ADMIN).get("/api/admin/audit").json()]
    assert {"auth.login_failed", "auth.login", "auth.logout"} <= set(actions)


def test_login_throttle(hub):
    c = hub()
    for _ in range(5):
        assert c.post("/api/auth/login", json={"email": ADMIN[0], "password": "nope"}).status_code == 401
    assert c.post("/api/auth/login", json={"email": ADMIN[0], "password": ADMIN[1]}).status_code == 429


def test_roles(hub):
    admin = hub(*ADMIN)
    _user(admin, "viewer@lab.test", "viewer")
    _user(admin, "maker@lab.test", "creator")
    viewer = hub("viewer@lab.test", "a-long-enough-password")
    maker = hub("maker@lab.test", "a-long-enough-password")
    assert viewer.get("/api/assets").status_code == 200
    assert viewer.post("/api/assets", json={"name": "x"}).status_code == 403
    a = _asset(maker)
    assert a["created_by"] == maker.get("/api/me").json()["user"]["id"]
    v = maker.post(f"/api/assets/{a['id']}/import", files={"file": ("m.glb", _glb(), "model/gltf-binary")},
                   data={"source": "upload", "evidence_class": "authored"}).json()
    # reviewing needs the reviewer role; the reviewer name is the signed-in user, not a free-text field
    assert maker.post(f"/api/versions/{v['id']}/reviews", json={"verdict": "pass"}).status_code == 403
    r = admin.post(f"/api/versions/{v['id']}/reviews", json={"reviewer": "someone else", "verdict": "pass"})
    assert r.json()["reviews"][0]["reviewer"] == ADMIN[0]
    assert maker.get("/api/admin/users").status_code == 403


def test_cross_site_post_with_cookie_refused(hub):
    maker = hub(*ADMIN)
    r = maker.post("/api/assets", json={"name": "x"}, headers={"origin": "http://evil.example"})
    assert r.status_code == 403


def test_workspaces_isolate_assets_and_runs(hub, fake_gpu):
    admin = hub(*ADMIN)
    ws = admin.post("/api/admin/workspaces", json={"slug": "hk6", "name": "HK6"}).json()
    _user(admin, "a@lab.test", "creator", workspace_ids=[ws["id"]])
    _user(admin, "b@lab.test", "creator")  # default workspace only
    a, b = hub("a@lab.test", "a-long-enough-password"), hub("b@lab.test", "a-long-enough-password")
    asset = _asset(a)
    assert asset["workspace_id"] == ws["id"]
    assert a.get(f"/api/assets/{asset['id']}").status_code == 200
    assert b.get(f"/api/assets/{asset['id']}").status_code == 404
    assert b.get("/api/assets").json() == []
    inp = _inputs(a)
    run = a.post("/api/runs", json={"asset_id": asset["id"], "input_set_id": inp["id"], "workflow": "multiview",
                                    "adapters": ["hunyuan3d-2mv"]}).json()["runs"][0]
    assert b.get(f"/api/runs/{run['id']}").status_code == 404
    assert [r["id"] for r in a.get("/api/runs?mine=1").json()] == [run["id"]]
    assert b.post("/api/assets", json={"name": "x", "workspace_id": ws["id"]}).status_code == 422
    assert len(admin.get("/api/assets").json()) == 1  # admins see every workspace


def test_quotas(hub, fake_gpu):
    admin = hub(*ADMIN)
    ws = admin.post("/api/admin/workspaces", json={"slug": "bench", "name": "Bench", "daily_gpu_runs": 2}).json()
    _user(admin, "q@lab.test", "creator", workspace_ids=[ws["id"]], daily_gpu_runs=5)
    c = hub("q@lab.test", "a-long-enough-password")
    asset, inp = _asset(c), _inputs(c)
    body = {"asset_id": asset["id"], "input_set_id": inp["id"], "workflow": "multiview"}
    assert c.post("/api/runs", json={**body, "adapters": ["trellis2", "hunyuan3d-2mv"]}).status_code == 200
    r = c.post("/api/runs", json={**body, "adapters": ["hunyuan3d-2mv"]})
    assert r.status_code == 429 and "daily GPU run quota" in r.json()["detail"]
    me = c.get("/api/me").json()
    assert me["workspaces"][0]["gpu_runs_today"] == 2 and me["user"]["gpu_runs_today"] == 2


def test_api_keys(hub):
    admin = hub(*ADMIN)
    u = _user(admin, "ci@lab.test", "creator")
    assert admin.post("/api/admin/keys", json={"name": "too much", "user_id": u["id"], "role": "admin"}).status_code == 422
    k = admin.post("/api/admin/keys", json={"name": "bench runner", "user_id": u["id"], "role": "viewer"}).json()
    assert k["secret"].startswith("haf_") and k["state"] == "active"
    assert "secret" not in admin.get("/api/admin/keys").json()[0]
    c = hub()
    h = {"authorization": f"Bearer {k['secret']}"}
    assert c.get("/api/assets", headers=h).status_code == 200
    assert c.post("/api/assets", json={"name": "x"}, headers=h).status_code == 403  # viewer key
    assert c.get(f"/api/assets?token={k['secret']}").status_code == 401  # no tokens in URLs
    assert admin.get("/api/admin/keys").json()[0]["last_used_at"]
    # disabling the owner disables the key
    admin.patch(f"/api/admin/users/{u['id']}", json={"active": False})
    assert c.get("/api/assets", headers=h).status_code == 401
    admin.patch(f"/api/admin/users/{u['id']}", json={"active": True})
    assert c.get("/api/assets", headers=h).status_code == 200
    admin.delete(f"/api/admin/keys/{k['id']}")
    assert c.get("/api/assets", headers=h).status_code == 401
    actions = [e["action"] for e in admin.get("/api/admin/audit?action=key").json()]
    assert actions == ["key.revoke", "key.create"]


def test_last_admin_cannot_be_removed(hub):
    admin = hub(*ADMIN)
    me = admin.get("/api/me").json()["user"]
    assert admin.patch(f"/api/admin/users/{me['id']}", json={"role": "creator"}).status_code == 409
    assert admin.patch(f"/api/admin/users/{me['id']}", json={"active": False}).status_code == 409


def test_password_reset_and_change(hub):
    admin = hub(*ADMIN)
    u = _user(admin, "p@lab.test", "viewer")
    c = hub("p@lab.test", "a-long-enough-password")
    temp = admin.post(f"/api/admin/users/{u['id']}/reset-password").json()["temporary_password"]
    assert c.get("/api/me").status_code == 401  # reset signs the user out
    c = hub("p@lab.test", temp)
    assert c.post("/api/me/password", json={"current": temp, "new": "short"}).status_code == 422
    assert c.post("/api/me/password", json={"current": temp, "new": "another-long-password"}).status_code == 200
    assert c.get("/api/me").status_code == 200  # the changing session gets a fresh cookie
    hub("p@lab.test", "another-long-password")


def test_legacy_token_still_works_and_is_flagged(hub, monkeypatch):
    monkeypatch.setenv("HUB_API_TOKEN", "s3cret")
    c = hub()
    assert c.get("/api/assets").status_code == 401
    assert c.get("/api/assets", headers={"authorization": "Bearer s3cret"}).status_code == 200
    assert c.get("/api/assets", headers={"authorization": "Bearer wrong"}).status_code == 401
    warnings = hub(*ADMIN).get("/api/admin/health").json()["warnings"]
    assert any("HUB_API_TOKEN" in w for w in warnings)


def test_models_and_services_settings(hub, fake_gpu):
    admin = hub(*ADMIN)
    st = admin.get("/api/admin/settings").json()
    keys = {f["key"]: f for f in st["fields"]}
    assert keys["hunyuan_url"]["env_value"] == "http://fake-dgx:9002" and "database_url" not in keys
    r = admin.put("/api/admin/settings", json={"disabled_adapters": ["trellis2"],
                                               "overrides": {"hunyuan_url": "http://10.0.1.106:9002"}})
    assert r.status_code == 200, r.text
    assert {f["key"]: f for f in r.json()["fields"]}["hunyuan_url"]["effective"] == "http://10.0.1.106:9002"
    assert admin.put("/api/admin/settings", json={"overrides": {"hunyuan_url": "not a url"}}).status_code == 422
    assert admin.put("/api/admin/settings", json={"overrides": {"database_url": "x"}}).status_code == 422
    ad = {a["name"]: a for a in admin.get("/api/adapters").json()}
    assert ad["trellis2"]["enabled"] is False and ad["trellis2"]["available"] is False
    asset, inp = _asset(admin), _inputs(admin)
    r = admin.post("/api/runs", json={"asset_id": asset["id"], "input_set_id": inp["id"], "workflow": "multiview",
                                      "adapters": ["trellis2"]})
    assert r.status_code == 409
    # the dispatcher sees the override
    from hub.worker import loop
    admin.post("/api/runs", json={"asset_id": asset["id"], "input_set_id": inp["id"], "workflow": "multiview",
                                  "adapters": ["hunyuan3d-2mv"]})
    assert loop(gpu=True, once=True) == 1
    admin.put("/api/admin/settings", json={"overrides": {"hunyuan_url": None}})
    assert {f["key"]: f for f in admin.get("/api/admin/settings").json()["fields"]}["hunyuan_url"]["override"] is None
    assert any(e["action"] == "settings.update" for e in admin.get("/api/admin/audit").json())


def test_usage_and_health(hub, fake_gpu):
    admin = hub(*ADMIN)
    asset, inp = _asset(admin), _inputs(admin)
    admin.post("/api/runs", json={"asset_id": asset["id"], "input_set_id": inp["id"], "workflow": "multiview",
                                  "adapters": ["hunyuan3d-2mv"]})
    from hub.worker import loop
    loop(gpu=True, once=True)
    u = admin.get("/api/admin/usage?days=7").json()
    assert u["rows"][0]["user"] == ADMIN[0] and u["rows"][0]["succeeded"] == 1
    assert u["rows"][0]["gpu_seconds"] == 1.5
    h = admin.get("/api/admin/health").json()
    assert h["runs"]["succeeded"] == 1 and h["users"] == 1 and h["active_sessions"] == 1


def test_existing_database_is_upgraded(tmp_path, monkeypatch):
    """A hub.db from before accounts gains workspace columns and its assets land in `default`."""
    import sqlite3
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE assets (id VARCHAR(16) PRIMARY KEY, name VARCHAR(200), family VARCHAR(80), site VARCHAR(40),
            description TEXT, tags JSON, library_ref VARCHAR(200), evidence_refs JSON, envelope JSON, created_at DATETIME);
        INSERT INTO assets VALUES ('a1', 'C3250', 'genset', 'HK6', '', '[]', '', '[]', '{}', '2026-10-13 00:00:00');
    """)
    con.commit()
    con.close()
    monkeypatch.setenv("HUB_DATA_DIR", str(tmp_path))
    from hub import db
    from hub.models import Asset, Workspace
    db.init_engine(f"sqlite:///{path}")
    with db.session() as s:
        a = s.get(Asset, "a1")
        assert s.get(Workspace, a.workspace_id).slug == "default"


def test_auth_off_needs_dev_flag(tmp_path, monkeypatch):
    import pytest
    from fastapi.testclient import TestClient

    from hub.api import app
    monkeypatch.setenv("HUB_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HUB_AUTH", "off")
    monkeypatch.delenv("HUB_DEV", raising=False)
    with pytest.raises(RuntimeError):
        with TestClient(app):
            pass


def test_pages_are_served(hub):
    c = hub()
    from hub.api import _dist
    if (_dist / "login.html").exists():
        assert c.get("/login").status_code == 200
        assert c.get("/admin").status_code == 200


def _glb():
    from .conftest import glb_bytes
    return glb_bytes()
