"""The inbound request API's response contract (#1168).

Reported by Karesto: POST /api/v1/request answered 500 on every SUCCESS, and
the `completed` its own docstring promised was never reachable. Both defects
survived because nothing tested this endpoint's HTTP behaviour — the existing
suite only exercises the cleanup thread — and because the feature looks healthy
from the logs: the request really is queued and the download really does start.
Only the caller ever sees the failure.
"""

import sys
import types
from datetime import datetime, timedelta

import pytest


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
from api.helpers import api_success  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_state():
    with request_mod._requests_lock:
        request_mod._pending_requests.clear()
    request_mod._cleanup_stop_event.clear()
    yield
    request_mod._cleanup_stop_event.set()
    with request_mod._requests_lock:
        request_mod._pending_requests.clear()
    request_mod._cleanup_stop_event.clear()


class TestSuccessResponseShape:
    """Root cause 1: `api_success(...), 202` built a nested tuple."""

    def test_api_success_already_returns_a_status(self):
        # The fact the fix rests on. Appending another status to this makes
        # ((response, 200), 202), which Flask rejects with a TypeError it
        # surfaces as a 500.
        from flask import Flask

        with Flask(__name__).app_context():
            result = api_success({"ok": True}, status=202)
        assert isinstance(result, tuple)
        assert len(result) == 2, 'api_success returns (response, status)'
        assert result[1] == 202

    def test_the_endpoint_does_not_double_wrap(self):
        # Asserted against the source, because reproducing it needs a full app
        # + auth + a download source, and the defect is one character of shape.
        import inspect
        src = inspect.getsource(request_mod)
        assert "}), 202" not in src, "api_success(...), 202 builds a nested tuple Flask refuses"
        assert "}, status=202)" in src


class TestTerminalStatus:
    """Root cause 2: `completed` was documented and never assigned."""

    class _Status:
        def __init__(self, state):
            self.state = state

    def _req(self, request_id="r1", status="downloading"):
        with request_mod._requests_lock:
            request_mod._pending_requests[request_id] = {
                "request_id": request_id,
                "query": "Daft Punk - Get Lucky",
                "status": status,
                "created_at": datetime.now(),
                "completed_at": None,
                "download_id": "dl-1",
                "error": None,
            }

    def _watch(self, states, request_id="r1"):
        """Run the watcher against a scripted sequence of transfer states."""
        seq = list(states)

        class _Soulseek:
            def get_download_status(self, download_id):
                return seq.pop(0) if seq else None

        # The watcher waits on the shutdown event between polls; make that
        # instant rather than sleeping 5s per poll.
        request_mod._COMPLETION_POLL_SECONDS = 0.001
        request_mod._await_download_completion(
            request_id, _Soulseek(), "dl-1", run_async=lambda coro: coro,
        )
        with request_mod._requests_lock:
            return dict(request_mod._pending_requests.get(request_id, {}))

    def test_a_finished_transfer_reaches_completed(self):
        self._req()
        req = self._watch([self._Status("InProgress"), self._Status("Completed, Succeeded")])
        assert req["status"] == "completed"
        assert req["completed_at"] is not None

    def test_a_compound_failure_is_NOT_read_as_completed(self):
        # "Completed, Errored" contains "Completed". A check for that substring
        # would call a dead transfer a success, which is worse than the bug.
        self._req()
        req = self._watch([self._Status("Completed, Errored")])
        assert req["status"] == "failed"
        assert "Errored" in req["error"]

    def test_a_cancelled_transfer_fails_rather_than_completing(self):
        self._req()
        req = self._watch([self._Status("Completed, Cancelled")])
        assert req["status"] == "failed"

    def test_it_keeps_waiting_through_pending_states(self):
        self._req()
        req = self._watch([
            self._Status("Queued"),
            self._Status("InProgress"),
            self._Status("Completed, Succeeded"),
        ])
        assert req["status"] == "completed"

    def test_a_timeout_leaves_downloading_rather_than_claiming_failure(self):
        # The transfer may well still be running. Calling it failed because we
        # stopped watching would be a worse lie than saying we stopped.
        self._req()
        request_mod._COMPLETION_TIMEOUT_SECONDS = 0.01
        try:
            req = self._watch([self._Status("InProgress")] * 50)
        finally:
            request_mod._COMPLETION_TIMEOUT_SECONDS = 15 * 60
        assert req["status"] == "downloading"
        assert req.get("timed_out") is True

    def test_it_stops_if_the_record_is_cleaned_up_under_it(self):
        # The 1-hour TTL can evict a record mid-watch; the watcher must not
        # resurrect it as a stray dict.
        self._req()
        with request_mod._requests_lock:
            request_mod._pending_requests.clear()
        req = self._watch([self._Status("Completed, Succeeded")])
        assert req == {}

    def test_a_poll_error_does_not_kill_the_watch(self):
        self._req()
        seq = [RuntimeError("slskd down"), self._Status("Completed, Succeeded")]

        class _Soulseek:
            def get_download_status(self, download_id):
                nxt = seq.pop(0)
                if isinstance(nxt, Exception):
                    raise nxt
                return nxt

        request_mod._COMPLETION_POLL_SECONDS = 0.001
        request_mod._await_download_completion(
            "r1", _Soulseek(), "dl-1", run_async=lambda coro: coro,
        )
        with request_mod._requests_lock:
            assert request_mod._pending_requests["r1"]["status"] == "completed"


