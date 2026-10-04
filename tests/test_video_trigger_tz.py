"""Fix 13: 'today' is derived in the trigger's timezone, not server-local."""
from core.automation.handlers.video_auto_wishlist_airing import _today_in_tz


def test_today_in_tz_uses_trigger_tz():
    # a tz far ahead of UTC: at 23:00 UTC it's already tomorrow there
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    # pick a tz where it's definitely a different date than UTC right now
    # (we can't control "now", so just verify the function respects the tz)
    result = _today_in_tz('Pacific/Auckland')
    expected = datetime.now(ZoneInfo('Pacific/Auckland')).date().isoformat()
    assert result == expected


def test_today_in_tz_bad_name_falls_back():
    from datetime import date
    # invalid tz name → server-local fallback, no crash
    assert _today_in_tz('Not/ARealZone') == date.today().isoformat()


def test_today_in_tz_none_uses_utc():
    from datetime import datetime, timezone
    assert _today_in_tz(None) == datetime.now(timezone.utc).date().isoformat()


def test_handler_derives_today_from_trigger_tz():
    # the handler must use config['_trigger_tz'], not server-local date.today()
    from core.automation.handlers import video_auto_wishlist_airing as mod
    from datetime import datetime
    from zoneinfo import ZoneInfo

    seen = []
    class _Deps:
        def update_progress(self, automation_id, **kw): pass

    def fetch_airing(start, end):
        seen.append((start, end))
        return []

    # use a tz where "today" differs from server-local (Auckland is UTC+12/+13)
    mod.auto_video_add_airing_episodes(
        {"_automation_id": "a", "_trigger_tz": "Pacific/Auckland"},
        _Deps(), fetch_airing=fetch_airing,
        # stub the rest to avoid DB access
        add_episodes=lambda *a: 0,
        get_bookmark=lambda: (None, False),
        set_bookmark=lambda *a: True,
        unowned_follows=lambda: [],
        tmdb_airings=lambda *a: [],
        prune_follows=lambda: [],
    )
    # the window should be derived from Auckland's today, not the server's
    expected_today = datetime.now(ZoneInfo("Pacific/Auckland")).date().isoformat()
    assert seen, "fetch_airing should have been called"
    # start <= expected_today <= end (catch-up window covers today)
    start, end = seen[0]
    assert start <= expected_today <= end, f"window {start}..{end} should cover {expected_today}"
