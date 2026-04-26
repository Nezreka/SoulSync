"""Tests for `core.socketio_cors` — the resolver, rejection predictor,
and dedup logger that gate Socket.IO WebSocket origins.

These pin the security-relevant behavior:

- The resolver returns ``None`` (engineio's same-origin default — also
  the secure default) for anything other than an explicit allow-list or
  the wildcard. CRITICAL: the resolver must NEVER return ``[]`` — in
  engineio that means "disable CORS handling" which is identical to the
  ``'*'`` wildcard from a security standpoint (engineio/server.py:202:
  ``if cors_allowed_origins != []``). And it must never silently turn
  into ``'*'`` from a misshapen config value.
- The rejection predictor must mirror engineio's same-origin check
  exactly so the warning we log is accurate. This includes accepting
  matches against ``X-Forwarded-Host`` since engineio honors that
  automatically when ``cors_allowed_origins`` is ``None``.
- The dedup logger must emit each unique origin only once so a malicious
  site repeatedly hammering the WS endpoint can't spam logs.

Pure unit tests — no Flask, no engineio, no network. Just the logic.
"""

import logging
import threading
from typing import Any, List

import pytest

from core.socketio_cors import (
    RejectionLogger,
    log_startup_status,
    resolve_cors_origins,
    will_reject,
)


# ── helpers ───────────────────────────────────────────────────────────────


class _FakeConfig:
    """Minimal config_manager stub that returns one canned value for the
    `security.cors_origins` key. Anything else returns the default."""

    def __init__(self, value: Any):
        self._value = value

    def get(self, key: str, default: Any = None) -> Any:
        if key == 'security.cors_origins':
            return self._value
        return default


class _CapturingLogger:
    """Stand-in logger that records every warning/info call so tests can
    assert what was emitted (and how many times)."""

    def __init__(self):
        self.warnings: List[str] = []
        self.infos: List[str] = []

    def warning(self, msg: str) -> None:
        self.warnings.append(msg)

    def info(self, msg: str) -> None:
        self.infos.append(msg)


# ── resolve_cors_origins ──────────────────────────────────────────────────


@pytest.mark.parametrize("value, expected", [
    # Unset / empty / whitespace / bogus types → None (engineio same-origin default)
    (None, None),
    ('', None),
    ('   ', None),
    ('\n\n', None),
    (',,,', None),
    (12345, None),               # numeric — invalid type
    ({'a': 1}, None),            # dict — invalid type
    ([], None),                  # explicit empty list
    (['  ', ''], None),          # list of all-empty strings

    # Wildcard
    ('*', '*'),
    (' * ', '*'),
    (['*'], '*'),
    (['https://x.com', '*'], '*'),  # wildcard in a list still wins

    # Single origin
    ('https://x.com', ['https://x.com']),
    (['https://x.com'], ['https://x.com']),

    # Multiple origins, comma-separated
    ('https://x.com, http://y.com', ['https://x.com', 'http://y.com']),

    # Multiple origins, newline-separated (textarea input)
    ('https://x.com\nhttp://y.com', ['https://x.com', 'http://y.com']),

    # Mixed separators + extra commas / whitespace get cleaned
    ('https://x.com,, http://y.com,\n http://z.com', ['https://x.com', 'http://y.com', 'http://z.com']),

    # List with mixed types (bytes-like → str coerce)
    (['https://x.com', '  ', 'http://y.com'], ['https://x.com', 'http://y.com']),
])
def test_resolve_cors_origins_normalizes_input(value, expected):
    assert resolve_cors_origins(_FakeConfig(value)) == expected


def test_resolve_cors_origins_handles_missing_config_manager():
    """Defensive: if config_manager is None (e.g., very early init), the
    resolver must fall back to the secure default rather than crashing."""
    assert resolve_cors_origins(None) is None


