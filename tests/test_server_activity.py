"""live server activity: what a card shows, the click-through to soulsync's own
pages, the drawer wiring and the plex connection cache.

the rich shape (source vs stream, reasons, transcoder, kinds) is pinned in
test_server_activity_normalize.py; the recorder, history and stats in their
own files.
"""

from __future__ import annotations

from xml.etree import ElementTree as ET

from core.server_activity import jellyfin as jf
from core.server_activity import links
from core.server_activity import plex as px
from tests.sa_helpers import sa_env  # noqa: F401 - fixture


def _track(**a):
    base = {"type": "track", "title": "t", "sessionKey": "1"}
    base.update({k: v for k, v in a.items() if v is not None})
    return px.normalize(ET.Element("Track", **base))


# ── what a card shows: the thing playing, before whoever made it (#2089) ─────

def test_a_track_shows_its_albums_cover_not_the_artists_picture():
    s = _track(title="I Don't Get Lonely (Extended Mix)", grandparentTitle="Orjan Nilsen", parentTitle="Anomalous",
               thumb="/library/metadata/9/thumb/1", parentThumb="/library/metadata/8/thumb/1",
               grandparentThumb="/library/metadata/7/thumb/1", art="/library/metadata/7/art/1",
               grandparentArt="/library/metadata/7/art/1")
    assert s["thumb"] == "/library/metadata/8/thumb/1"
    assert s["art"] == "/library/metadata/8/thumb/1"      # the backdrop is the cover too


def test_an_audiobook_chapter_shows_the_books_cover_not_the_authors_picture():
    s = _track(title="Chapter 12", grandparentTitle="Caroline Peckham", parentTitle="Zodiac Academy 8",
               parentThumb="/library/metadata/21/thumb/1", grandparentThumb="/library/metadata/20/thumb/1",
               art="/library/metadata/20/art/1")
    assert s["thumb"] == s["art"] == "/library/metadata/21/thumb/1"


def test_the_artists_picture_is_only_what_is_left_without_a_cover():
    s = _track(title="Loose Single", grandparentTitle="Someone", grandparentThumb="/library/metadata/7/thumb/1",
               art="/library/metadata/7/art/1")
    assert s["thumb"] == "/library/metadata/7/thumb/1" and s["art"] == "/library/metadata/7/art/1"


def test_an_episode_keeps_its_shows_poster_and_a_movie_its_own():
    ep = px.normalize(ET.Element("Video", type="episode", title="Pilot", grandparentTitle="Severance",
                                 thumb="/library/metadata/3/thumb/1", parentThumb="/library/metadata/2/thumb/1",
                                 grandparentThumb="/library/metadata/1/thumb/1", art="/library/metadata/1/art/1"))
    assert ep["thumb"] == "/library/metadata/1/thumb/1"
    mv = px.normalize(ET.Element("Video", type="movie", title="Heat", thumb="/library/metadata/5/thumb/1",
                                 art="/library/metadata/5/art/1"))
    assert (mv["thumb"], mv["art"]) == ("/library/metadata/5/thumb/1", "/library/metadata/5/art/1")


def _jf(npi, **s):
    return jf.normalize(dict({"NowPlayingItem": npi, "PlayState": {}, "Id": "s1"}, **s))


def test_a_jellyfin_track_with_no_picture_of_its_own_shows_its_albums_cover():
    npi = {"Type": "Audio", "Name": "Teardrop", "AlbumArtist": "Massive Attack", "Album": "Mezzanine",
           "Id": "track1", "AlbumId": "album1", "AlbumPrimaryImageTag": "abc", "ImageTags": {}}
    n = _jf(npi)
    assert n["thumb"] == n["art"] == "jf:album1"
    assert _jf(dict(npi, ImageTags={"Primary": "t"}))["thumb"] == "jf:track1"
    assert _jf({"Type": "Audio", "Name": "X", "Id": "track2", "AlbumId": "album2"})["thumb"] == "jf:track2"


