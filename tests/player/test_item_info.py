"""the info sheet (core/player/item_info.py, api/player_info.py,
scrap/ITEM_INFO_CONTRACT.md): for every kind the player shows, what the
item is and what file it is, as sections of label/value text. only what
the profile may play, paths and servers for admins only, nothing probed."""

from __future__ import annotations

import os
import sqlite3

import pytest
from flask import Flask, g

import api.player as player_api
import api.player_info as R
from core.player import item_info as I
from core.player import playable as P
from core.player import tracks as K

SCHEMA = os.path.join(os.path.dirname(__file__), "..", "..", "database", "video_schema.sql")
ALL = {"music.access", "movies.access", "tv.access", "books.access", "podcasts.access"}


@pytest.fixture
def ctx(monkeypatch):
    state = {"pid": 3, "perms": set(ALL), "admin": False, "progress": None}
    monkeypatch.setattr(P, "current_profile_id", lambda: state["pid"])
    monkeypatch.setattr(P, "_may", lambda perm: perm in state["perms"])
    monkeypatch.setattr(player_api, "_is_player_admin", lambda: state["admin"])
    monkeypatch.setattr(I, "_progress", lambda t, i: state["progress"])
    monkeypatch.setattr(I, "_player_db", lambda: object())
    monkeypatch.setattr(K, "_probe_cache", {})
    app = Flask(__name__)
    app.register_blueprint(R.create_blueprint())

    @app.before_request
    def _device():
        if state.get("device"):
            g.device_auth = True
            g.device = state["device"]

    state["client"] = app.test_client()
    return state


def get(ctx, path):
    res = ctx["client"].get(f"/api/player/info/{path}")
    return res.status_code, res.get_json()


def section(body, sid, n=0):
    found = [s for s in body["sections"] if s["id"] == sid]
    return found[n] if len(found) > n else None


def values(body, sid, n=0):
    s = section(body, sid, n)
    return {r["label"]: r["value"] for r in s["rows"]} if s else {}


def ids(body):
    return [s["id"] for s in body["sections"]]


# ── video ────────────────────────────────────────────────────────────────

class _Db:
    def __init__(self, path):
        self.path = path

    def _get_connection(self):
        return sqlite3.connect(self.path)


@pytest.fixture
def vdb(tmp_path, monkeypatch, ctx):
    path = tmp_path / "video.db"
    c = sqlite3.connect(path)
    c.executescript(open(SCHEMA, encoding="utf-8").read())
    from database.video_database import _COLUMN_MIGRATIONS
    for table, column, decl in _COLUMN_MIGRATIONS:
        try:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        except sqlite3.OperationalError:
            pass
    c.execute("INSERT INTO movies (id, title, year, tmdb_id, imdb_id, content_rating, runtime_minutes, studio, "
              "rating, imdb_rating, server_source, added_at, subtitle_langs) "
              "VALUES (1, 'Alien', 1979, 348, 'tt0078748', 'R', 117, '20th Century Fox', 8.1, 8.5, 'plex', "
              "'2026-01-02 10:00:00', '[\"en\"]')")
    c.execute("INSERT INTO genres (id, name) VALUES (1, 'Horror'), (2, 'Science Fiction')")
    c.execute("INSERT INTO movie_genres VALUES (1, 1), (1, 2)")
    c.execute("INSERT INTO media_files (id, movie_id, relative_path, size_bytes, resolution, video_codec, "
              "audio_codec, runtime_seconds, audio_channels, dynamic_range, audio_languages, "
              "subtitle_languages, quality, release_source) VALUES "
              "(10, 1, '/media/movies/Alien (1979)/Alien.2160p.mkv', 60000000000, '2160p', 'hevc', 'truehd', "
              "7020, 8, 'HDR10', 'en', 'en,fr', 'Bluray-2160p', 'bluray'), "
              "(11, 1, '/media/movies/Alien (1979)/Alien.1080p.mp4', 8000000000, '1080p', 'h264', 'aac', "
              "7020, 2, NULL, 'en', '', 'WEBDL-1080p', 'web-dl')")
    c.execute("INSERT INTO shows (id, title, tmdb_id, tvdb_id, imdb_id, content_rating, network, "
              "server_source) VALUES (2, 'Severance', 95396, 371980, 'tt11280740', 'TV-MA', 'Apple TV+', "
              "'jellyfin')")
    c.execute("INSERT INTO seasons (id, show_id, season_number) VALUES (20, 2, 1)")
    c.execute("INSERT INTO episodes (id, show_id, season_id, season_number, episode_number, title, air_date, "
              "runtime_minutes, tvdb_id, rating) VALUES (200, 2, 20, 1, 3, 'In Perpetuity', '2022-02-25', "
              "55, 8812345, 8.4)")
    c.execute("INSERT INTO media_files (id, episode_id, relative_path, size_bytes, resolution, video_codec, "
              "audio_codec, runtime_seconds) VALUES (30, 200, '/tv/Severance/S01E03.mkv', 2000000000, "
              "'1080p', 'h264', 'eac3', 3300)")
    c.commit()
    c.close()
    db = _Db(path)
    import api.video.kids as kids
    import core.player.libraries as L
    import database.player_database as pdb
    monkeypatch.setattr(I, "_video_db", lambda: db)
    ctx["cap"] = None
    monkeypatch.setattr(kids, "video_cap", lambda: ctx["cap"])
    monkeypatch.setattr(L, "library_ids_for", lambda vdb, p, kind, ids_: {int(i): 7 for i in ids_})
    monkeypatch.setattr(L, "libraries", lambda p: [{"id": 7, "name": "Movies 4K"}])
    monkeypatch.setattr(pdb, "get_player_database", lambda: object())
    return db


