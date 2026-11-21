"""GET /api/devices/identity: the fingerprint Settings shows, admins only,
never the secret."""

from __future__ import annotations

import pytest

web_server = pytest.importorskip("web_server")


def _as(pid):
    c = web_server.app.test_client()
    with c.session_transaction() as s:
        s.clear()
        s["profile_id"] = pid
    return c


def test_the_admin_sees_the_fingerprint_and_never_the_secret():
    from core.connect.server_key import server_identity
    ident = server_identity(web_server.get_database(), web_server.config_manager)
    r = _as(1).get("/api/devices/identity")
    assert r.status_code == 200
    body = r.get_json()
    assert body["fingerprint"] == ident.fingerprint == ident.lookup_id[:12]
    assert body["lookup_id"] == ident.lookup_id
    assert ident.key not in r.get_data(as_text=True)


def test_a_member_is_refused():
    db = web_server.get_database()
    pid = db.create_profile("ident_member", "#123456", None, is_admin=False)
    try:
        assert _as(pid).get("/api/devices/identity").status_code == 403
    finally:
        db.delete_profile(pid)
