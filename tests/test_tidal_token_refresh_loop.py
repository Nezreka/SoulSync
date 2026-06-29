"""Regression: post-boot, is_authenticated() must NOT refresh a still-valid Tidal token.

#949 moved the "token still valid -> return True" short-circuit into the boot-phase branch
only, so every post-boot call fell through to the silent refresh — a constant-refresh loop
(wolf's logs: "access token expired -> refresh -> success" every few seconds)."""

import time

import core.boot_phase as boot_phase
from core.tidal_client import TidalClient


def _client(expires_at):
    c = TidalClient.__new__(TidalClient)
    c.access_token = "tok"
    c.refresh_token = "refresh"
    c.token_expires_at = expires_at
    c._refresh_calls = 0

    def _fake_refresh():
        c._refresh_calls += 1
        return True

    c._refresh_access_token = _fake_refresh
    return c


def test_valid_token_does_not_refresh_post_boot(monkeypatch):
    monkeypatch.setattr(boot_phase, "_boot_active", False)   # post-boot
    c = _client(time.time() + 3600)                          # valid for an hour
    assert c.is_authenticated() is True
    assert c._refresh_calls == 0                             # MUST NOT refresh a valid token


def test_expired_token_still_refreshes_post_boot(monkeypatch):
    monkeypatch.setattr(boot_phase, "_boot_active", False)
    c = _client(time.time() - 10)                            # expired
    assert c.is_authenticated() is True
    assert c._refresh_calls == 1                             # expired -> one refresh


def test_valid_token_returns_true_during_boot(monkeypatch):
    monkeypatch.setattr(boot_phase, "_boot_active", True)    # boot phase
    c = _client(time.time() + 3600)
    assert c.is_authenticated() is True
    assert c._refresh_calls == 0                             # boot never probes/refreshes