class TestTheWatcherIsActuallyWired:
    """The watcher working is not the same as the worker calling it.

    Every test above drives `_await_download_completion` directly, so they all
    pass even with the call removed from the worker — which is exactly how a fix
    ships and does nothing. (A selector that matched no elements got through
    this way earlier in the same codebase.)
    """

    def test_the_worker_waits_for_a_terminal_state(self, monkeypatch):
        watched = []

        class _Soulseek:
            def search_and_download_best(self, query):
                return "dl-77"

        class _App:
            soulsync = {"download_orchestrator": _Soulseek()}

            def _get_current_object(self):
                return self

        monkeypatch.setattr(request_mod, "current_app", _App())
        monkeypatch.setattr(
            request_mod, "_await_download_completion",
            lambda rid, sk, dl, ra: watched.append((rid, dl)),
        )
        monkeypatch.setitem(
            sys.modules, "utils.async_helpers",
            types.SimpleNamespace(run_async=lambda coro: coro),
        )

        with request_mod._requests_lock:
            request_mod._pending_requests["r9"] = {
                "request_id": "r9", "query": "q", "status": "queued",
                "created_at": datetime.now(), "completed_at": None,
                "download_id": None, "error": None,
            }

        request_mod._run_search_and_download("r9", "q", notify_url=None)

        assert watched == [("r9", "dl-77")], 'the worker never waited for the transfer'

    def test_nothing_is_watched_when_no_match_was_found(self, monkeypatch):
        # not_found is already terminal; there is no transfer to watch.
        watched = []

        class _Soulseek:
            def search_and_download_best(self, query):
                return None

        class _App:
            soulsync = {"download_orchestrator": _Soulseek()}

            def _get_current_object(self):
                return self

        monkeypatch.setattr(request_mod, "current_app", _App())
        monkeypatch.setattr(
            request_mod, "_await_download_completion",
            lambda rid, sk, dl, ra: watched.append(rid),
        )
        monkeypatch.setitem(
            sys.modules, "utils.async_helpers",
            types.SimpleNamespace(run_async=lambda coro: coro),
        )

        with request_mod._requests_lock:
            request_mod._pending_requests["r8"] = {
                "request_id": "r8", "query": "q", "status": "queued",
                "created_at": datetime.now(), "completed_at": None,
                "download_id": None, "error": None,
            }

        request_mod._run_search_and_download("r8", "q", notify_url=None)

        assert watched == []
        with request_mod._requests_lock:
            assert request_mod._pending_requests["r8"]["status"] == "not_found"
