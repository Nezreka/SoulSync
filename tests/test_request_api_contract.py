"""The inbound request API's response contract (#1168).

Reported by Karesto: POST /api/v1/request answered 500 on every SUCCESS, and
the `completed` its own docstring promised was never reachable. Both defects
survived because nothing tested this endpoint's HTTP behaviour — the existing
suite only exercises the cleanup thread — and because the feature looks healthy
from the logs: the request really is queued and the download really does start.
Only the caller ever sees the failure.
"""

import sys
import time
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
        def __init__(self, state, id="dl-1"):
            self.state = state
            self.id = id

    def _req(self, request_id="r1", status="downloading", download_id="dl-1"):
        with request_mod._requests_lock:
            request_mod._pending_requests[request_id] = {
                "request_id": request_id,
                "query": "Daft Punk - Get Lucky",
                "status": status,
                "created_at": datetime.now(),
                "completed_at": None,
                "download_id": download_id,
                "error": None,
                "notify_url": None,
                "watch_until": time.monotonic() + 600,
            }

    def _sweep(self, transfers, request_id="r1"):
        """Run one sweep of the shared watcher against a set of transfers."""
        class _Soulseek:
            def get_all_downloads(self):
                return transfers

        settled = request_mod._resolve_downloading_requests(
            _Soulseek(), run_async=lambda coro: coro,
        )
        with request_mod._requests_lock:
            return dict(request_mod._pending_requests.get(request_id, {})), settled

    def test_a_finished_transfer_reaches_completed(self):
        self._req()
        req, settled = self._sweep([self._Status("Completed, Succeeded")])
        assert req["status"] == "completed"
        assert req["completed_at"] is not None
        assert len(settled) == 1

    def test_a_compound_failure_is_NOT_read_as_completed(self):
        # "Completed, Errored" contains "Completed". A substring check for that
        # would call a dead transfer a success, which is worse than the bug.
        self._req()
        req, _ = self._sweep([self._Status("Completed, Errored")])
        assert req["status"] == "failed"
        assert "Errored" in req["error"]

    def test_a_cancelled_transfer_fails_rather_than_completing(self):
        self._req()
        req, _ = self._sweep([self._Status("Completed, Cancelled")])
        assert req["status"] == "failed"

    def test_an_in_progress_transfer_is_left_alone(self):
        self._req()
        req, settled = self._sweep([self._Status("InProgress")])
        assert req["status"] == "downloading"
        assert settled == []

    def test_a_transfer_missing_from_the_sweep_is_left_alone(self):
        # slskd not listing it yet is not evidence of anything.
        self._req()
        req, _ = self._sweep([self._Status("Completed, Succeeded", id="someone-else")])
        assert req["status"] == "downloading"

    def test_it_settles_only_the_matching_request(self):
        self._req("r1", download_id="dl-1")
        self._req("r2", download_id="dl-2")
        self._sweep([self._Status("Completed, Succeeded", id="dl-1")])
        with request_mod._requests_lock:
            assert request_mod._pending_requests["r1"]["status"] == "completed"
            assert request_mod._pending_requests["r2"]["status"] == "downloading"

    def test_past_the_watch_window_it_stops_looking_without_claiming_failure(self):
        # The transfer may well still be running. Calling it failed because we
        # stopped watching would be a worse lie than saying we stopped.
        self._req()
        with request_mod._requests_lock:
            request_mod._pending_requests["r1"]["watch_until"] = time.monotonic() - 1
        req, settled = self._sweep([self._Status("InProgress")])
        assert req["status"] == "downloading"
        assert req.get("timed_out") is True
        assert settled == [], 'a timeout is not a result to call back about'

    def test_a_failed_sweep_settles_nothing(self):
        self._req()

        class _Soulseek:
            def get_all_downloads(self):
                raise RuntimeError("slskd down")

        settled = request_mod._resolve_downloading_requests(
            _Soulseek(), run_async=lambda coro: coro,
        )
        assert settled == []
        with request_mod._requests_lock:
            assert request_mod._pending_requests["r1"]["status"] == "downloading"


class TestTheSweepDoesNotFloodSoulseek:
    """A fix for #1168 is not allowed to cause #1166.

    The endpoint allows 60 requests a minute and each is watched for up to
    fifteen minutes. Polling per request would mean hundreds of live threads
    issuing hundreds of slskd calls a second — the exact search flooding the
    concurrency cap exists to prevent. One bulk read settles all of them.
    """

    def test_one_api_call_settles_every_watched_request(self):
        calls = []
        for i in range(50):
            with request_mod._requests_lock:
                request_mod._pending_requests[f"r{i}"] = {
                    "request_id": f"r{i}", "query": "q", "status": "downloading",
                    "created_at": datetime.now(), "completed_at": None,
                    "download_id": f"dl-{i}", "error": None, "notify_url": None,
                    "watch_until": time.monotonic() + 600,
                }

        class _Transfer:
            def __init__(self, i):
                self.id = f"dl-{i}"
                self.state = "Completed, Succeeded"

        class _Soulseek:
            def get_all_downloads(self):
                calls.append(1)
                return [_Transfer(i) for i in range(50)]

        settled = request_mod._resolve_downloading_requests(
            _Soulseek(), run_async=lambda coro: coro,
        )

        assert len(calls) == 1, f'50 requests cost {len(calls)} slskd calls, expected 1'
        assert len(settled) == 50

    def test_an_idle_sweep_costs_no_api_call_at_all(self):
        calls = []

        class _Soulseek:
            def get_all_downloads(self):
                calls.append(1)
                return []

        assert request_mod._resolve_downloading_requests(
            _Soulseek(), run_async=lambda coro: coro,
        ) == []
        assert calls == [], 'nothing is downloading; slskd should not be asked'

