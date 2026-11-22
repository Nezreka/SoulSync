"""a paired device through the real app (PAIRING_SPEC.md 4, 8): an
unprofiled token can do nothing but pick, the pick asks for the profile's
pin or password like the picker does, "Use music apps" filters the list and
holds after the pick, and revocation (one device, a profile's devices, sign
out everywhere, deleting the profile) is immediate."""

from __future__ import annotations

import json
import time
from uuid import uuid4

import pytest
from werkzeug.security import generate_password_hash

web_server = pytest.importorskip("web_server")

from core.connect import device_tokens as T  # noqa: E402


@pytest.fixture
def db():
    return web_server.get_database()


@pytest.fixture(autouse=True)
def _clean_limits(db):
    web_server._launch_pin_limiter.reset()
    web_server._login_limiter.reset()
    T._reset_for_tests()
    db.set_permission_policy_raw("{}")
    yield
    web_server._launch_pin_limiter.reset()
    web_server._login_limiter.reset()


def _anon():
    c = web_server.app.test_client()
    with c.session_transaction() as s:
        s.clear()
    return c


def _as(pid):
    c = web_server.app.test_client()
    with c.session_transaction() as s:
        s.clear()
        s["profile_id"] = pid
    return c


def _dev(token):
    return {"X-Device-Token": token}


def _template(db, slug):
    return next(t for t in db.list_permission_templates() if t.get("slug") == slug)


@pytest.fixture
def people(db):
    """a member on Adult, a child on Kids (music apps off), and a member
    with a pin"""
    made = []

    def make(slug, pin=None):
        name = "dev_%s_%s" % (slug, uuid4().hex[:6])
        pid = db.create_profile(name, "#123456",
                                generate_password_hash(pin, method="pbkdf2:sha256") if pin else None,
                                is_admin=False)
        tid = _template(db, slug)["id"]
        assert db.set_profile_permission_layers(pid, tid, "{}")
        from core import permission_store
        permission_store.recompute(db, [pid])
        made.append(pid)
        return pid

    out = {"adult": make("adult"), "kid": make("kids"), "pinned": make("adult", pin="4321")}
    yield out
    for pid in made:
        db.delete_profile(pid)


def _new_device(db, **kw):
    token, did = T.create(db, name=kw.pop("name", "Test phone"), **kw)
    return token, did


def _pick(token, pid, **body):
    return _anon().post("/api/devices/me/profile", headers=_dev(token), json={"profile_id": pid, **body})


# ── unprofiled ───────────────────────────────────────────────────────────

def test_an_unprofiled_device_may_only_pick(db, people):
    token, _ = _new_device(db)
    c = _anon()
    assert c.get("/api/devices/me", headers=_dev(token)).status_code == 200
    assert c.get("/api/devices/me/profiles", headers=_dev(token)).status_code == 200
    # anything else, even what every signed-in browser may call
    for path in ("/api/profiles/current", "/api/profiles", "/api/settings", "/api/devices",
                 "/api/profiles/me/permissions", "/api/system/status"):
        r = c.get(path, headers=_dev(token))
        assert r.status_code == 403, path
        assert r.get_json()["error"] in ("device_pick_first", "device_not_allowed"), path


def test_the_pick_list_leaves_out_profiles_without_music_apps(db, people):
    token, _ = _new_device(db)
    r = _anon().get("/api/devices/me/profiles", headers=_dev(token))
    ids = {p["id"] for p in r.get_json()["profiles"]}
    assert people["adult"] in ids and people["pinned"] in ids
    assert people["kid"] not in ids
    assert 1 in ids  # the admin has every permission
    pinned = next(p for p in r.get_json()["profiles"] if p["id"] == people["pinned"])
    assert pinned["needs"] == "pin"


def test_a_kid_cannot_be_picked_even_by_id(db, people):
    token, _ = _new_device(db)
    r = _pick(token, people["kid"])
    assert r.status_code == 404
    assert T.find(db, token)["profile_id"] is None


def test_the_pin_is_asked_and_wrong_tries_are_throttled(db, people):
    token, _ = _new_device(db)
    r = _pick(token, people["pinned"])
    assert r.status_code == 401 and r.get_json().get("pin_required")
    for _ in range(10):
        assert _pick(token, people["pinned"], pin="0000").status_code == 401
    # locked out now, even with the right pin
    assert _pick(token, people["pinned"], pin="4321").status_code == 429
    web_server._launch_pin_limiter.reset()
    assert _pick(token, people["pinned"], pin="4321").status_code == 200
    assert T.find(db, token)["profile_id"] == people["pinned"]


