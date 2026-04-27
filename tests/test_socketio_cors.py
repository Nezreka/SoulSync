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


@pytest.mark.parametrize("allowed, origin, host, scheme, expected_reject", [
    # Same-origin (Origin's full {scheme}://{host} matches request) — allow
    (None,                  'http://localhost:8888',   'localhost:8888',   'http',  False),
    (None,                  'http://192.168.1.5:8888', '192.168.1.5:8888', 'http',  False),
    (None,                  'https://soulsync.foo',    'soulsync.foo',     'https', False),

    # Cross-origin with default allow-list — reject
    (None,                  'https://x.com',           'localhost:8888',   'http',  True),
    (None,                  'https://soulsync.foo',    'localhost:8888',   'http',  True),  # reverse proxy NOT forwarding Host
    # Scheme mismatch — engineio rejects, so do we
    (None,                  'https://soulsync.foo',    'soulsync.foo',     'http',  True),

    # Wildcard short-circuit — allow
    ('*',                   'https://x.com',           'localhost:8888',   'http',  False),
    ('*',                   'https://anything.evil',   'localhost:8888',   'http',  False),

    # Origin in allow-list — allow
    (['https://x.com'],     'https://x.com',           'localhost:8888',   'http',  False),
    (['https://soulsync.foo'], 'https://soulsync.foo', 'localhost:8888',   'http',  False),

    # Cross-origin not in allow-list — reject
    (['https://x.com'],     'https://y.com',           'localhost:8888',   'http',  True),

    # Same-origin still works even when allow-list has other entries
    (['https://x.com'],     'http://localhost:8888',   'localhost:8888',   'http',  False),
])
def test_will_reject_predicts_engineio_decision(allowed, origin, host, scheme, expected_reject):
    assert will_reject(allowed, origin, host, request_scheme=scheme) is expected_reject


def test_will_reject_with_empty_host_only_uses_allowlist():
    """If the request somehow has no Host header (shouldn't happen but be
    safe), same-origin can't be checked — fall through to allow-list only."""
    assert will_reject(None, 'https://x.com', '', request_scheme='https') is True
    assert will_reject(['https://x.com'], 'https://x.com', '', request_scheme='https') is False
    assert will_reject('*', 'https://x.com', '', request_scheme='https') is False


def test_will_reject_honors_x_forwarded_host():
    """Engineio honors X-Forwarded-Host automatically when
    cors_allowed_origins is None (engineio/base_server.py:_cors_allowed_origins).
    Our predictor must mirror that — otherwise reverse-proxy users with
    proper proxy headers would trigger spurious "rejected" log lines."""
    # Same-origin via X-Forwarded-Host (typical TLS-terminating reverse proxy)
    assert will_reject(None, 'https://soulsync.foo', 'internal:8888',
                       request_scheme='http',
                       forwarded_host='soulsync.foo',
                       forwarded_proto='https') is False

    # X-Forwarded-Host with comma list (proxy chain) — first entry wins
    assert will_reject(None, 'https://soulsync.foo', 'internal:8888',
                       request_scheme='http',
                       forwarded_host='soulsync.foo, edge.proxy',
                       forwarded_proto='https') is False

    # X-Forwarded-Host doesn't match either — still reject
    assert will_reject(None, 'https://attacker.com', 'internal:8888',
                       request_scheme='http',
                       forwarded_host='soulsync.foo',
                       forwarded_proto='https') is True

    # X-Forwarded-Host empty — falls back to Host check (the unset case)
    assert will_reject(None, 'https://soulsync.foo', 'soulsync.foo',
                       request_scheme='https',
                       forwarded_host='') is False


