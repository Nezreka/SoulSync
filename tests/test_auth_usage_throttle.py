"""Unit tests for the auth last_used_at write throttle.

Fix 1.2: every authenticated API request previously called
`config_mgr.set("api_keys", ...)`, which rewrites the entire app config
blob to SQLite. Writes are now throttled per key hash.
"""

import sys
import threading
import types
from datetime import datetime, timedelta, timezone

import pytest


# api/__init__.py eagerly imports flask_limiter. Tests only need the auth
# module, so stub flask_limiter before importing the api package.
def _install_flask_limiter_stub():
    if "flask_limiter" in sys.modules:
        return
    stub = types.ModuleType("flask_limiter")

    class _Limiter:
        def __init__(self, *args, **kwargs):
            pass

        def limit(self, *args, **kwargs):
            def decorator(target):
                return target
            return decorator

        def init_app(self, app):
            pass

    stub.Limiter = _Limiter
    sys.modules["flask_limiter"] = stub

    util_stub = types.ModuleType("flask_limiter.util")
    util_stub.get_remote_address = lambda: "127.0.0.1"
    sys.modules["flask_limiter.util"] = util_stub


_install_flask_limiter_stub()

from api import auth  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_usage_cache():
    """Ensure a clean throttle cache for each test."""
    with auth._usage_lock:
        auth._last_persisted_usage.clear()
    yield
    with auth._usage_lock:
        auth._last_persisted_usage.clear()


def test_first_call_persists():
    now = datetime.now(timezone.utc)
    assert auth._should_persist_usage("hash-a", now) is True


def test_second_call_within_interval_does_not_persist():
    start = datetime.now(timezone.utc)
    assert auth._should_persist_usage("hash-a", start) is True
    # 5 minutes later, still inside the 15-minute window
    assert auth._should_persist_usage("hash-a", start + timedelta(minutes=5)) is False


def test_call_after_interval_persists_again():
    start = datetime.now(timezone.utc)
    assert auth._should_persist_usage("hash-a", start) is True
    later = start + auth._USAGE_WRITE_INTERVAL
    assert auth._should_persist_usage("hash-a", later) is True


def test_different_keys_have_independent_throttles():
    now = datetime.now(timezone.utc)
    assert auth._should_persist_usage("hash-a", now) is True
    assert auth._should_persist_usage("hash-b", now) is True
    # Both keys should now be throttled for the next 15 minutes
    assert auth._should_persist_usage("hash-a", now + timedelta(minutes=1)) is False
    assert auth._should_persist_usage("hash-b", now + timedelta(minutes=1)) is False


def test_concurrent_access_is_thread_safe():
    """Many threads racing on the same key should only produce one persist per window."""
    now = datetime.now(timezone.utc)
    results: list[bool] = []
    results_lock = threading.Lock()

    def worker():
        decision = auth._should_persist_usage("hash-shared", now)
        with results_lock:
            results.append(decision)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Exactly one thread should have won the race and persisted.
    assert results.count(True) == 1
    assert results.count(False) == 19


def test_usage_interval_matches_spec():
    """The throttle window should be 15 minutes (documented contract)."""
    assert auth._USAGE_WRITE_INTERVAL == timedelta(minutes=15)
