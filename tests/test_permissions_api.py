"""the people and permissions api (P0): every route admin only except
/api/profiles/me/permissions; shapes as scrap/permissions/API.md says."""

from __future__ import annotations

from uuid import uuid4

import pytest

from core import permissions as P

web_server = pytest.importorskip("web_server")


def _as(pid):
    c = web_server.app.test_client()
    with c.session_transaction() as s:
        s.clear()
        s["profile_id"] = pid
    return c


def _anon():
    c = web_server.app.test_client()
    with c.session_transaction() as s:
        s.clear()
    return c


@pytest.fixture
def db():
    return web_server.get_database()


@pytest.fixture
def member(db):
    pid = db.create_profile(name="m_%s" % uuid4().hex[:8], can_download=False, allowed_sides="both")
    db.update_profile(pid, max_rating="PG-13")
    db.remap_legacy_profiles()
    yield pid
    db.delete_profile(pid)


@pytest.fixture
def other(db):
    pid = db.create_profile(name="o_%s" % uuid4().hex[:8], can_download=True, allowed_sides="both")
    db.remap_legacy_profiles()
    yield pid
    db.delete_profile(pid)


@pytest.fixture(autouse=True)
def _signup_settings_moved(db):
    """the old sign-up settings already moved (#2166): templates stay the
    four built-ins whatever sign-in settings earlier tests saved"""
    from core import permission_store as PS
    keys = (PS.SIGNUP_MOVED_KEY, "perm_signup_template")
    saved = {k: db.get_metadata(k) for k in keys}
    guest = next(t["id"] for t in db.list_permission_templates() if t.get("slug") == "guest")
    # an earlier test's sign-in can leave a "New sign-ups" template and
    # point sign-up at it: back to the four built-ins, sign-up on Guest
    for t in db.list_permission_templates():
        if (t.get("name") or "").lower() == PS.SIGNUP_TEMPLATE_NAME.lower():
            db.delete_permission_template(int(t["id"]), reassign_to=guest)
    if saved.get("perm_signup_template") and not db.get_permission_template(int(saved["perm_signup_template"])):
        saved["perm_signup_template"] = str(guest)
    db.set_metadata("perm_signup_template", str(guest))
    db.set_metadata(PS.SIGNUP_MOVED_KEY, "1")
    yield
    for k, v in saved.items():
        if v is None:
            db.delete_metadata(k)
        else:
            db.set_metadata(k, v)


@pytest.fixture(autouse=True)
def _open_policy(db):
    db.set_permission_policy_raw("{}")
    yield
    db.set_permission_policy_raw("{}")


ADMIN_ROUTES = [
    ("get", "/api/permissions/catalogue"),
    ("get", "/api/permissions/quality-options"),
    ("get", "/api/permissions/policy"),
    ("put", "/api/permissions/policy"),
    ("get", "/api/permissions/templates"),
    ("post", "/api/permissions/templates"),
    ("put", "/api/permissions/templates/1"),
    ("delete", "/api/permissions/templates/1"),
    ("put", "/api/permissions/signup-template"),
    ("get", "/api/profiles/1/permissions"),
    ("put", "/api/profiles/1/permissions"),
]


@pytest.mark.parametrize("method,path", ADMIN_ROUTES)
def test_members_are_refused_every_admin_route(member, method, path):
    r = getattr(_as(member), method)(path, json={"set": {"music.get": "download"}, "name": "x"})
    assert r.status_code == 403, (method, path, r.get_json())


def test_a_member_cant_raise_their_own_permissions(member, db):
    r = _as(member).put("/api/profiles/%d/permissions" % member, json={"set": {"music.get": "download"}})
    assert r.status_code == 403
    assert db.get_profile(member)["can_download"] is False


def test_catalogue(member):
    body = _as(1).get("/api/permissions/catalogue").get_json()
    assert body["success"] and len(body["rows"]) == len(P.CATALOGUE_ROWS)
    assert [c["id"] for c in body["categories"]] == list(P.CATEGORY_IDS)
    assert body["open_policy"] == P.OPEN_POLICY