def test_a_movie_has_every_section_with_a_group_per_version(ctx, vdb):
    status, body = get(ctx, "movie/1")
    assert status == 200 and body["success"] is True
    assert body["title"] == "Alien" and body["subtitle"] == "1979 · R · 1h 57m"
    assert body["artwork"] == "/api/video/poster/movie/1?w=342"
    assert ids(body) == ["details", "ids", "file", "video", "audio", "file", "video", "audio",
                         "subtitles", "source"]
    d = values(body, "details")
    assert d["Genres"] == "Horror, Science Fiction" and d["Runtime"] == "1h 57m"
    assert d["Scores"] == "TMDB 8.1 · IMDb 8.5" and d["Added"] == "Jan 2, 2026"
    assert "Plays" not in d and "Progress" not in d  # no value, no row
    assert section(body, "ids")["rows"] == [
        {"label": "TMDB", "value": "348", "mono": True, "copy": True},
        {"label": "IMDb", "value": "tt0078748", "mono": True, "copy": True}]
    # one group per file, biggest first, titled by its quality
    first, second = section(body, "file"), section(body, "file", 1)
    assert (first["group"], first["title"]) == ("4K HDR10", "File · 4K HDR10")
    assert second["group"] == "1080p"
    f = values(body, "file")
    assert f["File name"] == "Alien.2160p.mkv" and f["Container"] == "MKV" and f["Size"] == "55.9 GB"
    assert f["Overall bitrate"] == "68.4 Mbps" and f["Release"] == "Blu-ray"
    assert values(body, "video")["Codec"] == "HEVC" and values(body, "video")["Dynamic range"] == "HDR10"
    assert values(body, "audio") == {"Codec": "TrueHD", "Channels": "7.1", "Languages": "English"}
    assert values(body, "subtitles") == {"Embedded": "English, French", "Downloaded": "English"}
    assert values(body, "source") == {"From": "Plex", "Library": "Movies 4K"}


def test_the_path_and_server_are_for_admins_only(ctx, vdb):
    _, body = get(ctx, "movie/1")
    assert "Path" not in values(body, "file") and "Server" not in values(body, "file")
    assert "/media/movies" not in str(body)
    ctx["admin"] = True
    _, body = get(ctx, "movie/1")
    rows = {r["label"]: r for r in section(body, "file")["rows"]}
    assert rows["Path"] == {"label": "Path", "value": "/media/movies/Alien (1979)/Alien.2160p.mkv",
                            "mono": True, "copy": True}
    assert rows["Server"]["value"] == "Plex" and rows["Server"]["copy"] is True


