"""a movie or episode played through counts as watched, for the profile whose watch state it is"""

import pytest

from core.player import watched
from core.video import metadata as med
from core.video import watch_scope


@pytest.fixture
def calls(monkeypatch):
    seen = []
    monkeypatch.setattr(med, "set_watched", lambda db, kind, vid, on, **kw: seen.append((kind, vid, on, kw)) or {"ok": True})
    import api.video as video_api
    monkeypatch.setattr(video_api, "get_video_db", lambda: "db")
    return seen


def run_now(fn):
    fn()


def test_a_movie_or_episode_is_marked_on_the_watchers_account(calls, monkeypatch):
    monkeypatch.setattr(watch_scope, "current_watcher", lambda: watch_scope.Watcher(rows=4, token="tok"))
    assert watched.mark("episode", "12", spawn=run_now) is True
    assert calls == [("episode", 12, True, {"push": True, "watcher": 4, "member_token": "tok"})]


def test_only_videos_and_only_a_profile_that_can_mark(calls, monkeypatch):
    monkeypatch.setattr(watch_scope, "current_watcher", lambda: watch_scope.Watcher(rows=4, can_mark=False))
    assert watched.mark("movie", 3, spawn=run_now) is False
    monkeypatch.setattr(watch_scope, "current_watcher", lambda: watch_scope.SHARED)
    assert watched.mark("track", 3, spawn=run_now) is False
    assert watched.mark("movie", "x", spawn=run_now) is False
    assert calls == []


def test_a_failed_mark_never_raises(monkeypatch):
    import api.video as video_api
    monkeypatch.setattr(video_api, "get_video_db", lambda: "db")
    monkeypatch.setattr(watch_scope, "current_watcher", lambda: watch_scope.SHARED)

    def boom(*a, **k):
        raise RuntimeError("server down")
    monkeypatch.setattr(med, "set_watched", boom)
    assert watched.mark("movie", 3, spawn=run_now) is True
