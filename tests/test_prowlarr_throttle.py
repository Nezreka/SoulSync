"""Pin core.prowlarr_throttle — the ONE process-wide Prowlarr search budget.

Prowlarr hands every search straight to your indexers and shields them from
nothing, and this app had NO pacing on it at all, on either side. The video
wishlist drain is the loud one: three items at a time, each fanning out two or
three search strategies concurrently, and the next item starts the moment one
finishes. Boulder, Aug 2026: "it will search one item on torrent, less than 5
seconds later is doing a different search", with indexers getting upset.

Everything else in the app is paced (thirteen metadata services with a
MIN_API_INTERVAL each, slskd via core.slskd_throttle), so this closes the last
unthrottled third-party caller. SoulSync replaces the arr stack, and the arrs
are well behaved with indexers, so this is table stakes rather than polish.

Covers the reservation math, the concurrency property that the reservation model
exists for, the 429 cooldown, the interactive refusal, and that the search path
actually calls it.
"""

from __future__ import annotations

import ast
import threading
import time

import pytest

import core.prowlarr_throttle as th


@pytest.fixture(autouse=True)
def _fresh_budget(monkeypatch):
    # Fixed knobs: these must not depend on whatever is in the user's config.
    monkeypatch.setattr(th, "_settings", lambda: (2.0, 20))
    th._reset_for_tests()
    yield
    th._reset_for_tests()


class TestReservationMath:
    def test_min_gap_spaces_consecutive_searches(self):
        first = th.reserve_search_slot()
        second = th.reserve_search_slot()
        assert second - first == pytest.approx(2.0, abs=0.05)

    def test_the_window_cap_holds_a_burst(self, monkeypatch):
        monkeypatch.setattr(th, "_settings", lambda: (0.0, 3))
        times = [th.reserve_search_slot() for _ in range(4)]
        # first three are free, the fourth waits out the window
        assert times[1] - times[0] == pytest.approx(0.0, abs=0.05)
        assert times[3] - times[0] == pytest.approx(th.WINDOW_SECONDS, abs=0.05)

    def test_zero_knobs_disable_the_budget(self, monkeypatch):
        monkeypatch.setattr(th, "_settings", lambda: (0.0, 0))
        times = [th.reserve_search_slot() for _ in range(50)]
        assert times[-1] - times[0] == pytest.approx(0.0, abs=0.05)

    def test_a_config_that_cannot_be_read_falls_back_to_defaults(self, monkeypatch):
        import core.settings as cs

        def _boom(*a, **k):
            raise RuntimeError("config is on fire")

        monkeypatch.setattr(cs.config_manager, "get", _boom)
        # the real _settings, not the fixture's stub
        monkeypatch.undo()
        th._reset_for_tests()
        assert th._settings() == (th.DEFAULT_MIN_GAP_SECONDS, th.DEFAULT_MAX_PER_WINDOW)