def test_jellyfin_track_uses_artist_album():
    n = _jf({"Type": "Audio", "Name": "Teardrop", "AlbumArtist": "Massive Attack", "Album": "Mezzanine", "Id": "x"})
    assert n["kind"] == "track" and n["subtitle"] == "Massive Attack · Mezzanine"


# ── click-through: the soulsync page for what's playing ──────────────────────

def _hinted(kind, **hint):
    return {"kind": kind, "show": {"season": 2, "episode": 4} if kind == "episode" else None,
            "_link": dict({"server": "plex", "kind": kind, "rating_key": "", "parent_key": "",
                           "grandparent_key": "", "title": "", "year": None, "tmdb": ""}, **hint)}


class _VDB:
    def __init__(self, rows=(), by_title=None):
        self.rows, self.by_title, self.calls = list(rows), by_title or {}, 0

    def items_by_server_ids(self, ids, server_source=None):
        self.calls += 1
        return [r for r in self.rows if r["server_id"] in ids]

    def find_library_ref_by_title(self, kind, title, year=None):
        return self.by_title.get((kind, title, year))


def test_resolve_library_links(sa_env, monkeypatch):
    vdb = _VDB([{"kind": "movie", "id": 7, "tmdb_id": 603, "server_id": "100", "title": "Heat"},
                {"kind": "show", "id": 9, "tmdb_id": 1396, "server_id": "200", "title": "BB"}])
    monkeypatch.setattr("api.video.get_video_db", lambda: vdb)
    ss = [_hinted("movie", rating_key="100"), _hinted("episode", rating_key="201", grandparent_key="200"),
          _hinted("movie", rating_key="999")]
    links.resolve(ss, now=0)
    assert ss[0]["link"] == {"kind": "movie", "id": 7, "source": "library"}
    assert ss[1]["link"] == {"kind": "show", "id": 9, "source": "library", "season": 2, "episode": 4}
    assert ss[2]["link"] is None
    assert all("_link" not in s and "_key" not in s for s in ss)
    # cached: the next poll asks the db nothing
    again = [_hinted("movie", rating_key="100")]
    links.resolve(again, now=10)
    assert again[0]["link"]["id"] == 7 and vdb.calls == 1


def test_resolve_falls_back_to_title_year_then_tmdb(sa_env, monkeypatch):
    monkeypatch.setattr("api.video.get_video_db", lambda: _VDB(by_title={("movie", "Heat", 1995): 42}))
    ss = [_hinted("movie", rating_key="x", title="Heat", year=1995),
          _hinted("movie", rating_key="y", title="Dune", year=2024, tmdb="693134")]
    links.resolve(ss, now=0)
    assert ss[0]["link"] == {"kind": "movie", "id": 42, "source": "library"}
    assert ss[1]["link"] == {"kind": "movie", "id": "693134", "source": "tmdb"}


def test_resolve_links_kind_mismatch_is_ignored(sa_env, monkeypatch):
    monkeypatch.setattr("api.video.get_video_db",
                        lambda: _VDB([{"kind": "show", "id": 3, "tmdb_id": 1, "server_id": "5", "title": "X"}]))
    ss = [_hinted("movie", rating_key="5")]
    links.resolve(ss, now=0)
    assert ss[0]["link"] is None