def test_login_mode_asks_for_the_password(db, people, monkeypatch):
    real_get = web_server.config_manager.get
    monkeypatch.setattr(web_server.config_manager, "get",
                        lambda k, d=None: True if k == "security.require_login" else real_get(k, d))
    db.set_profile_password(people["adult"], "goodpass1")
    token, _ = _new_device(db)
    # the device still reaches its pick with no session, through the login gate
    listed = _anon().get("/api/devices/me/profiles", headers=_dev(token)).get_json()["profiles"]
    assert next(p for p in listed if p["id"] == people["adult"])["needs"] == "password"
    r = _pick(token, people["adult"])
    assert r.status_code == 401 and r.get_json().get("password_required")
    assert _pick(token, people["adult"], password="wrong").status_code == 401
    assert _pick(token, people["adult"], password="goodpass1").status_code == 200


def test_the_pick_window_closes_after_15_minutes(db, people):
    token, did = _new_device(db, now=time.time() - 15 * 60 - 1)
    r = _pick(token, people["adult"])
    assert r.status_code == 401
    assert r.get_json()["error"] == "device_pick_expired"
    assert _anon().get("/api/devices/me", headers=_dev(token)).status_code == 401


def test_a_device_picks_once(db, people):
    token, _ = _new_device(db)
    assert _pick(token, people["adult"]).status_code == 200
    r = _pick(token, people["pinned"], pin="4321")
    assert r.status_code == 409
    assert T.find(db, token)["profile_id"] == people["adult"]
    assert _anon().get("/api/devices/me/profiles", headers=_dev(token)).status_code == 409


# ── after the pick ───────────────────────────────────────────────────────

def test_a_picked_device_is_that_profile_and_never_an_admin(db, people):
    token, _ = _new_device(db)
    assert _pick(token, 1).status_code == 200
    me = _anon().get("/api/devices/me", headers=_dev(token)).get_json()
    assert me["device"]["profile_id"] == 1 and me["device"]["state"] == "live"
    assert "last_ip" not in me["device"]
    # even picked as the admin, nothing admin is reachable
    for method, path in (("GET", "/api/devices"), ("GET", "/api/settings"), ("POST", "/api/settings"),
                         ("GET", "/api/v1/api-keys"), ("POST", "/api/profiles/invites"),
                         ("DELETE", "/api/devices/1"), ("POST", "/api/database/backup")):
        r = _anon().open(path, method=method, headers=_dev(token), json={})
        assert r.status_code == 403, (method, path)
        assert r.get_json()["error"] == "device_not_allowed"


def test_turning_music_apps_off_stops_a_device_that_picked(db, people):
    token, _ = _new_device(db)
    assert _pick(token, people["adult"]).status_code == 200
    assert _anon().get("/api/devices/me", headers=_dev(token)).status_code == 200
    db.set_profile_permission_layers(people["adult"], _template(db, "adult")["id"],
                                     json.dumps({"music.apps": False}))
    r = _anon().get("/api/devices/me", headers=_dev(token))
    assert r.status_code == 403 and r.get_json()["error"] == "device_profile_not_allowed"


def test_a_turned_off_profile_stops_its_devices(db, people):
    token, _ = _new_device(db)
    assert _pick(token, people["adult"]).status_code == 200
    db.update_profile(people["adult"], disabled=True)
    assert _anon().get("/api/devices/me", headers=_dev(token)).status_code == 403


def test_one_credential_per_request(db, people):
    token, _ = _new_device(db)
    r = _anon().get("/api/devices/me", headers={**_dev(token), "X-API-Key": "sk_whatever"})
    assert r.status_code == 400


def test_a_browser_without_a_token_gets_nothing_from_device_routes(db):
    assert _as(1).get("/api/devices/me").status_code == 401
    assert _as(1).post("/api/devices/me/profile", json={"profile_id": 1}).status_code == 401


# ── revocation ───────────────────────────────────────────────────────────

def test_revoking_one_device_is_immediate(db, people):
    token, did = _new_device(db)
    assert _pick(token, people["adult"]).status_code == 200
    listed = _as(1).get("/api/devices").get_json()["devices"]
    assert any(d["id"] == did and d["state"] == "live" for d in listed)
    assert _as(1).delete(f"/api/devices/{did}").status_code == 200
    r = _anon().get("/api/devices/me", headers=_dev(token))
    assert r.status_code == 401 and r.get_json()["error"] == "device_token_invalid"
    assert all(d["id"] != did for d in _as(1).get("/api/devices").get_json()["devices"])


def test_a_member_cannot_list_or_revoke_devices(db, people):
    _, did = _new_device(db)
    assert _as(people["adult"]).get("/api/devices").status_code == 403
    assert _as(people["adult"]).delete(f"/api/devices/{did}").status_code == 403