def test_will_reject_compares_full_scheme_when_known():
    """When the caller provides scheme info, engineio compares full
    {scheme}://{host} strings. A TLS-terminating proxy can leave the
    backend seeing http while the browser's Origin is https — engineio
    rejects, our predictor must too (otherwise we miss logging it)."""
    # Backend sees http, browser sent https → engineio rejects → we predict reject
    assert will_reject(None, 'https://soulsync.foo', 'soulsync.foo',
                       request_scheme='http') is True

    # Backend sees http, browser sent http → match → allow
    assert will_reject(None, 'http://soulsync.foo', 'soulsync.foo',
                       request_scheme='http') is False

    # X-Forwarded-Proto says the public request was https → match origin's https
    assert will_reject(None, 'https://soulsync.foo', 'internal:8888',
                       request_scheme='http',
                       forwarded_host='soulsync.foo',
                       forwarded_proto='https') is False

    # X-Forwarded-Proto says https but Origin is http → mismatch → reject
    assert will_reject(None, 'http://soulsync.foo', 'internal:8888',
                       request_scheme='http',
                       forwarded_host='soulsync.foo',
                       forwarded_proto='https') is True

    # Comma-separated X-Forwarded-Proto (proxy chain) — first wins, like engineio
    assert will_reject(None, 'https://soulsync.foo', 'internal:8888',
                       request_scheme='http',
                       forwarded_host='soulsync.foo',
                       forwarded_proto='https, http') is False


def test_will_reject_allows_missing_origin_matching_engineio():
    """Engineio (server.py:207: ``if origin:``) skips CORS validation
    entirely when no Origin header is sent — non-browser clients (curl,
    server-to-server) are intentionally permitted. Our predictor must
    match that or we'd log spurious "rejected" warnings for legitimate
    non-browser traffic. Must also not raise on None input."""
    # Wildcard permits missing origin — and so does the default policy
    # (matches engineio's actual behavior).
    assert will_reject('*', None, 'localhost:8888') is False
    assert will_reject('*', '', 'localhost:8888') is False
    assert will_reject(None, None, 'localhost:8888') is False
    assert will_reject(None, '', 'localhost:8888') is False
    assert will_reject(['https://x.com'], None, 'localhost:8888') is False


def test_will_reject_honors_forwarded_proto_alone():
    """Engineio adds the forwarded candidate when EITHER X-Forwarded-Proto
    OR X-Forwarded-Host is present (it falls back to HTTP_HOST for the
    missing one). Our predictor must mirror that — otherwise a misconfig
    sending only X-Forwarded-Proto would look like a rejection in our
    log even though engineio actually allows it."""
    # forwarded_proto alone: backend host stands in for forwarded_host
    assert will_reject(None, 'https://localhost:8888', 'localhost:8888',
                       request_scheme='http',
                       forwarded_proto='https') is False

    # forwarded_proto alone but origin's host doesn't match the backend host
    assert will_reject(None, 'https://attacker.com', 'localhost:8888',
                       request_scheme='http',
                       forwarded_proto='https') is True


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
    # Same-origin via X-Forwarded-Host (with proxy scheme info) — no warning
    rl.maybe_log(None, 'https://soulsync.foo', 'internal:8888',
                 request_scheme='http',
                 forwarded_host='soulsync.foo',
                 forwarded_proto='https')

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


def test_rejection_logger_caps_dedup_set_at_configured_limit():
    """A hostile actor opening connections from many distinct fake origins
    would otherwise grow the dedup set unbounded. After the cap is hit,
    further rejections are silently dropped (after one overflow notice)."""
    log = _CapturingLogger()
    rl = RejectionLogger(log, dedup_cap=5)

    # Fill the cap
    for i in range(5):
        rl.maybe_log(None, f'https://fake{i}.com', 'localhost:8888')
    assert len(log.warnings) == 5

    # Next unique origin → overflow notice, NOT a per-origin warning
    rl.maybe_log(None, 'https://fake5.com', 'localhost:8888')
    assert len(log.warnings) == 6
    assert 'cap' in log.warnings[5].lower() or 'suppress' in log.warnings[5].lower()

    # Further unique origins → silently dropped (overflow notice already emitted)
    for i in range(6, 20):
        rl.maybe_log(None, f'https://fake{i}.com', 'localhost:8888')
    assert len(log.warnings) == 6  # unchanged

    # After reset, cap restarts
    rl.reset_for_tests()
    rl.maybe_log(None, 'https://fake0.com', 'localhost:8888')
    assert len(log.warnings) == 7


def test_rejection_logger_default_cap_is_reasonable():
    """The default cap should be high enough that legitimate-but-unusual
    setups (e.g., a power user with a dozen reverse-proxy domains rotating)
    don't hit the overflow notice during normal use."""
    assert RejectionLogger.DEFAULT_DEDUP_CAP >= 50, (
        "default dedup cap should fit normal usage"
    )


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
