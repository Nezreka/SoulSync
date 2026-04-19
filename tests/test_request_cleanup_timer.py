"""Tests for api/request.py periodic cleanup timer (fix 4.2).

Before this fix, `_cleanup_old_requests()` was only invoked on
`create_request`. During idle periods stale entries lingered for the
full uptime of the server. A background timer now runs every
_CLEANUP_INTERVAL_SECONDS and evicts anything older than _MAX_REQUEST_AGE.
"""

import sys
import threading
import time
import types
from datetime import datetime, timedelta

import pytest


# api/__init__.py imports flask_limiter at module load. Stub it.
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

from api import request as request_mod  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_state():
    # Ensure no leftover thread from a previous test.
    request_mod.stop_cleanup_thread(timeout=1.0)
    with request_mod._requests_lock:
        request_mod._pending_requests.clear()
    yield
    request_mod.stop_cleanup_thread(timeout=1.0)
    with request_mod._requests_lock:
        request_mod._pending_requests.clear()


def _add_request(request_id, age_minutes):
    with request_mod._requests_lock:
        request_mod._pending_requests[request_id] = {
            "request_id": request_id,
            "query": "q",
            "status": "queued",
            "created_at": datetime.now() - timedelta(minutes=age_minutes),
            "completed_at": None,
            "download_id": None,
            "error": None,
        }


class TestCleanupOldRequests:
    def test_evicts_entries_older_than_ttl(self):
        _add_request("old-1", age_minutes=120)  # > 60 min TTL
        _add_request("old-2", age_minutes=61)
        _add_request("fresh", age_minutes=5)

        removed = request_mod._cleanup_old_requests()

        assert removed == 2
        with request_mod._requests_lock:
            ids = set(request_mod._pending_requests.keys())
        assert ids == {"fresh"}

    def test_returns_zero_when_nothing_to_evict(self):
        _add_request("fresh", age_minutes=5)
        assert request_mod._cleanup_old_requests() == 0

    def test_empty_map_is_safe(self):
        assert request_mod._cleanup_old_requests() == 0


class TestCleanupThreadLifecycle:
    def test_start_returns_true_first_time_false_after(self):
        assert request_mod.start_cleanup_thread() is True
        # Second call in the same process should not start a new thread.
        assert request_mod.start_cleanup_thread() is False

    def test_stop_joins_thread(self):
        request_mod.start_cleanup_thread()
        assert request_mod._cleanup_thread is not None
        assert request_mod._cleanup_thread.is_alive()

        request_mod.stop_cleanup_thread(timeout=2.0)
        assert request_mod._cleanup_thread is None

    def test_thread_evicts_on_wakeup(self, monkeypatch):
        # Force a tiny interval so the test doesn't wait 5 minutes.
        monkeypatch.setattr(request_mod, "_CLEANUP_INTERVAL_SECONDS", 0.05)

        _add_request("old", age_minutes=120)
        request_mod.start_cleanup_thread()

        # Give the loop time to wake up and run at least once.
        deadline = time.time() + 2.0
        while time.time() < deadline:
            with request_mod._requests_lock:
                remaining = set(request_mod._pending_requests.keys())
            if "old" not in remaining:
                break
            time.sleep(0.05)

        with request_mod._requests_lock:
            assert "old" not in request_mod._pending_requests

    def test_stop_signals_thread_to_exit_promptly(self, monkeypatch):
        # With a huge interval, stop must still make the thread exit via the
        # stop event, not wait for the next timeout.
        monkeypatch.setattr(request_mod, "_CLEANUP_INTERVAL_SECONDS", 30.0)
        request_mod.start_cleanup_thread()
        thread = request_mod._cleanup_thread
        assert thread is not None

        request_mod.stop_cleanup_thread(timeout=2.0)
        assert not thread.is_alive()