def test_a_cached_probe_adds_streams_but_none_is_ever_run(ctx, vdb, monkeypatch):
    monkeypatch.setattr(K.subprocess, "run", lambda *a, **k: pytest.fail("ffprobe ran"))
    K._probe_cache[("id", ("media-file", 10, None))] = K.parse_probe({
        "streams": [
            {"codec_type": "video", "codec_name": "hevc", "profile": "Main 10", "width": 3840, "height": 1608,
             "avg_frame_rate": "24000/1001", "pix_fmt": "yuv420p10le", "color_transfer": "smpte2084"},
            {"codec_type": "audio", "codec_name": "truehd", "channels": 8, "tags": {"language": "eng"},
             "disposition": {"default": 1}},
            {"codec_type": "audio", "codec_name": "ac3", "channels": 6, "bit_rate": "640000",
             "tags": {"language": "jpn"}},
            {"codec_type": "subtitle", "codec_name": "hdmv_pgs_subtitle", "tags": {"language": "eng"},
             "disposition": {"forced": 1}},
        ],
        "format": {"format_name": "matroska,webm", "bit_rate": "70000000"},
        "chapters": []})
    K._probe_cache[("id", ("media-file", 10, None))]["chapters"] = [{"start": 0}, {"start": 60}, {"start": 90}]
    _, body = get(ctx, "movie/1")
    v = values(body, "video")
    assert v["Resolution"] == "3840×1608 (4K)" and v["Frame rate"] == "23.976 fps"
    assert v["Bit depth"] == "10-bit" and v["Profile"] == "Main 10"
    assert values(body, "audio") == {"Track 1": "English · TrueHD · 7.1 · default",
                                     "Track 2": "Japanese · AC-3 · 5.1 · 640 kbps"}
    assert values(body, "file")["Overall bitrate"] == "70.0 Mbps"
    assert values(body, "subtitles") == {"English": "PGS · forced · embedded", "Downloaded": "English"}
    assert values(body, "chapters") == {"Chapters": "3"}
    # the second version has no probe: its stored facts
    assert values(body, "audio", 1)["Codec"] == "AAC"


def test_a_movie_over_the_cap_or_in_a_hidden_library_is_refused(ctx, vdb):
    from api.video.kids import VideoCap
    ctx["cap"] = VideoCap(movie=13)
    status, body = get(ctx, "movie/1")
    assert (status, body["error"]) == (403, "restricted")
    # a hidden library answers like a missing title: never named
    ctx["cap"] = VideoCap(hidden={7})
    status, body = get(ctx, "movie/1")
    assert (status, body["error"]) == (404, "not_found") and "Movies 4K" not in str(body)
    ctx["cap"] = None
    ctx["perms"] = {"tv.access"}
    status, body = get(ctx, "movie/1")
    assert (status, body["error"]) == (403, "not_allowed")
    assert get(ctx, "movie/999")[0] == 403
    ctx["perms"] = set(ALL)
    assert get(ctx, "movie/999")[0] == 404
    assert get(ctx, "movie/abc")[0] == 400


def test_an_episode_names_its_show_and_follows_the_show(ctx, vdb):
    ctx["progress"] = {"position_seconds": 1500, "duration_seconds": 3300, "finished": False,
                       "updated_at": "2026-11-20T21:00:00"}
    status, body = get(ctx, "episode/200")
    assert status == 200
    assert body["title"] == "In Perpetuity" and body["subtitle"] == "Severance · S1 E3"
    assert ids(body) == ["details", "ids", "file", "video", "audio", "source"]
    d = values(body, "details")
    assert d["Episode"] == "Season 1, Episode 3" and d["Show"] == "Severance" and d["Network"] == "Apple TV+"
    assert d["Aired"] == "Feb 25, 2022" and d["Progress"] == "25m in of 55m" and d["Last played"] == "Nov 20, 2026"
    assert values(body, "ids") == {"TVDB": "8812345", "Show TMDB": "95396", "Show TVDB": "371980",
                                   "Show IMDb": "tt11280740"}
    assert section(body, "file").get("group") is None and section(body, "file")["title"] == "File"
    assert values(body, "source")["From"] == "Jellyfin"
    from api.video.kids import VideoCap
    ctx["cap"] = VideoCap(hidden={7})
    assert get(ctx, "episode/200")[1]["error"] == "not_found"
    ctx["cap"] = VideoCap(show=7)
    assert get(ctx, "episode/200")[0] == 403