def test_resolve_cors_origins_never_returns_empty_list():
    """SECURITY CRITICAL: ``cors_allowed_origins=[]`` in engineio means
    "disable CORS handling entirely" — identical security to ``'*'``
    (engineio/server.py:202). The resolver must return ``None`` for the
    secure default, never ``[]``, regardless of what the user typed."""
    edge_cases = [None, '', '   ', '\n\n', ',,,', 12345, 3.14, {'a': 1},
                  object(), True, False, [], ['  '], ['', '  '], ('   ',)]
    for value in edge_cases:
        result = resolve_cors_origins(_FakeConfig(value))
        assert result != [], (
            f"resolve_cors_origins({value!r}) returned [] — that disables "
            f"engineio's CORS check entirely, allowing all origins. Must be None."
        )


def test_resolve_cors_origins_never_silently_returns_wildcard_for_garbage():
    """Security-critical: a misshapen config value must NEVER turn into
    `'*'` by accident. Anything we can't parse falls back to same-origin."""
    for bogus in [12345, 3.14, {'a': 1}, object(), True, False]:
        assert resolve_cors_origins(_FakeConfig(bogus)) is None, (
            f"resolve_cors_origins({bogus!r}) returned a non-None value — "
            f"bogus inputs must default to same-origin only"
        )


# ── will_reject ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("allowed, origin, host, expected_reject", [
    # Same-origin (Origin's host:port matches the request Host) — allow
    (None,                            'http://localhost:8888',  'localhost:8888',  False),
    (None,                            'http://192.168.1.5:8888','192.168.1.5:8888',False),
    (None,                            'https://soulsync.foo',   'soulsync.foo',    False),

    # Cross-origin with default allow-list — reject
    (None,                            'https://x.com',          'localhost:8888',  True),
    (None,                            'https://soulsync.foo',   'localhost:8888',  True),  # reverse proxy NOT forwarding Host

    # Wildcard short-circuit — allow
    ('*',                             'https://x.com',          'localhost:8888',  False),
    ('*',                             'https://anything.evil',  'localhost:8888',  False),

    # Origin in allow-list — allow
    (['https://x.com'],               'https://x.com',          'localhost:8888',  False),
    (['https://soulsync.foo'],        'https://soulsync.foo',   'localhost:8888',  False),

    # Cross-origin not in allow-list — reject
    (['https://x.com'],               'https://y.com',          'localhost:8888',  True),

    # Same-origin still works even when allow-list has other entries
    (['https://x.com'],               'http://localhost:8888',  'localhost:8888',  False),

    # Origin with path component — only host:port should be compared
    (None,                            'http://x.com:8080/path', 'x.com:8080',      False),
])
def test_will_reject_predicts_engineio_decision(allowed, origin, host, expected_reject):
    assert will_reject(allowed, origin, host) is expected_reject


def test_will_reject_with_empty_host_only_uses_allowlist():
    """If the request somehow has no Host header (shouldn't happen but be
    safe), same-origin can't be checked — fall through to allow-list only."""
    assert will_reject(None, 'https://x.com', '') is True
    assert will_reject(['https://x.com'], 'https://x.com', '') is False
    assert will_reject('*', 'https://x.com', '') is False


def test_will_reject_honors_x_forwarded_host():
    """Engineio honors X-Forwarded-Host automatically when
    cors_allowed_origins is None (engineio/base_server.py:_cors_allowed_origins).
    Our predictor must mirror that — otherwise reverse-proxy users with
    proper proxy headers would trigger spurious "rejected" log lines."""
    # Same-origin via X-Forwarded-Host (typical reverse-proxy setup)
    assert will_reject(None, 'https://soulsync.foo', 'internal:8888',
                       forwarded_host='soulsync.foo') is False

    # X-Forwarded-Host with comma list (proxy chain) — first entry wins
    assert will_reject(None, 'https://soulsync.foo', 'internal:8888',
                       forwarded_host='soulsync.foo, edge.proxy') is False

    # X-Forwarded-Host doesn't match either — still reject
    assert will_reject(None, 'https://attacker.com', 'internal:8888',
                       forwarded_host='soulsync.foo') is True

    # X-Forwarded-Host empty — falls back to Host check (the unset case)
    assert will_reject(None, 'https://soulsync.foo', 'soulsync.foo',
                       forwarded_host='') is False


# ── RejectionLogger ───────────────────────────────────────────────────────


