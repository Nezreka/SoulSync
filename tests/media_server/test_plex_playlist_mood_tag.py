"""Tag every song on a plex playlist with a mood (wolf39us, Discord Nov 4 2026).

With the songs tagged, a plex smart playlist can start from "mood is <tag>"
and grow from rating rules. Plex metadata only; existing moods stay.
"""

from __future__ import annotations

from types import SimpleNamespace

from core.plex_client import PlexClient


class _Track:
    def __init__(self, title, moods=(), fail=False):
        self.title, self.type = title, "track"
        self.moods = [SimpleNamespace(tag=m) for m in moods]
        self.added, self.fail = [], fail

    def addMood(self, tag):  # noqa: N802 - plexapi's name
        if self.fail:
            raise RuntimeError("plex said no")
        self.added.append(tag)


def test_each_song_gets_the_mood_once_and_keeps_its_others():
    tracks = [_Track("a"), _Track("b", moods=["Mom"]), _Track("c", moods=["Chill"]),
              _Track("d", fail=True)]
    counts = PlexClient.tag_tracks_mood(tracks, " mom ")
    assert counts == {"tagged": 2, "skipped": 1, "failed": 1}
    assert tracks[0].added == ["mom"] and tracks[2].added == ["mom"]
    assert tracks[1].added == [], "already tagged, case aside"


def test_only_tracks_of_the_playlist_are_tagged():
    client = PlexClient.__new__(PlexClient)
    client.ensure_connection = lambda: True
    items = [_Track("song"), SimpleNamespace(type="episode", title="podcast")]
    client.server = SimpleNamespace(fetchItem=lambda key: SimpleNamespace(items=lambda: items))
    assert [t.title for t in client.playlist_tracks_for_tagging("42")] == ["song"]


def _client(monkeypatch, *, admin=True, server="plex"):
    import web_server
    from flask.testing import FlaskClient
    monkeypatch.setattr(web_server.app, "test_client_class", FlaskClient)
    monkeypatch.setattr(web_server, "is_admin_request", lambda: admin)
    monkeypatch.setattr(web_server.config_manager, "get_active_media_server", lambda: server)
    tagged = []
    fake = SimpleNamespace(playlist_tracks_for_tagging=lambda pid: [_Track("x"), _Track("y")],
                           tag_tracks_mood=lambda tracks, tag: tagged.append(tag) or {})
    monkeypatch.setattr(web_server, "_server_playlist_guard",
                        lambda srv, pid, name: (fake, pid, name or "Mom's list", None))

    class _Inline:
        def __init__(self, target, **_kw):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(web_server.threading, "Thread", _Inline)
    return web_server.app.test_client(), tagged


def test_the_route_tags_in_the_background(monkeypatch):
    http, tagged = _client(monkeypatch)
    res = http.post("/api/server/playlist/42/tag-mood", json={"tag": "mom", "name": "Mom's list"})
    assert res.get_json() == {"success": True, "tag": "mom", "total": 2}
    assert tagged == ["mom"]


def test_the_route_is_admin_only_and_plex_only(monkeypatch):
    http, tagged = _client(monkeypatch, admin=False)
    assert http.post("/api/server/playlist/42/tag-mood", json={"tag": "mom"}).status_code == 403
    http, tagged = _client(monkeypatch, server="jellyfin")
    assert http.post("/api/server/playlist/42/tag-mood", json={"tag": "mom"}).status_code == 400
    http, tagged = _client(monkeypatch)
    assert http.post("/api/server/playlist/42/tag-mood", json={"tag": "  "}).status_code == 400
    assert tagged == []