def test_a_paired_music_app_only_asks_about_tracks(ctx, vdb):
    ctx["device"] = {"id": 1, "kind": "music", "profile_id": 3}
    status, body = get(ctx, "movie/1")
    assert (status, body["error"]) == (403, "not_for_this_app")
    ctx["device"] = None
    ctx["pid"] = None
    assert get(ctx, "movie/1")[0] == 401


def test_home_video(ctx, vdb, monkeypatch):
    from core.player import home_access
    from core.player import libraries as L
    from core.video import home_videos as H
    c = sqlite3.connect(vdb.path)
    c.execute("INSERT INTO home_videos (id, source, source_key, root, folder, path, size_bytes, title, "
              "filmed_at, duration_s, width, height, video_codec, audio_codec, added_at) VALUES "
              "(5, 'disk', '/home/clips/Beach/day one.mov', 'r1', 'Beach', '/home/clips/Beach/day one.mov', "
              "300000000, 'Day one', '2024-07-02T12:30:00', 95.5, 1920, 1080, 'h264', 'aac', "
              "'2026-11-01 08:00:00')")
    c.commit()
    c.close()
    ctx["roots"] = {"r1"}
    monkeypatch.setattr(home_access, "visible_roots", lambda p, db, hidden: ctx["roots"])
    monkeypatch.setattr(home_access, "hidden_now", lambda: frozenset())
    monkeypatch.setattr(H, "roots", lambda db: [])
    monkeypatch.setattr(L, "libraries", lambda p: [{"id": 9, "name": "Family"}])
    monkeypatch.setattr(L, "home_library_of", lambda libs, root: 9)
    status, body = get(ctx, "home/5")
    assert status == 200 and body["title"] == "Day one"
    assert ids(body) == ["details", "file", "video", "audio", "source"]
    assert values(body, "details")["Filmed"] == "Jul 2, 2024, 12:30 PM"
    assert values(body, "video")["Resolution"] == "1920×1080"
    assert values(body, "source") == {"From": "Disk", "Library": "Family"}
    assert "Path" not in values(body, "file")
    ctx["roots"] = set()
    assert get(ctx, "home/5")[0] == 404


def test_youtube_video(ctx, vdb):
    c = sqlite3.connect(vdb.path)
    c.execute("INSERT INTO video_download_history (kind, source, media_id, title, channel_id, dest_path, "
              "size_bytes, quality_label, outcome, completed_at, published_at) VALUES ('show', 'youtube', "
              "'dQw4w9WgXcQ', 'Never Gonna', 'UC1', '/yt/Rick/Never Gonna [dQw4w9WgXcQ].mkv', 50000000, "
              "'1080p', 'completed', '2026-11-02 10:00:00', '2009-10-25')")
    c.execute("INSERT INTO youtube_channel_videos (channel_id, youtube_id, title, duration, view_count) "
              "VALUES ('UC1', 'dQw4w9WgXcQ', 'Never Gonna', '3:33', 1500000000)")
    c.execute("INSERT INTO youtube_channel_meta (channel_id, title) VALUES ('UC1', 'Rick Astley')")
    c.execute("INSERT INTO youtube_video_segments (youtube_id, category, start_sec, end_sec, uuid) "
              "VALUES ('dQw4w9WgXcQ', 'intro', 0, 12, 'a'), ('dQw4w9WgXcQ', 'outro', 200, 213, 'b')")
    c.commit()
    c.close()
    status, body = get(ctx, "youtube/dQw4w9WgXcQ")
    assert status == 200 and body["subtitle"] == "Rick Astley · Oct 25, 2009"
    d = values(body, "details")
    assert d["Channel"] == "Rick Astley" and d["Views"] == "1,500,000,000" and d["Runtime"] == "3m 33s"
    assert values(body, "ids") == {"YouTube": "dQw4w9WgXcQ", "Channel": "UC1"}
    assert values(body, "file")["Quality"] == "1080p"
    assert values(body, "chapters") == {"Intro": "0:00 to 0:12", "Credits": "3:20 to 3:33"}
    assert values(body, "source")["Plays from"] == "Your library"
    assert get(ctx, "youtube/zzzzzzzzzzz")[0] == 404
    assert get(ctx, "youtube/bad id!")[0] == 400


