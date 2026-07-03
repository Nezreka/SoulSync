"""Regression: the Tidal DOWNLOAD client's is_authenticated() must not hit Tidal on every call.

Anarkari: "Tidal keeps getting turned off as a download source — I authenticate, save, 5 minutes later
it's unselected." The download-source status poll calls is_configured() -> is_available() ->
is_authenticated(), which used to do `self.session.check_login()` (a LIVE Tidal API call) EVERY time.
A single transient failure there (rate-limit from frequent polling, a network blip, a refresh hiccup)
flipped a perfectly-good Tidal source to "unconfigured", which the settings UI then auto-deselected —
the same class of bug as the Deezer drop.

The fix: a valid, unexpired token means authenticated via a cheap LOCAL check (no network); only fall
back to a live check_login when the token is expired, and cache that result briefly.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import core.boot_phase as boot
from core.tidal_download_client import TidalDownloadClient


class _FakeSession:
    def __init__(self, access_token="tok", expiry_ts=None, check_result=True, raises=False):
        self.access_token = access_token
        self.token_type = "Bearer"
        self.refresh_token = "r"
        self.expiry_time = (
            datetime.fromtimestamp(expiry_ts, tz=timezone.utc) if expiry_ts else None)
        self._check_result = check_result
        self._raises = raises
        self.check_login_calls = 0

    def check_login(self):
        self.check_login_calls += 1
        if self._raises:
            raise RuntimeError("boom")
        return self._check_result


def _client(session):
    """Bare client (no __init__ / config / network) — just the auth-state seam under test."""
    c = TidalDownloadClient.__new__(TidalDownloadClient)
    c.session = session
    c._boot_session_tokens = None
    c._last_login_check_at = 0.0
    c._last_login_check_ok = False
    return c


def test_valid_token_is_authenticated_without_a_live_call(monkeypatch):
    monkeypatch.setattr(boot, "is_boot_phase", lambda: False)
    # check_result=False proves the live call is NOT consulted for a valid token.
    s = _FakeSession(expiry_ts=time.time() + 3600, check_result=False)
    c = _client(s)
    assert c.is_authenticated() is True
    assert s.check_login_calls == 0


def test_expired_token_falls_back_to_a_live_check(monkeypatch):
    monkeypatch.setattr(boot, "is_boot_phase", lambda: False)
    s = _FakeSession(expiry_ts=time.time() - 10, check_result=True)
    c = _client(s)
    assert c.is_authenticated() is True
    assert s.check_login_calls == 1


def test_expired_token_live_false_is_unauthenticated(monkeypatch):
    monkeypatch.setattr(boot, "is_boot_phase", lambda: False)
    c = _client(_FakeSession(expiry_ts=time.time() - 10, check_result=False))
    assert c.is_authenticated() is False


def test_live_check_is_cached_within_ttl(monkeypatch):
    monkeypatch.setattr(boot, "is_boot_phase", lambda: False)
    s = _FakeSession(expiry_ts=time.time() - 10, check_result=True)
    c = _client(s)
    assert c.is_authenticated() is True
    assert c.is_authenticated() is True
    assert s.check_login_calls == 1   # second call used the cache, not the network


def test_transient_live_failure_is_swallowed(monkeypatch):
    monkeypatch.setattr(boot, "is_boot_phase", lambda: False)
    c = _client(_FakeSession(expiry_ts=time.time() - 10, raises=True))
    assert c.is_authenticated() is False


def test_no_session_is_false(monkeypatch):
    monkeypatch.setattr(boot, "is_boot_phase", lambda: False)
    assert _client(None).is_authenticated() is False


def test_boot_phase_uses_pending_tokens(monkeypatch):
    monkeypatch.setattr(boot, "is_boot_phase", lambda: True)
    c = _client(None)
    c._boot_session_tokens = {"access_token": "tok"}
    assert c.is_authenticated() is True
    c._boot_session_tokens = {"access_token": ""}
    assert c.is_authenticated() is False


def test_transient_failure_is_not_cached_next_poll_rechecks(monkeypatch):
    # A negative result must NOT linger — otherwise one blip keeps a working source deselected.
    monkeypatch.setattr(boot, "is_boot_phase", lambda: False)
    s = _FakeSession(expiry_ts=time.time() - 10, check_result=False)
    c = _client(s)
    assert c.is_authenticated() is False
    assert c.is_authenticated() is False
    assert s.check_login_calls == 2   # each poll re-checks; False was not cached
