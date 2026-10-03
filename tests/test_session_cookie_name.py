"""The session cookie must not use Flask's default "session" name, which other
apps on the same host share and overwrite (cookies ignore ports)."""

from __future__ import annotations

import pytest

web_server = pytest.importorskip("web_server")


def test_session_cookie_has_unique_name():
    assert web_server.app.config["SESSION_COOKIE_NAME"] == "soulsync_session"


def test_session_write_sets_named_cookie():
    web_server.app.config["TESTING"] = True
    client = web_server.app.test_client()
    with client.session_transaction() as sess:
        sess["profile_id"] = 2
    assert client.get_cookie("soulsync_session") is not None
    assert client.get_cookie("session") is None
