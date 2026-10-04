"""Fix 10: overlap guards for the airing / refresh / reenrich video actions."""
import time

from core.automation.handlers.video_run_guard import VideoRunGuard


def test_guard_not_busy_when_idle():
    g = VideoRunGuard()
    assert g() is False


def test_guard_busy_while_running():
    g = VideoRunGuard()
    with g:
        assert g() is True
    assert g() is False


def test_guard_allows_retry_after_stuck_timeout():
    g = VideoRunGuard(timeout_seconds=10)
    with g:
        # simulate a crash 11s ago by backdating
        g._started_at = time.time() - 11
        assert g() is False


def test_guard_clears_on_exception():
    g = VideoRunGuard()
    try:
        with g:
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert g() is False


def test_refresh_guard_wired():
    from core.automation.handlers.video_refresh_airing_schedules import (
        is_refresh_already_running, _RUN_GUARD)
    assert is_refresh_already_running() is False
    with _RUN_GUARD:
        assert is_refresh_already_running() is True


def test_airing_guard_wired():
    from core.automation.handlers.video_auto_wishlist_airing import (
        is_airing_already_running, _RUN_GUARD)
    assert is_airing_already_running() is False
    with _RUN_GUARD:
        assert is_airing_already_running() is True


def test_reenrich_guard_wired():
    from core.automation.handlers.video_reenrich_stale import (
        is_reenrich_already_running, _RUN_GUARD)
    assert is_reenrich_already_running() is False
    with _RUN_GUARD:
        assert is_reenrich_already_running() is True


# ---------------------------------------------------------------------------
# The handlers must actually HOLD the guard while running (not just define it).
# Each test observes the guard from inside the run via an injected callable.
# ---------------------------------------------------------------------------


class _QuietDeps:
    def update_progress(self, automation_id, **kw):
        pass


def test_refresh_handler_holds_guard_during_run():
    from core.automation.handlers import video_refresh_airing_schedules as mod
    seen = []

    def fetch_shows():
        seen.append(mod.is_refresh_already_running())
        return []

    mod.auto_video_refresh_airing_schedules(
        {"_automation_id": "a"}, _QuietDeps(), fetch_shows=fetch_shows)
    assert seen == [True], "guard must be held while the handler runs"
    assert mod.is_refresh_already_running() is False


def test_airing_handler_holds_guard_during_run():
    from core.automation.handlers import video_auto_wishlist_airing as mod
    seen = []

    def fetch_airing(start, end):
        seen.append(mod.is_airing_already_running())
        return []

    mod.auto_video_add_airing_episodes(
        {"_automation_id": "a"}, _QuietDeps(), fetch_airing=fetch_airing)
    assert seen == [True], "guard must be held while the handler runs"
    assert mod.is_airing_already_running() is False


def test_reenrich_handler_holds_guard_during_run():
    from core.automation.handlers import video_reenrich_stale as mod
    seen = []

    def fetch_stale(limit, offset, days):
        seen.append(mod.is_reenrich_already_running())
        return []

    mod.auto_video_reenrich_stale(
        {"_automation_id": "a"}, _QuietDeps(), fetch_stale=fetch_stale)
    assert seen == [True], "guard must be held while the handler runs"
    assert mod.is_reenrich_already_running() is False
