"""TheHomeGuy: his Pi-hole rate-limited SoulSync for hitting Navidrome over
1,000 times an hour. the listening poll asked getAlbum for up to 50 recent and
100 frequent albums on every poll, about 150 requests a poll per user, whether
anything had been played or not. an album is now fetched again only when its
own played stamp or play count moves."""

from __future__ import annotations

from core.navidrome_client import NavidromeClient, NavidromeUserView


def _client(albums_by_type, songs_by_album):
    c = NavidromeClient.__new__(NavidromeClient)
    NavidromeClient.__init__(c)
    c.base_url, c.username, c.password = "http://nd", "app", "pw"
    calls = []

    def req(endpoint, params=None, as_user=None, timeout=None):
        calls.append((endpoint, (params or {}).get("id") or (params or {}).get("type"), as_user))
        if endpoint == "getAlbumList2":
            return {"albumList2": {"album": albums_by_type[params["type"]]}}
        if endpoint == "getAlbum":
            return {"album": {"song": songs_by_album[params["id"]]}}
        return None

    c._make_request = req
    c.ensure_connection = lambda: True
    return c, calls


ALBUMS = [{"id": "a1", "played": "2026-10-27T10:00:00Z", "playCount": 3},
          {"id": "a2", "played": "2026-10-26T10:00:00Z", "playCount": 1}]
SONGS = {"a1": [{"id": "t1", "title": "One", "played": "2026-10-27T10:00:00Z", "playCount": 3}],
         "a2": [{"id": "t2", "title": "Two", "played": "2026-10-26T10:00:00Z", "playCount": 1}]}


def _gets(calls):
    return [c for c in calls if c[0] == "getAlbum"]


def test_a_quiet_poll_is_just_the_two_list_calls():
    c, calls = _client({"recent": ALBUMS, "frequent": ALBUMS}, SONGS)
    first = (c.get_play_history(), c.get_track_play_counts())
    assert len(_gets(calls)) == 2               # each album fetched once, shared by both reads
    calls.clear()
    again = (c.get_play_history(), c.get_track_play_counts())
    assert _gets(calls) == []
    assert [x[0] for x in calls] == ["getAlbumList2", "getAlbumList2"]
    assert again == first


def test_an_album_played_since_is_fetched_again():
    albums = [dict(a) for a in ALBUMS]
    c, calls = _client({"recent": albums, "frequent": albums}, SONGS)
    c.get_play_history()
    calls.clear()
    albums[0]["playCount"] = 4
    albums[0]["played"] = "2026-10-28T09:00:00Z"
    c.get_play_history()
    assert [x[1] for x in _gets(calls)] == ["a1"]


def test_each_user_keeps_their_own_play_data():
    c, calls = _client({"recent": ALBUMS, "frequent": ALBUMS}, SONGS)
    c.get_play_history()
    calls.clear()
    view = NavidromeUserView(c, "kid", "pw2")
    view.get_play_history()
    assert len(_gets(calls)) == 2               # another user's plays aren't the app account's


def test_a_server_without_album_play_values_is_never_cached():
    bare = [{"id": "a1"}, {"id": "a2"}]
    c, calls = _client({"recent": bare, "frequent": bare}, SONGS)
    c.get_play_history()
    c.get_play_history()
    assert len(_gets(calls)) == 4


def test_the_old_plays_tagger_gets_history_again():
    """limit=None raised inside min() and came back as an empty history."""
    c, _calls = _client({"recent": ALBUMS, "frequent": ALBUMS}, SONGS)
    assert len(c.get_play_history(limit=None)) == 2