def test_quality_options_always_has_video_default():
    body = _as(1).get("/api/permissions/quality-options").get_json()
    assert body["success"]
    first = dict(body["movies"][0])
    # each one says what it fetches ("720p to 1080p", #2166 follow-up 3)
    assert isinstance(first.pop("range"), str)
    assert first == {"id": 0, "name": "Default", "media_type": None, "tags": []}
    assert body["tv"][0]["id"] == 0
    assert isinstance(body["music"], list)


def test_policy_get_dry_run_and_save(member, other, db):
    c = _as(1)
    body = c.get("/api/permissions/policy").get_json()
    assert body["policy"] == P.OPEN_POLICY and body["changed"] == []
    r = c.put("/api/permissions/policy?dry_run=1", json={"set": {"music.get": "ask"}})
    body = r.get_json()
    assert r.status_code == 200 and body["dry_run"] is True
    moved = {ch["profile_id"]: ch["rows"] for ch in body["impact"]["changes"]}
    assert {"id": "music.get", "before": "download", "after": "ask"} in moved[other]
    assert member not in moved
    assert db.get_profile(other)["can_download"] is True          # nothing saved
    r = c.put("/api/permissions/policy", json={"set": {"music.get": "ask"}})
    assert r.status_code == 200 and r.get_json()["policy"]["music.get"] == "ask"
    assert db.get_profile(other)["can_download"] is False
    assert c.get("/api/permissions/policy").get_json()["changed"] == ["music.get"]
    r = c.put("/api/permissions/policy", json={"reset": "all"})
    assert db.get_profile(other)["can_download"] is True
    r = c.put("/api/permissions/policy", json={"set": {"music.get": "lots"}})
    assert r.status_code == 400 and r.get_json()["error"] == "invalid"


def test_templates_crud(member, db):
    c = _as(1)
    body = c.get("/api/permissions/templates").get_json()
    slugs = [t["slug"] for t in body["templates"]]
    assert slugs[:4] == ["adult", "teen", "kids", "guest"]
    teen = body["templates"][1]
    assert any(f["id"] == member for f in teen["followers"])
    assert body["signup_template_id"] == body["templates"][3]["id"]
    name = "T %s" % uuid4().hex[:6]
    r = c.post("/api/permissions/templates", json={"name": name, "from_template_id": teen["id"],
                                                     "perms": {"requests.moderate": True}})
    assert r.status_code == 201
    made = r.get_json()["template"]
    assert made["perms"]["requests.moderate"] is True and made["builtin"] is False
    r = c.put("/api/permissions/templates/%d?dry_run=1" % teen["id"], json={"perms": {"movies.get": "download"}})
    assert r.get_json()["impact"]["changes"][0]["profile_id"] in {f["id"] for f in teen["followers"]}
    assert db.get_profile(member)["can_download"] is False
    r = c.delete("/api/permissions/templates/%d" % teen["id"])
    assert r.status_code == 409 and r.get_json()["error"] == "has_followers"
    r = c.put("/api/permissions/signup-template", json={"template_id": made["id"]})
    assert r.get_json()["signup_template_id"] == made["id"]
    r = c.delete("/api/permissions/templates/%d" % made["id"])
    assert r.status_code == 409 and r.get_json()["error"] == "signup_template"
    c.put("/api/permissions/signup-template", json={"template_id": body["signup_template_id"]})
    r = c.delete("/api/permissions/templates/%d" % made["id"])
    assert r.status_code == 200
    assert c.put("/api/permissions/templates/999999", json={}).status_code == 404