def test_a_track_links_to_its_library_artist_album_and_track(sa_env):
    from core.server_activity import store
    with store.connect() as c:
        c.executescript("""
            CREATE TABLE lib2_albums (id INTEGER PRIMARY KEY, primary_artist_id INTEGER);
            CREATE TABLE lib2_tracks (id INTEGER PRIMARY KEY, album_id INTEGER, server_source TEXT, server_id TEXT);
            CREATE TABLE lib2_media_server_mappings (entity_type TEXT, entity_id INTEGER, server_source TEXT,
                                                     server_library_id TEXT DEFAULT '', server_id TEXT);
            INSERT INTO lib2_albums VALUES (11, 5);
            INSERT INTO lib2_tracks VALUES (101, 11, NULL, NULL), (102, 11, 'plex', '703');
            INSERT INTO lib2_media_server_mappings VALUES ('track', 101, 'plex', '', '702');
        """)
    ss = [_hinted("track", rating_key="702"), _hinted("track", rating_key="703"), _hinted("track", rating_key="9")]
    links.resolve(ss, now=0)
    assert ss[0]["link"] == {"kind": "music", "id": 5, "artist_id": 5, "album_id": 11, "track_id": 101,
                             "source": "library"}
    assert ss[1]["link"]["track_id"] == 102            # the compatibility columns
    assert ss[2]["link"] is None


def test_audiobooks_and_podcasts_are_told_apart_from_music(monkeypatch):
    monkeypatch.setattr(links, "_root_names", lambda: {"audiobook": "audiobooks", "podcast": "podcasts"})
    monkeypatch.setattr(links, "_book_index", lambda: {"andy weir/project hail mary": {"asin": "B08", "narrator": "Ray Porter"}})
    assert links.audio_kind("Audiobooks", "") == "audiobook"
    assert links.audio_kind("Hörbücher", "") == "audiobook"
    assert links.audio_kind("My Podcasts", "") == "podcast"
    assert links.audio_kind("Music", "/data/podcasts/Hard Fork/ep1.mp3") == "podcast"
    assert links.audio_kind("Music", "/data/music/Radiohead/Kid A/01.flac") is None
    # a book folder soulsync knows, on a server that mounts it elsewhere
    assert links.audio_kind("Books", "/srv/library/Andy Weir/Project Hail Mary/14.m4b") == "audiobook"
    assert links.book_meta("D:\\stuff\\Andy Weir\\Project Hail Mary\\CD1\\01.mp3")["asin"] == "B08"


# ── frontend wiring ──────────────────────────────────────────────────────────
def test_ui_is_wired():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    idx = (root / "webui" / "index.html").read_text(encoding="utf-8")
    # ported to typescript (src/shell, aug 26); same pins against the TS source
    js = (root / "webui" / "src" / "shell" / "server-activity.ts").read_text(encoding="utf-8")
    css = (root / "webui" / "static" / "style.css").read_text(encoding="utf-8")
    # app-wide floating button next to the notif bell + the script include
    assert 'id="activity-float-btn"' in idx and "ServerActivity.toggle()" in idx
    assert "dist/shell.js" in idx  # the shell bundle carries the port
    # the drawer + poll + endpoints
    assert "/api/server-activity" in js and "/api/server-activity/image" in js
    assert "function card" in js and "function refresh" in js
    assert "setInterval(() => void refresh(), 3000)" in js  # live cadence while open
    assert "startBadgePoll" in js                     # ambient badge from any page
    # never touches the network-triggering user thumb — initials avatar instead
    assert "function initials" in js
    # tabbed: Activity + History (Phase 2)
    assert 'data-sact-tab="activity"' in js and 'data-sact-tab="history"' in js
    assert "function historyRow" in js and "function ago" in js
    assert "/api/server-activity/history" in js
    # the elegant bits exist in CSS
    assert ".sact-drawer" in css and ".sact-badge--tc" in css and ".activity-live" in css
    assert ".sact-tab--on" in css and ".sact-hrow" in css
    # stream termination (Tautulli's kill move)
    assert "data-sact-stop" in js and "function openStop" in js
    assert "/api/server-activity/stop" in js and ".sact-stop-modal" in css
    # stats tab: graph + rankings (Phase 3)
    assert 'data-sact-tab="stats"' in js and "function renderStats" in js
    assert "function graph" in js and "/api/server-activity/stats" in js
    assert ".sact-graph" in css and ".sact-rank-bar" in css
    # visual elevation: smooth-ticking progress, no-flicker key-diff, equalizer, playhead
    assert "function liveTick" in js and "data-sact-fill" in js
    assert "setInterval(liveTick, 500)" in js
    assert "function actKey" in js and "_actKeys" in js       # key-diffed render (no flicker)
    assert "sact-eq" in js and ".sact-eq" in css              # music equalizer
    assert ".sact-head-dot" in css and "@keyframes sactCardIn" in css
    # click-through to the SoulSync detail page
    assert "sact-card--link" in js and "SoulSyncVideo.openDetail" in js
    assert "data-link-kind" in js and ".sact-card--link" in css
    vs = (root / "webui" / "static" / "video" / "video-side.js").read_text(encoding="utf-8")
    assert "window.SoulSyncVideo.openDetail" in vs and "persistSide('video')" in vs


