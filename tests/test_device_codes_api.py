"""device codes through the real app (PAIRING_SPEC.md 2, 3, 8): only an
admin makes them, redemption burns the code and hands back an unprofiled
token, wrong codes are throttled per address and lock a code after 5, and a
code never crosses the internet in the clear. person invites last a week
unless chosen."""

from __future__ import annotations

import json
import re
from uuid import uuid4

import pytest

web_server = pytest.importorskip("web_server")

import api.devices as devices_api  # noqa: E402
from core.connect import device_tokens as T  # noqa: E402
from core.connect import invite_codes as C  # noqa: E402

HOME = "192.168.1.20"
OUTSIDE = "203.0.113.9"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    devices_api._redeem_limiter.reset()
    db = web_server.get_database()
    with db._get_connection() as conn:
        conn.execute("UPDATE device_invite_codes SET cancelled_at = CURRENT_TIMESTAMP WHERE used_at IS NULL")
        conn.commit()
    yield
    devices_api._redeem_limiter.reset()


def _as(pid):
    c = web_server.app.test_client()
    with c.session_transaction() as s:
        s.clear()
        s["profile_id"] = pid
    return c


def _anon(ip=HOME, https=False):
    return web_server.app.test_client(), {"REMOTE_ADDR": ip}


def _new_code(note=""):
    r = _as(1).post("/api/devices/codes", json={"note": note})
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def _redeem(code, ip=HOME, https=False, **extra):
    c, env = _anon(ip, https)
    return c.post("/api/devices/redeem", json={"code": code, "name": "Kitchen speaker", "app": "Test",
                                                "platform": "Android", **extra}, environ_base=env,
                  base_url="https://localhost" if https else "http://localhost")


def test_an_admin_makes_a_code_and_sees_it_once():
    body = _new_code("for the car")
    assert re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{4}(-[0-9A-HJKMNP-TV-Z]{4}){3}", body["code"])
    canonical = body["code"].replace("-", "")
    assert body["code_hash"] == C.code_hash(canonical)
    assert body["seconds_left"] == 600
    assert body["payload"]["code"] == body["code"]
    assert body["payload"]["fingerprint"] == body["payload"]["lookup_id"][:12]
    assert body["qr_text"].startswith("soulsync://pair?d=")
    assert json.loads(body["payload_text"]) == body["payload"]
    listed = _as(1).get("/api/devices/codes").get_json()["codes"]
    assert any(c["id"] == body["id"] and c["note"] == "for the car" for c in listed)
    assert canonical not in str(listed) and body["code"] not in str(listed)


def test_members_cannot_make_or_see_codes():
    db = web_server.get_database()
    pid = db.create_profile("codes_member_%s" % uuid4().hex[:6], "#123456", None, is_admin=False)
    try:
        assert _as(pid).post("/api/devices/codes", json={}).status_code == 403
        assert _as(pid).get("/api/devices/codes").status_code == 403
    finally:
        db.delete_profile(pid)


def test_redeeming_burns_the_code_and_gives_an_unprofiled_token():
    code = _new_code()["code"]
    r = _redeem(code.lower())
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    assert body["token"].startswith("sdt_") and body["pick_seconds"] == 15 * 60
    row = T.find(web_server.get_database(), body["token"])
    assert row["profile_id"] is None and row["name"] == "Kitchen speaker" and row["platform"] == "Android"
    me = web_server.app.test_client().get("/api/devices/me", headers={"X-Device-Token": body["token"]})
    assert me.status_code == 200 and me.get_json()["device"]["state"] == "unprofiled"
    # once only
    assert _redeem(code).status_code == 404


def test_every_failure_reads_the_same():
    used = _new_code()["code"]
    assert _redeem(used).status_code == 201
    cancelled = _new_code()
    _as(1).delete(f"/api/devices/codes/{cancelled['id']}")
    answers = [_redeem(c).get_json() for c in (used, cancelled["code"], "0000-0000-0000-0000", "nope")]
    assert all(a == answers[0] for a in answers), answers
    assert answers[0]["error"] == "unknown_or_expired_code"


def test_wrong_codes_are_throttled_per_address():
    good = _new_code()["code"]
    for _ in range(10):
        assert _redeem("0000-0000-0000-0000", ip="10.0.0.77").status_code == 404
    r = _redeem(good, ip="10.0.0.77")
    assert r.status_code == 429 and r.headers.get("Retry-After")
    # another address isn't held back
    assert _redeem(good, ip="10.0.0.78").status_code == 201


def test_five_wrong_tries_on_one_code_lock_it():
    good = _new_code()["code"].replace("-", "")
    wrong = good[:4] + ("0" * 12 if good[4:] != "0" * 12 else "1" * 12)
    for i in range(5):
        assert _redeem(wrong, ip=f"10.0.1.{i}").status_code == 404
    assert _redeem(good, ip="10.0.1.99").status_code == 404


def test_a_code_never_crosses_the_internet_in_the_clear():
    code = _new_code()["code"]
    r = _redeem(code, ip=OUTSIDE)
    assert r.status_code == 403 and r.get_json()["error"] == "https_required"
    # the refusal didn't spend it, and https from outside works
    assert _redeem(code, ip=OUTSIDE, https=True).status_code == 201


def test_redemption_gets_through_login_mode_without_a_session(monkeypatch):
    code = _new_code()["code"]
    real_get = web_server.config_manager.get
    monkeypatch.setattr(web_server.config_manager, "get",
                        lambda k, d=None: True if k == "security.require_login" else real_get(k, d))
    assert _redeem(code).status_code == 201


def test_at_most_20_codes_wait():
    for _ in range(20):
        _new_code()
    r = _as(1).post("/api/devices/codes", json={})
    assert r.status_code == 409


def test_redemption_is_in_the_admin_log():
    code = _new_code()["code"]
    _redeem(code, name="Log speaker")
    entries = _as(1).get("/api/profiles/audit").get_json()["entries"]
    assert any(e["action"] == "device_code_used" and e.get("detail") == "Log speaker" for e in entries[:20])


def test_person_invites_last_a_week_unless_chosen():
    db = web_server.get_database()
    tid = next(t for t in db.list_permission_templates() if t.get("slug") == "guest")["id"]
    r = _as(1).post("/api/profiles/invites", json={"preset": {"template_id": tid}})
    assert r.status_code == 201 and r.get_json()["expires_hours"] == 168
    r = _as(1).post("/api/profiles/invites", json={"preset": {"template_id": tid}, "expires_hours": 24})
    assert r.get_json()["expires_hours"] == 24