class TestTheWatcherIsActuallyWired:
    """The sweep working is not the same as anything calling it.

    Worth its own class: an earlier version of these tests drove the watcher
    directly, so every one of them passed with the call removed from the worker
    — a fix that ships and does nothing.
    """

    def _app(self, result):
        class _Soulseek:
            def search_and_download_best(self, query):
                return result

        class _App:
            soulsync = {"download_orchestrator": _Soulseek()}

            def _get_current_object(self):
                return self

        return _App()

    def _run(self, monkeypatch, result, request_id="r9"):
        monkeypatch.setattr(request_mod, "current_app", self._app(result))
        monkeypatch.setitem(
            sys.modules, "utils.async_helpers",
            types.SimpleNamespace(run_async=lambda coro: coro),
        )
        with request_mod._requests_lock:
            request_mod._pending_requests[request_id] = {
                "request_id": request_id, "query": "q", "status": "queued",
                "created_at": datetime.now(), "completed_at": None,
                "download_id": None, "error": None, "notify_url": None,
            }
        request_mod._run_search_and_download(request_id, "q", notify_url=None)
        with request_mod._requests_lock:
            return dict(request_mod._pending_requests[request_id])

    def test_the_worker_hands_the_transfer_to_the_watcher(self, monkeypatch):
        # download_id AND watch_until: without the deadline the sweep would
        # never time it out, and without the id it cannot be matched at all.
        req = self._run(monkeypatch, result="dl-77")
        assert req["status"] == "downloading"
        assert req["download_id"] == "dl-77"
        assert req.get("watch_until"), 'nothing tells the sweep when to give up'

    def test_the_worker_does_NOT_sit_on_the_transfer(self, monkeypatch):
        # It used to poll here, holding a thread for up to fifteen minutes per
        # request against a 60/minute endpoint. It must return immediately.
        import inspect
        src = inspect.getsource(request_mod._run_search_and_download)
        assert "_resolve_downloading_requests" not in src
        assert "while" not in src, 'the worker is polling again'

    def test_create_request_starts_the_shared_watcher(self):
        # Nothing else does; without this the sweep never runs and `completed`
        # is unreachable all over again.
        #
        # Matched as a CALL, not a substring: `_ensure_watcher(app)` also occurs
        # in `def _ensure_watcher(app):`, so a plain `in src` passes with the
        # call deleted — which is how the first version of this test behaved.
        import inspect
        import re
        src = inspect.getsource(request_mod)
        calls = re.findall(r"^\s+_ensure_watcher\(app\)\s*$", src, re.MULTILINE)
        assert calls, "nothing calls _ensure_watcher — the sweep would never run"

    def test_a_no_match_is_terminal_immediately(self, monkeypatch):
        req = self._run(monkeypatch, result=None, request_id="r8")
        assert req["status"] == "not_found"
        assert req["completed_at"] is not None
        assert "watch_until" not in req, 'nothing to watch — there is no transfer'


class TestCallbacksFireOnTerminalOnly:
    def test_a_settled_request_notifies_with_its_terminal_status(self):
        posted = []
        with request_mod._requests_lock:
            request_mod._pending_requests["rn"] = {
                "request_id": "rn", "query": "q", "status": "completed",
                "created_at": datetime.now(), "completed_at": "now",
                "download_id": "dl-1", "error": None,
                "notify_url": "http://example.invalid/hook",
            }
            req = dict(request_mod._pending_requests["rn"])

        import api.request as mod
        original = mod.http_requests.post
        mod.http_requests.post = lambda url, json=None, timeout=None: posted.append((url, json))
        try:
            mod._notify(req)
        finally:
            mod.http_requests.post = original

        assert len(posted) == 1
        url, payload = posted[0]
        assert url == "http://example.invalid/hook"
        assert payload["status"] == "completed"
        # created_at is a datetime and would not serialise; notify_url is the
        # caller's own secret coming back at them.
        assert "created_at" not in payload
        assert "notify_url" not in payload

    def test_no_callback_when_none_was_asked_for(self):
        posted = []
        import api.request as mod
        original = mod.http_requests.post
        mod.http_requests.post = lambda *a, **k: posted.append(1)
        try:
            mod._notify({"request_id": "x", "status": "completed"})
        finally:
            mod.http_requests.post = original
        assert posted == []


