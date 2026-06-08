"""Genius 429 backoff must be a fail-fast gate, never a sleep.

The old wrapper slept the backoff (30-120s) in the calling thread — while
holding the global API lock, serializing every other Genius caller behind
it — and then re-raised anyway. The import pipeline measurably napped
2x120s per track ("Genius track lookup took 242.4s") for lookups that
still failed.
"""

from __future__ import annotations

import time

import pytest
import requests

import core.genius_client as gc


def _fresh(monkeypatch):
    monkeypatch.setattr(gc, '_rate_limit_until', 0)
    monkeypatch.setattr(gc, '_rate_limit_backoff', 0)
    monkeypatch.setattr(gc, '_last_api_call_time', 0)


def test_backoff_window_fails_fast_without_sleeping(monkeypatch):
    _fresh(monkeypatch)
    monkeypatch.setattr(gc, '_rate_limit_until', time.time() + 120)

    @gc.rate_limited
    def call():
        raise AssertionError('must not reach the API during a backoff window')

    started = time.time()
    with pytest.raises(gc.GeniusRateLimitedError):
        call()
    assert time.time() - started < 0.5  # the old code slept the full window here


def test_429_opens_the_gate_without_sleeping_and_escalates(monkeypatch):
    _fresh(monkeypatch)

    @gc.rate_limited
    def call():
        raise requests.exceptions.HTTPError('429 Client Error: Too Many Requests')

    started = time.time()
    with pytest.raises(requests.exceptions.HTTPError):
        call()
    assert time.time() - started < 0.5            # old code slept 30s+ here
    assert gc._rate_limit_until > time.time()      # the gate is open
    assert gc._rate_limit_backoff == 30

    # Next 429 (after the window expires) doubles the gate: 30 -> 60
    monkeypatch.setattr(gc, '_rate_limit_until', 0)
    monkeypatch.setattr(gc, '_last_api_call_time', 0)
    with pytest.raises(requests.exceptions.HTTPError):
        call()
    assert gc._rate_limit_backoff == 60


def test_rate_limited_error_is_a_request_exception():
    # The design hinge: existing callers (import source lookups, worker item
    # guards) catch RequestException and skip — no call-site changes needed.
    assert issubclass(gc.GeniusRateLimitedError, requests.RequestException)


def test_success_decays_backoff(monkeypatch):
    _fresh(monkeypatch)
    monkeypatch.setattr(gc, '_rate_limit_backoff', 30)

    @gc.rate_limited
    def call():
        return 'ok'

    assert call() == 'ok'
    assert gc._rate_limit_backoff == 25
