"""Tidal DOWNLOAD session restore must refresh an expired token, not drop it (#1002).

QT3496: Tidal download source disconnects on every restart. Cause: the saved
access token is expired after a restart, and ``check_login()`` verifies but
does NOT refresh — so the restore declared the session invalid and dropped it,
even though the stored refresh token was still good.

_restore_and_verify now refreshes explicitly when the loaded token is stale.
These pin: the recovery path, and the safety guards (save only on confirmed
success; never save/overwrite on failure so the next restart can retry).
"""

from __future__ import annotations

import core.tidal_download_client as tdc
from core.tidal_download_client import TidalDownloadClient


class _Cfg:
    def __init__(self):
        self.saved = []

    def set(self, key, value):
        self.saved.append((key, value))

    def get(self, key, default=None):
        return default


class _FakeSession:
    def __init__(self, *, load_ok=True, check_results=None, refresh=None):
        self.token_type = "Bearer"
        self.access_token = "tok"
        self.refresh_token = "r"
        self.expiry_time = None
        self._load_ok = load_ok
        self._checks = list(check_results if check_results is not None else [True])
        self._refresh = refresh  # True | False | an Exception instance
        self.token_refresh_calls = 0

    def load_oauth_session(self, **kw):
        return self._load_ok

    def check_login(self):
        return self._checks.pop(0) if self._checks else False

    def token_refresh(self, refresh_token):
        self.token_refresh_calls += 1
        if isinstance(self._refresh, Exception):
            raise self._refresh
        return self._refresh


def _client(session, monkeypatch):
    cfg = _Cfg()
    monkeypatch.setattr(tdc, "config_manager", cfg)
    c = TidalDownloadClient.__new__(TidalDownloadClient)
    c.session = session
    return c, cfg


def _saved_session(cfg):
    return [v for k, v in cfg.saved if k == "tidal_download.session"]


# --- valid token: restore succeeds, no refresh ------------------------------

def test_valid_token_restores_without_refresh(monkeypatch):
    s = _FakeSession(check_results=[True])
    c, cfg = _client(s, monkeypatch)
    assert c._restore_and_verify("Bearer", "tok", "r", 0) is True
    assert s.token_refresh_calls == 0
    assert len(_saved_session(cfg)) == 1


# --- THE FIX: expired token -> explicit refresh -> recovered ----------------

def test_expired_token_is_refreshed_and_recovered(monkeypatch):
    # First check_login False (expired) -> refresh True -> check_login True.
    s = _FakeSession(check_results=[False, True], refresh=True)
    c, cfg = _client(s, monkeypatch)
    assert c._restore_and_verify("Bearer", "tok", "r", 0) is True
    assert s.token_refresh_calls == 1
    assert len(_saved_session(cfg)) == 1   # refreshed session persisted


# --- safety: failures never save / overwrite the stored tokens --------------

def test_refresh_returns_false_does_not_save(monkeypatch):
    s = _FakeSession(check_results=[False], refresh=False)
    c, cfg = _client(s, monkeypatch)
    assert c._restore_and_verify("Bearer", "tok", "r", 0) is False
    assert _saved_session(cfg) == []       # stored config tokens left intact


def test_refresh_raises_is_swallowed_and_not_saved(monkeypatch):
    from tidalapi.exceptions import AuthenticationError
    s = _FakeSession(check_results=[False], refresh=AuthenticationError("expired"))
    c, cfg = _client(s, monkeypatch)
    assert c._restore_and_verify("Bearer", "tok", "r", 0) is False
    assert s.token_refresh_calls == 1
    assert _saved_session(cfg) == []


def test_no_refresh_token_no_refresh_attempt(monkeypatch):
    s = _FakeSession(check_results=[False])
    c, cfg = _client(s, monkeypatch)
    assert c._restore_and_verify("Bearer", "tok", "", 0) is False
    assert s.token_refresh_calls == 0
    assert _saved_session(cfg) == []


def test_load_failure_returns_false_no_refresh(monkeypatch):
    s = _FakeSession(load_ok=False)
    c, cfg = _client(s, monkeypatch)
    assert c._restore_and_verify("Bearer", "tok", "r", 0) is False
    assert s.token_refresh_calls == 0
    assert _saved_session(cfg) == []
