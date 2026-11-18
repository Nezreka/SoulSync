"""invites start from a template (#2166 follow-up 5).

a new invite names a template; whoever joins with it follows that template
with no personal changes, and their old profile columns are written from it.
old invites (column presets) keep working as before. a template that's gone
by the time the link is used falls back to the one new people start as.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

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


@pytest.fixture(autouse=True)
def _open_policy(db):
    db.set_permission_policy_raw("{}")
    yield
    db.set_permission_policy_raw("{}")


def _template(db, slug):
    return next(t for t in db.list_permission_templates() if t.get("slug") == slug)


def _invite(preset):
    r = _as(1).post("/api/profiles/invites", json={"preset": preset, "expires_hours": 24})
    return r


def _accept(token):
    name = "inv_%s" % uuid4().hex[:8]
    r = _anon().post(f"/api/invite/{token}/accept", json={"name": name})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["profile_id"]


def test_a_template_invite_joins_following_it(db):
    teen = _template(db, "teen")
    r = _invite({"template_id": teen["id"]})
    assert r.status_code == 201
    token = r.get_json()["token"]
    # the invite page says what the template gives
    info = _anon().get(f"/api/invite/{token}").get_json()
    assert info["sides"] == "both" and info["can_download"] is False
    pid = _accept(token)
    try:
        inputs = db.get_permission_inputs(pid)
        assert inputs["template_id"] == teen["id"]
        assert json.loads(inputs["overrides"] or "{}") == {}
        row = db.get_profile(pid)
        # the columns come from teen: both sides, asks first, up to PG-13
        assert row["allowed_sides"] == "both"
        assert row["can_download"] is False
        assert row["max_rating"] == "PG-13"
        assert row["is_admin"] is False
    finally:
        db.delete_profile(pid)


def test_an_adult_invite_can_download(db):
    adult = _template(db, "adult")
    pid = _accept(_invite({"template_id": adult["id"]}).get_json()["token"])
    try:
        assert db.get_permission_inputs(pid)["template_id"] == adult["id"]
        assert db.get_profile(pid)["can_download"] is True
    finally:
        db.delete_profile(pid)


def test_an_unknown_template_is_refused_when_making_the_link(db):
    r = _invite({"template_id": 987654})
    assert r.status_code == 400
    assert "template" in r.get_json()["error"].lower()
    assert _invite({"template_id": "teen"}).status_code == 400


def test_a_deleted_template_falls_back_to_the_signup_template(db):
    made = db.create_permission_template(name="gone_%s" % uuid4().hex[:6], description="",
                                         perms=_template(db, "adult")["perms"], sidebar=None)
    token = _invite({"template_id": made}).get_json()["token"]
    assert db.delete_permission_template(made)
    pid = _accept(token)
    try:
        signup = int(db.get_metadata("perm_signup_template"))
        assert db.get_permission_inputs(pid)["template_id"] == signup
        # never the gone template's download rights
        assert db.get_profile(pid)["can_download"] is False
    finally:
        db.delete_profile(pid)


def test_an_old_column_invite_still_works(db):
    token = _invite({"allowed_sides": "music", "can_download": False, "max_rating": "PG"}).get_json()["token"]
    pid = _accept(token)
    try:
        row = db.get_profile(pid)
        assert row["allowed_sides"] == "music" and row["can_download"] is False
        assert row["max_rating"] == "PG"
    finally:
        db.delete_profile(pid)


def test_a_preset_never_makes_an_admin(db):
    teen = _template(db, "teen")
    pid = _accept(_invite({"template_id": teen["id"], "is_admin": True}).get_json()["token"])
    try:
        assert db.get_profile(pid)["is_admin"] is False
    finally:
        db.delete_profile(pid)