def test_profile_permissions_get_has_provenance(member):
    body = _as(1).get("/api/profiles/%d/permissions" % member).get_json()
    assert body["mode"] == "template" and body["template"]["slug"] == "teen"
    rows = {r["id"]: r for r in body["rows"]}
    assert rows["movies.get"] == {"id": "movies.get", "value": "ask", "source": "template",
                                  "template_value": "ask", "override": False, "override_value": None,
                                  "ceiling": "download", "capped": False, "inert": False}
    assert body["effective"]["movies.max_rating"] == "PG-13"
    assert body["summary"]["lines"]
    assert _as(1).get("/api/profiles/999999/permissions").status_code == 404


def test_profile_permissions_put_set_reset_and_switch(member, db):
    c = _as(1)
    r = c.put("/api/profiles/%d/permissions" % member, json={"set": {"music.get": "download",
                                                                     "requests.see_all": True}})
    body = r.get_json()
    assert r.status_code == 200 and body["overrides"] == {"music.get": "download", "requests.see_all": True}
    assert db.get_profile(member)["can_download"] is True
    assert {"music", "requests"} <= set(body["changed_categories"])
    r = c.put("/api/profiles/%d/permissions" % member, json={"reset_categories": ["music"]})
    assert r.get_json()["overrides"] == {"requests.see_all": True}
    assert db.get_profile(member)["can_download"] is False
    r = c.put("/api/profiles/%d/permissions" % member, json={"reset": "all"})
    assert r.get_json()["overrides"] == {}
    kids = next(t["id"] for t in db.list_permission_templates() if t["slug"] == "kids")
    r = c.put("/api/profiles/%d/permissions" % member, json={"template_id": kids})
    assert r.get_json()["template"]["slug"] == "kids"
    assert db.get_profile(member)["hide_explicit"] is True and db.get_profile(member)["max_rating"] == "PG"
    assert c.put("/api/profiles/1/permissions", json={"set": {"music.get": "ask"}}).status_code == 409
    r = c.put("/api/profiles/%d/permissions" % member, json={"set": {"ui.sidebar": []}})
    assert r.status_code == 400
    r = c.put("/api/profiles/%d/permissions" % member, json=["not", "an", "object"])
    assert r.status_code == 400


def test_capped_rows_show_the_lock(member):
    c = _as(1)
    c.put("/api/profiles/%d/permissions" % member, json={"set": {"movies.get": "download"}})
    c.put("/api/permissions/policy", json={"set": {"movies.get": "ask"}})
    body = c.get("/api/profiles/%d/permissions" % member).get_json()
    assert "movies.get" in body["capped"]
    row = next(r for r in body["rows"] if r["id"] == "movies.get")
    assert row["inert"] and row["value"] == "ask" and row["override_value"] == "download"


def test_me_is_only_ever_yourself(member, other):
    body = _as(member).get("/api/profiles/me/permissions").get_json()
    assert body["profile_id"] == member and body["source"] == "template"
    assert body["effective"]["movies.get"] == "ask" and not body["is_admin"]
    assert "rows" not in body and "overrides" not in body
    assert body["summary"]["sentence"].startswith("You use")
    body = _as(1).get("/api/profiles/me/permissions").get_json()
    assert body["is_admin"] and body["effective"] == P.admin_map()


def test_me_without_a_profile_is_refused(db):
    if len(db.get_all_profiles()) < 2:
        db.create_profile(name="pad_%s" % uuid4().hex[:6])
    r = _anon().get("/api/profiles/me/permissions")
    assert r.status_code == 401


def test_me_for_a_turned_off_profile_is_refused(member, db):
    db.update_profile(member, disabled=1)
    assert _as(member).get("/api/profiles/me/permissions").status_code == 401


def test_routes_declare_their_access():
    vf = web_server.app.view_functions
    for ep in ("permissions.catalogue", "permissions.put_policy", "permissions.put_profile_permissions",
               "permissions.delete_template"):
        assert P.declared_access(vf[ep]) == ("admin", None), ep
    assert P.declared_access(vf["permissions.my_permissions"]) == ("self", None)