# ── music ────────────────────────────────────────────────────────────────

@pytest.fixture
def mdb(tmp_path, monkeypatch, ctx):
    from core.library2 import schema as S
    path = tmp_path / "music.db"
    c = sqlite3.connect(path)
    for ddl in (S.LIB2_ARTISTS_DDL, S.LIB2_ALBUMS_DDL, S.LIB2_TRACKS_DDL, S.LIB2_TRACK_FILES_DDL):
        c.execute(ddl)
    c.execute("INSERT INTO lib2_artists (id, name, musicbrainz_id) VALUES (1, 'Daft Punk', 'mb-artist')")
    c.execute("INSERT INTO lib2_albums (id, primary_artist_id, title, year, release_date, genres, label, upc, "
              "musicbrainz_id, track_count) VALUES (2, 1, 'Discovery', 2001, '2001-03-12', "
              "'[\"French house\", \"Electronic\"]', 'Virgin', '724384960650', 'mb-release', 14)")
    c.execute("INSERT INTO lib2_tracks (id, album_id, title, track_number, duration, isrc, musicbrainz_id, "
              "added_at, play_count) VALUES (3, 2, 'One More Time', 1, 320000, 'GBDUW0000053', 'mb-rec', "
              "'2026-05-01 09:00:00', 12)")
    c.execute("INSERT INTO lib2_track_files (id, track_id, path, size, bitrate, sample_rate, bit_depth, format, "
              "quality_tier, source, is_primary, tags_json) VALUES (40, 3, '/music/Daft Punk/Discovery/01.flac', "
              "90000000, 2250, 96000, 24, 'flac', 'lossless', 'qobuz', 1, ?)",
              ('{"title": "One More Time", "artist": "Daft Punk", "album": "Discovery", "albumartist": '
               '"Daft Punk", "track_number": 1, "total_tracks": 14, "year": "2001", "genre": "House", '
               '"cover": true, "replaygain_track_gain": "-8.21 dB", "replaygain_album_gain": "-7.9"}',))
    c.execute("INSERT INTO lib2_track_files (id, track_id, path, size, bitrate, format, quality_tier, "
              "is_primary) VALUES (41, 3, '/music/Daft Punk/Discovery/01.mp3', 12800000, 320000, 'mp3', "
              "'lossy_high', 0)")
    c.commit()
    c.close()

    class Db(_Db):
        pass
    db = Db(path)
    import core.connect.may_play as MP
    from core.player.sources import music
    ctx["library"] = {3}
    ctx["explicit"] = set()
    ctx["hide"] = False
    monkeypatch.setattr(I, "_music_db", lambda: db)
    monkeypatch.setattr(music, "_scope", lambda: None)
    monkeypatch.setattr(music, "hides_explicit", lambda: ctx["hide"])
    monkeypatch.setattr(MP, "may_play_track", lambda conn, tid, scope, hide_explicit: int(tid) in ctx["library"]
                        and not (hide_explicit and int(tid) in ctx["explicit"]))
    return db


def test_a_track_has_its_files_audio_and_tags(ctx, mdb):
    status, body = get(ctx, "track/3")
    assert status == 200
    assert body["title"] == "One More Time" and body["subtitle"] == "Daft Punk · Discovery"
    assert body["artwork"] == "/api/library/v2/artwork/album/2?size=thumb"
    assert ids(body) == ["details", "ids", "file", "audio", "file", "audio", "tags", "source"]
    d = values(body, "details")
    assert d["Track"] == "1 of 14" and d["Runtime"] == "5:20" and d["Released"] == "Mar 12, 2001"
    assert d["Genres"] == "French house, Electronic" and d["Label"] == "Virgin" and d["Plays"] == "12"
    assert "Album artist" not in d  # same as the artist
    i = values(body, "ids")
    assert i["ISRC"] == "GBDUW0000053" and i["MusicBrainz recording"] == "mb-rec" and i["UPC"] == "724384960650"
    assert all(r["copy"] and r["mono"] for r in section(body, "ids")["rows"])
    assert section(body, "file")["group"] == "FLAC 24/96" and section(body, "file", 1)["group"] == "MP3 320"
    a = values(body, "audio")
    assert a == {"Format": "FLAC", "Bitrate": "2250 kbps", "Sample rate": "96 kHz", "Bit depth": "24-bit",
                 "ReplayGain track": "-8.21 dB", "ReplayGain album": "-7.90 dB"}
    assert values(body, "audio", 1)["Bitrate"] == "320 kbps"  # stored as bps
    assert values(body, "file")["Got from"] == "Qobuz"
    t = values(body, "tags")
    assert t["Track"] == "1 of 14" and t["Embedded art"] == "Yes" and t["Genre"] == "House"
    assert "Path" not in values(body, "file") and "/music/" not in str(body)
    ctx["admin"] = True
    _, body = get(ctx, "track/3")
    assert values(body, "file")["Path"] == "/music/Daft Punk/Discovery/01.flac"