def test_revoking_a_profiles_devices(db, people):
    a, _ = _new_device(db)
    b, _ = _new_device(db)
    assert _pick(a, people["adult"]).status_code == 200
    assert _pick(b, people["adult"]).status_code == 200
    r = _as(1).post(f"/api/devices/profiles/{people['adult']}/revoke")
    assert r.get_json()["revoked"] == 2
    assert _anon().get("/api/devices/me", headers=_dev(a)).status_code == 401
    assert _anon().get("/api/devices/me", headers=_dev(b)).status_code == 401


def test_sign_out_everywhere_unpairs_the_profiles_devices(db, people):
    token, _ = _new_device(db)
    assert _pick(token, people["adult"]).status_code == 200
    assert _as(1).post(f"/api/profiles/{people['adult']}/sign-out-everywhere").status_code == 200
    assert _anon().get("/api/devices/me", headers=_dev(token)).status_code == 401


def test_deleting_the_profile_unpairs_its_devices(db):
    pid = db.create_profile("dev_del_%s" % uuid4().hex[:6], "#123456", None, is_admin=False)
    token, _ = _new_device(db)
    assert _pick(token, pid).status_code == 200
    assert db.delete_profile(pid)
    assert _anon().get("/api/devices/me", headers=_dev(token)).status_code == 401


def test_renaming_a_device(db, people):
    _, did = _new_device(db)
    assert _as(1).patch(f"/api/devices/{did}", json={"name": "Car stereo"}).status_code == 200
    assert T.get(db, did)["name"] == "Car stereo"
    assert _as(1).patch(f"/api/devices/{did}", json={"name": " "}).status_code == 400


# ── the shared pick check ────────────────────────────────────────────────

class _OneProfile:
    """a single-profile install whose only profile has a pin"""

    def __init__(self, ok_pin):
        self.ok_pin = ok_pin

    def get_all_profiles(self):
        return [{"id": 7}]

    def verify_profile_pin(self, pid, pin):
        return pin == self.ok_pin


def test_a_device_always_asks_a_pinned_profile_for_its_pin():
    from api.user_profiles import check_profile_pick
    profile = {"id": 7, "name": "solo", "has_pin": True}
    with web_server.app.test_request_context("/"):
        # the browser picker on a one-profile install doesn't ask
        refused, _ = check_profile_pick(_OneProfile("1111"), profile, pin="", password="", ip="1.2.3.4",
                                        now=time.time(), is_new_sign_in=True)
        assert refused is None
        # a device far away always does
        refused, _ = check_profile_pick(_OneProfile("1111"), profile, pin="", password="", ip="1.2.3.4",
                                        now=time.time(), is_new_sign_in=True, always_ask_pin=True)
        assert refused[1] == 401
        refused, checked = check_profile_pick(_OneProfile("1111"), profile, pin="1111", password="",
                                              ip="1.2.3.4", now=time.time(), is_new_sign_in=True,
                                              always_ask_pin=True)
        assert refused is None and checked


def test_the_picker_still_works_as_before(db, people):
    c = _anon()
    r = c.post("/api/profiles/select", json={"profile_id": people["pinned"]})
    assert r.status_code == 401 and r.get_json()["pin_required"]
    r = c.post("/api/profiles/select", json={"profile_id": people["pinned"], "pin": "4321"})
    assert r.status_code == 200


# ── "Use music apps" ─────────────────────────────────────────────────────

def test_music_apps_is_on_for_adults_and_off_on_kids(db):
    from core.permissions import CATALOGUE, TEMPLATE_DEFAULTS
    assert CATALOGUE["music.apps"].label == "Use music apps"
    assert TEMPLATE_DEFAULTS["music.apps"] is True
    kids = json.loads(_template(db, "kids")["perms"])
    adult = json.loads(_template(db, "adult")["perms"])
    assert kids["music.apps"] is False
    assert adult.get("music.apps", True) is True


def test_an_older_kids_template_gets_music_apps_off_once():
    import sqlite3
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
    cur.execute("CREATE TABLE permission_templates (id INTEGER PRIMARY KEY, slug TEXT, perms TEXT, "
                "builtin INTEGER, updated_at TIMESTAMP)")
    cur.execute("INSERT INTO permission_templates VALUES (1, 'kids', '{\"music.access\": true}', 1, NULL)")
    cur.execute("INSERT INTO permission_templates VALUES (2, 'adult', '{\"music.access\": true}', 1, NULL)")
    db = web_server.get_database()
    db._music_apps_off_for_kids(cur)
    perms = {r[0]: json.loads(r[1]) for r in cur.execute("SELECT id, perms FROM permission_templates")}
    assert perms[1]["music.apps"] is False
    assert "music.apps" not in perms[2]
    # once: an admin who turns it back on keeps it
    cur.execute("UPDATE permission_templates SET perms = '{\"music.apps\": true}' WHERE id = 1")
    db._music_apps_off_for_kids(cur)
    assert json.loads(cur.execute("SELECT perms FROM permission_templates WHERE id = 1").fetchone()[0]) == {
        "music.apps": True}