def test_rejection_logger_emits_once_per_unique_origin():
    log = _CapturingLogger()
    rl = RejectionLogger(log)

    # Same origin three times — only one warning
    for _ in range(3):
        rl.maybe_log(None, 'https://attacker.com', 'localhost:8888')
    assert len(log.warnings) == 1
    assert 'attacker.com' in log.warnings[0]

    # Different origin — separate warning
    rl.maybe_log(None, 'https://other.evil', 'localhost:8888')
    assert len(log.warnings) == 2
    assert 'other.evil' in log.warnings[1]


def test_rejection_logger_silent_when_request_would_be_allowed():
    log = _CapturingLogger()
    rl = RejectionLogger(log)

    # Same-origin — no warning
    rl.maybe_log(None, 'http://localhost:8888', 'localhost:8888')
    # Wildcard — no warning
    rl.maybe_log('*', 'https://x.com', 'localhost:8888')
    # In allow-list — no warning
    rl.maybe_log(['https://x.com'], 'https://x.com', 'localhost:8888')
    # Same-origin via X-Forwarded-Host — no warning
    rl.maybe_log(None, 'https://soulsync.foo', 'internal:8888', 'soulsync.foo')

    assert log.warnings == []


def test_rejection_logger_silent_when_no_origin_header():
    """Non-browser clients (curl, server-to-server) don't send Origin —
    they should not trigger the warning."""
    log = _CapturingLogger()
    rl = RejectionLogger(log)

    rl.maybe_log(None, None, 'localhost:8888')
    rl.maybe_log(None, '', 'localhost:8888')

    assert log.warnings == []


def test_rejection_logger_warning_message_points_user_to_settings():
    """The warning is the ONLY signal users get when their reverse proxy
    setup is broken. It must name the origin AND tell them where to fix it."""
    log = _CapturingLogger()
    rl = RejectionLogger(log)

    rl.maybe_log(None, 'https://soulsync.example.com', 'internal-host:8888')

    assert len(log.warnings) == 1
    msg = log.warnings[0]
    assert 'soulsync.example.com' in msg, "warning must include the rejected origin"
    assert 'internal-host:8888' in msg, "warning must include the request Host so users can debug proxy config"
    assert 'Settings' in msg, "warning must point users to Settings"
    assert 'Allowed' in msg, "warning must name the field they need to edit"


def test_rejection_logger_dedup_is_threadsafe():
    """Two threads racing on the same novel origin must result in exactly
    one warning, not two. Locks the dedup set internally."""
    log = _CapturingLogger()
    rl = RejectionLogger(log)
    barrier = threading.Barrier(8)

    def hammer():
        barrier.wait()
        for _ in range(50):
            rl.maybe_log(None, 'https://race.test', 'localhost:8888')

    threads = [threading.Thread(target=hammer) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(log.warnings) == 1


def test_rejection_logger_reset_for_tests_clears_dedup():
    log = _CapturingLogger()
    rl = RejectionLogger(log)

    rl.maybe_log(None, 'https://x.com', 'localhost:8888')
    assert len(log.warnings) == 1

    rl.reset_for_tests()
    rl.maybe_log(None, 'https://x.com', 'localhost:8888')
    assert len(log.warnings) == 2  # logged again after reset


# ── log_startup_status ────────────────────────────────────────────────────


def test_startup_status_warns_on_wildcard():
    """The wildcard is a security risk — startup must log a warning that
    points users to the settings page, not just an info line."""
    log = _CapturingLogger()
    log_startup_status('*', log)

    assert len(log.warnings) == 1
    assert "'*'" in log.warnings[0]
    assert 'Settings' in log.warnings[0]
    assert log.infos == []


def test_startup_status_info_logs_nonempty_allowlist():
    """Non-empty allow-list → info, so users can confirm their config
    actually took effect."""
    log = _CapturingLogger()
    log_startup_status(['https://x.com', 'https://y.com'], log)

    assert log.warnings == []
    assert len(log.infos) == 1
    assert 'https://x.com' in log.infos[0]


def test_startup_status_silent_on_default_same_origin():
    """None (default) → no log. Same-origin-only is the default;
    nothing noteworthy to announce on every startup."""
    log = _CapturingLogger()
    log_startup_status(None, log)

    assert log.warnings == []
    assert log.infos == []