def test_a_track_outside_the_profile_is_refused(ctx, mdb):
    ctx["library"] = set()
    assert get(ctx, "track/3")[1]["error"] == "not_found"
    ctx["library"], ctx["explicit"], ctx["hide"] = {3}, {3}, True
    status, body = get(ctx, "track/3")
    assert (status, body["error"]) == (403, "restricted")
    ctx["hide"] = False
    ctx["perms"] = {"movies.access"}
    assert get(ctx, "track/3")[1]["error"] == "not_allowed"


# ── audiobooks and podcasts ──────────────────────────────────────────────

def test_an_audiobook(ctx, monkeypatch):
    from core.player.sources import audiobook as A
    files = ["/books/Dune/02.m4b", "/books/Dune/01.m4b"]

    class Db:
        def get_library_entry(self, key):
            if key != "B00B7NPRY8":
                return None
            return {"asin": key, "catalog_asin": key, "title": "Dune", "author": "Frank Herbert",
                    "narrator": "Scott Brick", "series_title": "Dune", "series_sequence": "1",
                    "path": "/books/Dune", "file_paths": files, "size_bytes": 900_000_000,
                    "audio_format": "m4b", "runtime_minutes": 1263, "imported_at": 1764000000.0,
                    "origin": "soulsync", "metadata_json": {"language": "eng", "format_type": "unabridged",
                                                            "duration_seconds": 75780},
                    "catalog_book": {"release_date": "2007-06-01"}}
    monkeypatch.setattr(I, "_audiobook_db", lambda: Db())
    monkeypatch.setitem(A._chapter_counts, ("/books/Dune/01.m4b", "/books/Dune/02.m4b"), 48)
    status, body = get(ctx, "audiobook/B00B7NPRY8")
    assert status == 200 and body["subtitle"] == "Frank Herbert"
    assert ids(body) == ["details", "ids", "file", "chapters", "source"]
    d = values(body, "details")
    assert d["Narrator"] == "Scott Brick" and d["Series"] == "Dune, book 1" and d["Runtime"] == "21h 3m"
    assert d["Language"] == "English" and d["Edition"] == "Unabridged"
    assert values(body, "ids") == {"ASIN": "B00B7NPRY8"}
    assert values(body, "file")["Files"] == "2" and "Path" not in values(body, "file")
    assert values(body, "chapters") == {"Chapters": "48"}
    assert values(body, "source")["From"] == "SoulSync download"
    assert get(ctx, "audiobook/nope")[0] == 404
    ctx["perms"] = {"music.access"}
    assert get(ctx, "audiobook/B00B7NPRY8")[0] == 403


