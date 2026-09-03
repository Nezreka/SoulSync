"""Plex/Jellyfin poster art goes through the disk cache too.

The TMDB branch of `_stream_art` has been disk-cached for a while; the
MEDIA-SERVER branch below it never was. Every poster in a grid re-fetched from
Plex on every page view, with only `Cache-Control` between the server and that
cost - so a hard refresh, a second device or a cleared browser cache paid for
the whole grid again.

The numbers are why it shows up as lag rather than as slow images: a Plex
poster transcode measures ~97ms against Boulder's LAN server, a watchlist page
renders 60 cards, and the app runs ONE gunicorn worker with eight threads. Sixty
blocking proxy fetches occupy every thread for the better part of a second while
the page's own API calls queue behind them.
"""

from __future__ import annotations

import pytest
from flask import Flask

from api.video import poster as poster_mod


class _Cached:
    def __init__(self, path, status="hit"):
        self.path, self.mime_type, self.size, self.status = path, "image/jpeg", 3, status


@pytest.fixture()
def app(tmp_path, monkeypatch):
    """The poster blueprint over a stub DB that reports one Plex show poster."""
    import api.video as videoapi

    class _Db:
        def get_art_ref(self, kind, item_id, art):
            return {"poster_url": "/library/metadata/9/thumb/1", "server_source": "plex",
                    "server_id": "9"}

    monkeypatch.setattr(videoapi, "get_video_db", lambda: _Db())
    monkeypatch.setattr("core.video.sources.video_plex_config",
                        lambda: {"base_url": "http://plex.lan:32400", "token": "T0KEN"})
    app = Flask(__name__)

    @app.before_request
    def _stamp():
        from flask import g
        g.is_admin = True
        g.can_download = True

    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    return app


def test_a_cached_plex_poster_never_touches_the_server(app, tmp_path, monkeypatch):
    f = tmp_path / "p.jpg"
    f.write_bytes(b"jpg")
    asked = []
    monkeypatch.setattr(poster_mod, "_cached_file", lambda url: (asked.append(url), _Cached(f))[1])

    def _no(*a, **k):
        raise AssertionError("went upstream despite a cache hit")
    monkeypatch.setattr("requests.get", _no)

    r = app.test_client().get("/api/video/poster/show/9")
    assert r.status_code == 200
    assert r.headers["X-SoulSync-Image-Cache"] == "hit"
    # The cached URL is the authenticated one — same as the music side does for
    # Navidrome covers, and what the cache's LAN-host support exists for.
    assert asked and "plex.lan" in asked[0] and "X-Plex-Token=T0KEN" in asked[0]


def test_a_cache_miss_still_streams_from_the_server(app, monkeypatch):
    """A cache problem must cost a cache miss, never a broken poster."""
    monkeypatch.setattr(poster_mod, "_cached_file", lambda url: None)

    class _Resp:
        status_code = 200
        headers = {"Content-Type": "image/jpeg"}

        def iter_content(self, n):
            yield b"jpg"

    calls = []
    monkeypatch.setattr("requests.get", lambda *a, **k: (calls.append(a), _Resp())[1])

    r = app.test_client().get("/api/video/poster/show/9")
    assert r.status_code == 200 and r.data == b"jpg"
    assert calls, "a cache miss has to fall through to the server"


def test_the_transcode_fallback_survives_the_change(app, monkeypatch):
    """Plex's photo transcoder 404s when a server has transcoding off, and the
    proxy retries the original URL. Caching must not have eaten that."""
    monkeypatch.setattr(poster_mod, "_cached_file", lambda url: None)
    seen = []

    class _Resp:
        def __init__(self, code):
            self.status_code = code
            self.headers = {"Content-Type": "image/jpeg"}

        def iter_content(self, n):
            yield b"jpg"

    def _get(url, **kw):
        seen.append(url)
        return _Resp(404 if len(seen) == 1 else 200)

    monkeypatch.setattr("requests.get", _get)

    r = app.test_client().get("/api/video/poster/show/9?w=342")
    assert r.status_code == 200
    assert len(seen) == 2, "the non-transcoded fallback was not tried"
    assert "transcode" in seen[0] and "transcode" not in seen[1]