def test_web_server_registers_the_routes():
    from pathlib import Path
    ws = (Path(__file__).resolve().parent.parent / "api" / "server_activity.py").read_text(encoding="utf-8")
    for route in ("'/api/server-activity'", "'/api/server-activity/image'", "'/api/server-activity/history'",
                  "'/api/server-activity/history/<group_key>'", "'/api/server-activity/stats'",
                  "'/api/server-activity/stop', methods=['POST']", "'/api/server-activity/message', methods=['POST']",
                  "'/api/server-activity/settings', methods=['GET', 'POST']",
                  "'/api/server-activity/plays/clear', methods=['POST']", "'/api/server-activity/avatar'"):
        assert f"@bp.route({route})" in ws, route
    assert "admin_or_api_key_refusal" in ws   # admin-gated, every route but the image (#2155)
    assert "_OPEN_PATHS = ('/api/server-activity/image',)" in ws


def test_the_sampler_runs_from_the_live_push_loop():
    from pathlib import Path
    ws = (Path(__file__).resolve().parent.parent / "web_server.py").read_text(encoding="utf-8")
    body = ws[ws.index("def _emit_server_activity_loop"):ws.index("# --- Socket.IO event handlers ---")]
    assert "run_sampler_forever(" in body and "room='activity:live'" in body


def test_a_working_connection_is_kept_past_a_minute(monkeypatch):
    """the connection used to be rebuilt every 60 s, a plex round trip for
    nothing."""
    import core.server_activity as sa
    import plexapi.server
    sa.invalidate_plex_server_cache()
    monkeypatch.setattr(sa, "_plex_config", lambda db=None: {"base_url": "http://192.0.2.1:32400", "token": "abcdef123456"})
    clock = [1000.0]
    monkeypatch.setattr(sa.time, "time", lambda: clock[0])
    built = []
    monkeypatch.setattr(plexapi.server, "PlexServer", lambda *a, **k: built.append(1) or object())
    try:
        first = sa._plex_server()
        for _ in range(30):
            clock[0] += 61
            assert sa._plex_server() is first
        assert len(built) == 1
    finally:
        sa.invalidate_plex_server_cache()


def test_a_failed_connection_backs_off_then_reconnects(monkeypatch):
    import core.server_activity as sa
    import plexapi.server
    sa.invalidate_plex_server_cache()
    monkeypatch.setattr(sa, "_plex_config", lambda db=None: {"base_url": "http://192.0.2.1:32400", "token": "abcdef123456"})
    clock = [1000.0]
    monkeypatch.setattr(sa.time, "time", lambda: clock[0])
    built = []
    monkeypatch.setattr(plexapi.server, "PlexServer", lambda *a, **k: built.append(object()) or built[-1])
    try:
        srv = sa._plex_server()
        sa._mark_plex_unreachable(srv)
        clock[0] += 30
        assert sa._plex_server() is None          # backing off
        assert len(built) == 1
        clock[0] += 31
        again = sa._plex_server()                 # past the minute: reconnect
        assert again is not None and again is not srv
        assert len(built) == 2
    finally:
        sa.invalidate_plex_server_cache()