class TestASlowCallbackCannotStallTheSweep:
    """A callback is an HTTP POST to somebody else's server.

    Notifying settled requests in line would let one unreachable notify_url
    hold the sweep — at a ten second timeout each, fifty of them is eight
    minutes during which nobody else's status advances. The pool is bounded, so
    this cannot become the thread-per-request problem the shared sweep exists
    to avoid.
    """

    def test_notifications_do_not_run_on_the_calling_thread(self):
        import threading as _t
        seen = []
        caller = _t.current_thread().name

        import api.request as mod
        original = mod.http_requests.post

        def _slow(url, json=None, timeout=None):
            seen.append(_t.current_thread().name)

        mod.http_requests.post = _slow
        try:
            mod._notify_async({"request_id": "x", "status": "completed",
                               "notify_url": "http://example.invalid/hook"})
            # give the pool a moment to pick it up
            for _ in range(200):
                if seen:
                    break
                time.sleep(0.01)
        finally:
            mod.http_requests.post = original

        assert seen, "the callback never ran"
        assert seen[0] != caller, "the callback ran on the sweep thread"

    def test_the_pool_is_bounded(self):
        import api.request as mod
        mod._notify_async({"request_id": "x", "status": "completed",
                           "notify_url": "http://example.invalid/hook"})
        assert mod._notify_pool is not None
        assert mod._notify_pool._max_workers <= 8, "an unbounded pool is the bug again"

    def test_the_SWEEP_dispatches_asynchronously(self):
        # The one that matters. Every test above calls _notify_async directly,
        # so all of them pass with the sweep swapped back to the blocking
        # _notify — the helper being right is not the same as the sweep using
        # it. (Third time this shape of gap has appeared today.)
        import inspect
        import re
        src = inspect.getsource(request_mod._watcher_loop)
        assert re.search(r"_notify_async\(", src), 'the sweep is not dispatching async'
        assert not re.search(r"(?<!_async)\b_notify\(", src), 'the sweep calls the blocking _notify'

    def test_no_pool_work_when_no_callback_was_asked_for(self):
        import api.request as mod
        before = mod._notify_pool
        mod._notify_async({"request_id": "x", "status": "completed"})
        assert mod._notify_pool is before, "a request with no notify_url built a pool"


class TestWatcherLifecycleIsIndependent:
    def test_the_watcher_has_its_own_stop_event(self):
        # Sharing the cleanup thread's event meant stopping cleanup killed the
        # watcher — and since _ensure_watcher only restarts a DEAD thread onto
        # an event that is still set, it would exit again at once and never
        # sweep. Two threads, two lifecycles, two events.
        assert request_mod._watcher_stop_event is not request_mod._cleanup_stop_event

    def test_stopping_cleanup_does_not_stop_the_watcher(self):
        request_mod._cleanup_stop_event.set()
        try:
            assert not request_mod._watcher_stop_event.is_set()
        finally:
            request_mod._cleanup_stop_event.clear()


class TestTheWatchDeadlineMustExist:
    """A missing deadline is not an expired one.

    `req.get('watch_until', 0)` made `now >= 0` always true, so a request marked
    `downloading` without a deadline timed out on its first sweep. Nothing does
    that today — the handoff sets both — but the symptom of getting it wrong is
    a request that never reaches a terminal state, which is indistinguishable
    from the bug this endpoint was reported for.
    """

    def test_a_downloading_request_with_no_deadline_is_not_timed_out(self):
        with request_mod._requests_lock:
            request_mod._pending_requests["nd"] = {
                "request_id": "nd", "query": "q", "status": "downloading",
                "created_at": datetime.now(), "completed_at": None,
                "download_id": "dl-nd", "error": None, "notify_url": None,
                # watch_until deliberately absent
            }

        class _Soulseek:
            def get_all_downloads(self):
                return []

        request_mod._resolve_downloading_requests(_Soulseek(), run_async=lambda c: c)

        with request_mod._requests_lock:
            req = request_mod._pending_requests["nd"]
        assert req["status"] == "downloading"
        assert "timed_out" not in req, "a missing deadline was treated as an expired one"

    def test_a_real_deadline_still_expires(self):
        with request_mod._requests_lock:
            request_mod._pending_requests["ex"] = {
                "request_id": "ex", "query": "q", "status": "downloading",
                "created_at": datetime.now(), "completed_at": None,
                "download_id": "dl-ex", "error": None, "notify_url": None,
                "watch_until": time.monotonic() - 1,
            }

        class _Soulseek:
            def get_all_downloads(self):
                return []

        request_mod._resolve_downloading_requests(_Soulseek(), run_async=lambda c: c)

        with request_mod._requests_lock:
            assert request_mod._pending_requests["ex"].get("timed_out") is True