class TestConcurrency:
    def test_threads_arriving_together_get_different_slots(self):
        """The whole reason this reserves a time instead of checking the clock.

        A plain "has it been 2s since the last call" check lets every thread
        that arrives in the same instant read the same answer and fire at once,
        which is exactly the shape this subsystem runs in: a pool of wishlist
        items, each fanning out its strategies.
        """
        got: list = []
        lock = threading.Lock()

        def _grab():
            at = th.reserve_search_slot()
            with lock:
                got.append(at)

        threads = [threading.Thread(target=_grab) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(set(got)) == 8, "two callers reserved the same instant"
        ordered = sorted(got)
        for earlier, later in zip(ordered, ordered[1:]):
            assert later - earlier == pytest.approx(2.0, abs=0.05)


class TestBackoff:
    def test_a_429_pushes_every_caller_back(self):
        th.note_rate_limited(45)
        at = th.reserve_search_slot()
        assert at - time.monotonic() == pytest.approx(45.0, abs=0.5)

    def test_the_cooldown_is_clamped_at_both_ends(self):
        th.note_rate_limited(0.5)
        assert th.reserve_search_slot() - time.monotonic() == pytest.approx(5.0, abs=0.5)
        th._reset_for_tests()
        th.note_rate_limited(99999)
        assert th.reserve_search_slot() - time.monotonic() == pytest.approx(300.0, abs=0.5)

    def test_garbage_retry_after_still_backs_off(self):
        th.note_rate_limited("soon-ish")
        assert th.reserve_search_slot() - time.monotonic() == pytest.approx(30.0, abs=0.5)

    def test_a_second_429_cannot_shorten_the_first(self):
        th.note_rate_limited(120)
        th.note_rate_limited(5)
        assert th.reserve_search_slot() - time.monotonic() > 60


class TestInteractiveCallers:
    def test_a_long_wait_is_refused_rather_than_blocking(self, monkeypatch):
        monkeypatch.setattr(th, "_settings", lambda: (60.0, 20))
        assert th.reserve_search_slot() is not None
        assert th.reserve_search_slot(max_wait_seconds=5.0) is None

    def test_a_refused_caller_does_not_consume_a_slot(self, monkeypatch):
        monkeypatch.setattr(th, "_settings", lambda: (60.0, 20))
        th.reserve_search_slot()
        before = th.status()["searches_in_window"]
        th.reserve_search_slot(max_wait_seconds=1.0)
        assert th.status()["searches_in_window"] == before

    def test_wait_for_slot_reports_the_refusal(self, monkeypatch):
        monkeypatch.setattr(th, "_settings", lambda: (60.0, 20))
        assert th.wait_for_slot() is True
        assert th.wait_for_slot(max_wait_seconds=1.0) is False


class TestStatus:
    def test_status_reports_the_budget(self):
        th.reserve_search_slot()
        s = th.status()
        assert s["searches_in_window"] == 1
        assert s["max_searches_per_window"] == 20
        assert s["min_gap_seconds"] == 2.0
        assert s["cooldown_remaining"] == 0.0


class TestWiring:
    """The call site, not the helper.

    Every Prowlarr search in the app funnels through `_search_sync`: the async
    `search`, the per-indexer fan-out that calls it, and the video side calling
    it directly. A throttle nothing calls is the shape that has shipped here
    before, so this walks the AST rather than grepping for a name that also
    matches its own import line.
    """

    @staticmethod
    def _fn(name):
        with open("core/prowlarr_client.py", encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                return node
        return None

    def test_search_sync_waits_for_a_slot(self):
        node = self._fn("_search_sync")
        assert node is not None, "_search_sync is gone"
        called = {
            n.func.id for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert "wait_for_slot" in called

    def test_the_async_search_still_goes_through_search_sync(self):
        """If it ever stops delegating, the throttle silently covers half the app."""
        node = self._fn("search")
        assert node is not None
        names = {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
        assert "_search_sync" in names

    def test_a_429_is_reported_to_the_shared_budget(self):
        node = self._fn("_api_get")
        assert node is not None
        called = {
            n.func.id for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert "note_rate_limited" in called


class TestTheFanOutIsOneWave:
    """A per-indexer fan-out (#1151) sends each indexer ONE query.

    The budget exists to protect an indexer from being hit too often, so a wave
    that touches each of them once costs one slot. Billing per HTTP request
    instead would charge a single album search a slot per indexer and add twenty
    seconds to a search somebody is waiting on.
    """

    def test_search_each_indexer_reserves_once_not_per_indexer(self):
        node = TestWiring._fn("search_each_indexer")
        assert node is not None
        # It is handed to run_blocking rather than called directly (the
        # reservation sleeps and this is an async function), so look for the
        # NAME being referenced, not for a direct call.
        names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        assert "wait_for_slot" in names, "the wave must reserve"

    def test_its_children_do_not_reserve_again(self):
        node = TestWiring._fn("search_each_indexer")
        # the inner self.search(...) call must pass throttle=False
        opted_out = [
            kw for call in ast.walk(node) if isinstance(call, ast.Call)
            for kw in call.keywords
            if kw.arg == "throttle" and isinstance(kw.value, ast.Constant)
            and kw.value.value is False
        ]
        assert opted_out, "the per-indexer calls must opt out of the budget"

    def test_search_sync_honours_the_opt_out(self, monkeypatch):
        monkeypatch.setattr(th, "_settings", lambda: (60.0, 20))
        th._reset_for_tests()
        calls = []
        monkeypatch.setattr(th, "wait_for_slot", lambda **k: calls.append(1) or True)

        from core.prowlarr_client import ProwlarrClient
        client = ProwlarrClient.__new__(ProwlarrClient)
        monkeypatch.setattr(client, "_api_get", lambda *a, **k: [], raising=False)

        client._search_sync("q", [], [], 10, throttle=False)
        assert calls == [], "throttle=False must not reserve"


class TestInteractiveSearchesAreBounded:
    """A person waiting on a manual search must not queue behind the drain.

    The wishlist drain is happy to wait its turn. A manual search holding a
    request worker for a minute while the window empties is a hung page.
    """

    def test_a_refused_slot_raises_rather_than_searching_anyway(self, monkeypatch):
        from core.prowlarr_client import ProwlarrClient, ProwlarrSearchError

        monkeypatch.setattr(th, "wait_for_slot", lambda **k: False)
        client = ProwlarrClient.__new__(ProwlarrClient)
        called = []
        monkeypatch.setattr(client, "_api_get",
                            lambda *a, **k: called.append(1) or [], raising=False)

        with pytest.raises(ProwlarrSearchError):
            client._search_sync("q", [], [], 10, max_wait_seconds=1.0)
        assert called == [], "a refused search must not hit prowlarr anyway"

    def test_the_manual_video_endpoints_pass_a_bound(self):
        """Every manual search path bounds its wait on the shared budget.

        This used to count two bounded call sites, because the two endpoints
        each called prowlarr_search themselves. They were consolidated into one
        helper, so the count went to 1 and the test failed on a refactor that
        did not lose the bound. What actually matters is that NO call is
        unbounded and that both endpoints still reach a bounded one.
        """
        with open("api/video/downloads.py", encoding="utf-8") as f:
            tree = ast.parse(f.read())

        calls = [
            call for call in ast.walk(tree)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name) and call.func.id == "prowlarr_search"
        ]
        assert calls, "the manual search lane no longer calls prowlarr_search"
        unbounded = [
            call.lineno for call in calls
            if not any(kw.arg == "max_wait_seconds" for kw in call.keywords)
        ]
        assert not unbounded, (
            f"unbounded manual prowlarr_search at line(s) {unbounded} — a person "
            "is waiting on this response, it must not sit in the shared budget"
        )

        # And both manual endpoints still go through the bounded helper rather
        # than growing their own unbounded lane somewhere else.
        helper = next(
            (node for node in ast.walk(tree)
             if isinstance(node, ast.FunctionDef) and node.name == "_torrent_lane_hits"),
            None,
        )
        assert helper is not None, "the bounded manual search helper is gone"
        assert any(
            isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
            and call.func.id == "prowlarr_search"
            for call in ast.walk(helper)
        ), "_torrent_lane_hits no longer performs the search"
        callers = [
            call for call in ast.walk(tree)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
            and call.func.id == "_torrent_lane_hits"
        ]
        assert len(callers) >= 2, (
            f"expected both manual endpoints to use the bounded helper, found "
            f"{len(callers)} caller(s)"
        )

    def test_the_background_drain_does_not_bound_it(self):
        """It should queue, not give up — dropping a wishlist search loses work."""
        with open("core/automation/handlers/video_process_wishlist.py", encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for call in ast.walk(tree):
            if (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                    and call.func.id == "prowlarr_search"):
                assert not any(kw.arg == "max_wait_seconds" for kw in call.keywords)