@pytest.fixture
def pods(tmp_path, monkeypatch, ctx):
    path = tmp_path / "music.db"
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE downloaded_podcast_episodes (id INTEGER PRIMARY KEY, feed_url TEXT, guid TEXT, "
              "enclosure_url TEXT, title TEXT, pub_date TEXT, file_path TEXT, downloaded_at TEXT, pruned_at TEXT)")
    c.execute("INSERT INTO downloaded_podcast_episodes (feed_url, guid, enclosure_url, title, file_path, "
              "downloaded_at) VALUES ('https://feed/x', 'https://ep/42', 'https://cdn/42.mp3', 'Ep 42', "
              "'/podcasts/Show/42.mp3', '2026-11-03 07:00:00')")
    c.commit()
    c.close()

    class Db(_Db):
        def get_watchlist_podcasts(self, profile_id=None):
            return [{"id": 6, "feed_url": "https://feed/x", "title": "The Show", "author": "Host"}] \
                if profile_id == 3 else []
    monkeypatch.setattr(I, "_music_db", lambda: Db(path))
    show = {"title": "The Show", "artwork_url": "https://art/show.jpg", "episodes": [
        {"guid": "https://ep/42", "title": "Ep 42", "enclosure_url": "https://cdn/42.mp3",
         "enclosure_type": "audio/mpeg", "enclosure_length": 48000000, "pub_date": "2026-11-02T06:00:00",
         "duration_seconds": 3000, "season": 2, "episode_number": 42}]}
    monkeypatch.setattr(I, "_cached_feed", lambda url: show if url == "https://feed/x" else None)
    return show


def test_a_podcast_episode(ctx, pods):
    from urllib.parse import quote
    status, body = get(ctx, "podcast_episode/" + quote("6:https://ep/42", safe=""))
    assert status == 200 and body["title"] == "Ep 42" and body["subtitle"] == "The Show · Nov 2, 2026"
    assert ids(body) == ["details", "ids", "file", "audio", "source"]
    d = values(body, "details")
    assert d["Show"] == "The Show" and d["Episode"] == "Season 2, Episode 42" and d["Runtime"] == "50m"
    assert values(body, "ids") == {"GUID": "https://ep/42"}
    f = values(body, "file")
    assert f["File name"] == "42.mp3" and f["Overall bitrate"] == "128 kbps" and "Path" not in f
    assert values(body, "source")["From"] == "Your library"
    # the key keeps its slashes unencoded too
    assert get(ctx, "podcast_episode/6:https://ep/42")[0] == 200
    assert get(ctx, "podcast_episode/6:https://ep/nope")[0] == 404
    assert get(ctx, "podcast_episode/7:https://ep/42")[0] == 404  # not followed
    ctx["perms"] = {"music.access"}
    assert get(ctx, "podcast_episode/6:https://ep/42")[1]["error"] == "not_allowed"


def test_unknown_kinds_and_ids(ctx):
    assert get(ctx, "hologram/1") == (400, {"success": False, "error": "unknown_kind",
                                            "message": "There's no info for that kind of thing."})


# ── words and the probe ──────────────────────────────────────────────────

def test_values_read_as_words():
    assert I.size_text(1536) == "1 KB" and I.size_text(0) is None
    assert I.clock(7509) == "2:05:09" and I.clock(225) == "3:45"
    assert I.runtime(7020) == "1h 57m" and I.runtime(3600) == "1h" and I.runtime(42) == "42s"
    assert I.runtime(213) == "3m 33s" and I.runtime(1500) == "25m"
    assert I.bitrate_text(320000) == "320 kbps" and I.bitrate_text(12_400_000) == "12.4 Mbps"
    assert I.date_text("2026-11-03T21:05:00", with_time=True) == "Nov 3, 2026, 9:05 PM"
    assert I.languages("ja,en,und") == "Japanese, English" and I.languages('["fr"]') == "French"
    assert I.row("Empty", "") is None and I.row("Zero", 0) == {"label": "Zero", "value": "0"}


def test_the_probe_keeps_picture_facts():
    t = K.parse_probe({"streams": [
        {"codec_type": "video", "codec_name": "hevc", "width": 3840, "height": 2160, "avg_frame_rate": "25/1",
         "pix_fmt": "yuv420p10le", "color_transfer": "arib-std-b67", "bit_rate": "40000000"},
        {"codec_type": "video", "codec_name": "mjpeg", "disposition": {"attached_pic": 1}},
        {"codec_type": "subtitle", "codec_name": "subrip", "disposition": {"hearing_impaired": 1}},
    ], "format": {"bit_rate": "41000000", "duration": "5400.5"}})
    assert t["video"] == [{"codec": "hevc", "profile": None, "width": 3840, "height": 2160, "fps": 25.0,
                           "bit_depth": 10, "transfer": "arib-std-b67", "bitrate": 40000000}]
    assert t["format"]["bitrate"] == 41000000 and t["format"]["duration"] == 5400.5
    assert t["subtitles"][0]["sdh"] is True
