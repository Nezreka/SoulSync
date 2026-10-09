"""the admin-owned video watchlist automations also scan every other profile.

the video watchlist is per-profile and the system scan automations belong to
admin. every handler reads and writes through the background profile, so
before this a non-admin's followed people, studios, channels, playlists and
shows were never scanned at all.
"""

from __future__ import annotations

from core.automation.handlers.video_auto_wishlist_airing import (
    auto_video_add_airing_episodes_all_profiles,
)
from core.automation.handlers.video_profile_fanout import extra_profiles, run_per_profile
from core.profile_context import get_current_profile_id


class _Deps:
    def __init__(self):
        self.progress = []

    def update_progress(self, automation_id, **kw):
        self.progress.append(kw)


def test_only_admin_fans_out():
    assert extra_profiles(1, [1, 2, 3]) == [2, 3]
    assert extra_profiles(1, [1]) == []
    # an automation a non-admin owns stays theirs alone
    assert extra_profiles(2, [1, 2, 3]) == []


def test_each_profile_scanned_under_its_own_profile():
    deps = _Deps()
    seen = []

    def run_pass(d, pid, is_owner):
        seen.append((pid, get_current_profile_id(), is_owner))
        d.update_progress('a', status='finished', log_line='done')
        return {'status': 'completed', 'people': 1, 'movies_added': pid, '_manages_own_progress': True}

    out = run_per_profile(run_pass, deps, ['person'],
                          follower_profiles=lambda kinds: [1, 2, 3], owner_fn=lambda: 1)
    assert [(p, own) for p, _ctx, own in seen] == [(1, True), (2, False), (3, False)]
    # the extra passes really run as that profile, so reads and writes land
    # on that profile's lists
    assert [ctx for _p, ctx, _own in seen][1:] == [2, 3]
    assert out['people'] == 3 and out['movies_added'] == 6
    assert out['status'] == 'completed'
    # only the last pass may tell the card it finished
    statuses = [p.get('status') for p in deps.progress]
    assert statuses == [None, None, 'finished']
    assert deps.progress[1]['log_line'].startswith('[')


def test_no_followers_runs_the_owner_like_before():
    deps = _Deps()
    calls = []

    def run_pass(d, pid, is_owner):
        calls.append(pid)
        assert d is deps   # untouched deps on the plain single run
        return {'status': 'completed'}

    run_per_profile(run_pass, deps, ['studio'],
                    follower_profiles=lambda kinds: [], owner_fn=lambda: 1)
    assert calls == [1]


def test_one_profile_failing_does_not_skip_the_rest():
    calls = []

    def run_pass(d, pid, is_owner):
        calls.append(pid)
        if pid == 2:
            raise RuntimeError('tmdb down')
        return {'status': 'completed', 'channels': 1}

    out = run_per_profile(run_pass, _Deps(), ['channel'],
                          follower_profiles=lambda kinds: [2, 3], owner_fn=lambda: 1)
    assert calls == [1, 2, 3]
    assert out['status'] == 'error' and 'tmdb down' in out['error']


# ── airing: extra passes only take explicit follows; one shared bookmark ─────

def _row(tid, s, e):
    return {"show_tmdb_id": tid, "show_id": tid * 100, "show_title": "Show %s" % tid,
            "season_number": s, "episode_number": e, "title": "Ep",
            "air_date": "2026-06-21", "has_file": False, "monitored": 1}


def _airing(**overrides):
    added = []
    writes = []
    kw = dict(
        fetch_airing=lambda start, end: [_row(1, 1, 1), _row(5, 2, 3)],
        add_episodes=lambda tid, title, eps, *a, **k: (
            added.append((get_current_profile_id(), tid)) or len(eps)),
        today_fn=lambda: "2026-06-21",
        season_meta=lambda *a: None,
        get_bookmark=lambda: "2026-06-20",
        set_bookmark=writes.append,
        unowned_follows=lambda: [],
        tmdb_airings=lambda tid, s, e: {'poster_url': None, 'episodes': []},
        fetch_follows=lambda pid: [{'tmdb_id': 5}] if pid == 2 else [],
        follower_profiles=lambda kinds: [1, 2],
    )
    kw.update(overrides)
    out = auto_video_add_airing_episodes_all_profiles(
        {"_automation_id": "a", "prune_ended": False}, _Deps(), **kw)
    return out, added, writes


def test_airing_reaches_a_non_admin_follow():
    out, added, writes = _airing()
    # admin gets the whole calendar as before; profile 2 gets only show 5,
    # the one it follows, not every library show admin watches by default
    assert (1, 1) in added and (1, 5) in added
    assert [a for a in added if a[0] == 2] == [(2, 5)]
    assert out['status'] == 'completed'
    # one bookmark write, after both passes
    assert writes == ["2026-06-21"]


def test_airing_failed_pass_keeps_the_bookmark():
    def add(tid, title, eps, *a, **k):
        if get_current_profile_id() == 2:
            raise RuntimeError('db locked')
        return len(eps)

    out, _added, writes = _airing(add_episodes=add)
    assert out['status'] == 'error'
    # every profile re-covers the window next run (the upsert is idempotent)
    assert writes == []


def test_watchlist_profile_ids(tmp_path):
    from database.video_database import VideoDatabase
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    db.add_to_watchlist('person', 10, 'A', profile_id=1)
    db.add_to_watchlist('person', 11, 'B', profile_id=3)
    db.add_to_watchlist('studio', 12, 'C', profile_id=2)
    assert db.watchlist_profile_ids(['person']) == [1, 3]
    assert db.watchlist_profile_ids(['studio', 'person']) == [1, 2, 3]
    assert db.watchlist_profile_ids([]) == []


def test_refresh_schedules_covers_every_profiles_shows_once(monkeypatch):
    import api.video as video_api
    import core.video.sources as sources
    from core.automation.handlers import video_refresh_airing_schedules as refresh

    class _DB:
        def watchlist_continuing_shows(self, server, profile_id=1):
            return {1: [{'library_id': 7, 'title': 'Shared'}],
                    2: [{'library_id': 7, 'title': 'Shared'},
                        {'library_id': 9, 'title': 'Only Two Follows This'}]}[profile_id]

        def watchlist_profile_ids(self, kinds):
            assert kinds == ['show']
            return [1, 2]

    monkeypatch.setattr(video_api, 'get_video_db', lambda: _DB())
    monkeypatch.setattr(sources, 'resolve_video_server', lambda: 'plex')
    shows = refresh._default_fetch_shows()
    assert [s['library_id'] for s in shows] == [7, 9]
