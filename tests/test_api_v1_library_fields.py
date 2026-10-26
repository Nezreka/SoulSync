"""API v1 library: enriched metadata fields, opaque IDs, wider lookup.

The library tables already store Discogs/Bandcamp/Amazon/JioSaavn metadata,
canonical-release picks, per-track paths and user locks — the v1 serializers
just never let them out, the detail routes 400'd on any non-integer ID even
though IDs are TEXT ('...::soulsync' IDs are valid standalone IDs), and
/library/lookup only knew eight providers.

These pin:
  * the new fields serialize (JSON-backed ones as real lists, locks as
    booleans, rows without the columns serialize clean instead of raising);
  * every pre-existing field keeps its exact key/value (additive only);
  * opaque and numeric-string IDs both work on the detail/child routes and
    the albums artist_id filter;
  * lookup covers discogs/amazon/jiosaavn/bandcamp, keeps the genius/album
    400, and adds the mirror bandcamp/artist 400.
"""

import sys
import types
from unittest.mock import patch

import pytest


def _install_flask_limiter_stub():
    if "flask_limiter" in sys.modules:
        return
    stub = types.ModuleType("flask_limiter")

    class _Limiter:
        def __init__(self, *args, **kwargs):
            pass

        def limit(self, *args, **kwargs):
            def decorator(target):
                return target
            return decorator

        def init_app(self, app):
            pass

    stub.Limiter = _Limiter
    sys.modules["flask_limiter"] = stub
    util_stub = types.ModuleType("flask_limiter.util")
    util_stub.get_remote_address = lambda: "127.0.0.1"
    sys.modules["flask_limiter.util"] = util_stub


_install_flask_limiter_stub()

from flask import Blueprint, Flask  # noqa: E402

from api import library as library_mod  # noqa: E402
from api.serializers import serialize_album, serialize_artist, serialize_track  # noqa: E402
from database.music_database import MusicDatabase  # noqa: E402


# ── Serializer unit tests ─────────────────────────────────────

ARTIST_ROW = {
    "id": "ar1::soulsync",
    "name": "The Testers",
    "genres": '["Rock"]',
    "soul_id": "soul-ar1",
    "soul_id_path": "canonical",
    "aliases": '["The Testers UK", "Testers"]',
    "discogs_id": "111",
    "discogs_bio": "A band from nowhere.",
    "discogs_members": '["Ann A", "Bob B"]',
    "discogs_urls": '["https://discogs.example/artist/111"]',
    "discogs_match_status": "matched",
    "discogs_last_attempted": "2026-01-02 03:04:05",
    "amazon_id": "amz-ar",
    "amazon_match_status": "matched",
    "amazon_last_attempted": "2026-01-03 00:00:00",
    "jiosaavn_id": "jio-ar",
    "jiosaavn_match_status": "not_found",
    "jiosaavn_last_attempted": "2026-01-04 00:00:00",
    "art_locked": 1,
    "genres_locked": 0,
}

ALBUM_ROW = {
    "id": "al1::soulsync",
    "artist_id": "ar1::soulsync",
    "title": "First Record",
    "year": 1999,
    "release_date": "1999-05-04",
    "track_count": 2,
    "api_track_count": 12,
    "discogs_id": "222",
    "discogs_genres": '["Rock", "Pop"]',
    "discogs_styles": '["Indie Rock"]',
    "discogs_label": "Test Label",
    "discogs_catno": "TL-001",
    "discogs_country": "US",
    "discogs_rating": 4.5,
    "discogs_rating_count": 37,
    "discogs_match_status": "matched",
    "discogs_last_attempted": "2026-01-05 00:00:00",
    "amazon_id": "amz-al",
    "amazon_match_status": "matched",
    "amazon_last_attempted": "2026-01-06 00:00:00",
    "jiosaavn_id": "jio-al",
    "jiosaavn_match_status": "matched",
    "jiosaavn_last_attempted": "2026-01-07 00:00:00",
    "bandcamp_id": "bc-al",
    "bandcamp_url": "https://testers.bandcamp.com/album/first-record",
    "bandcamp_tags": '["indie", "rock"]',
    "bandcamp_label": "BC Label",
    "bandcamp_match_status": "matched",
    "bandcamp_last_attempted": "2026-01-08 00:00:00",
    "canonical_source": "discogs",
    "canonical_album_id": "222",
    "canonical_score": 0.93,
    "canonical_resolved_at": "2026-02-01 00:00:00",
    "canonical_locked": 1,
    "canonical_track_count": 12,
    "art_locked": 0,
    "genres_locked": 1,
    "metadata_locked": 0,
}

TRACK_ROW = {
    "id": "tr1::soulsync",
    "album_id": "al1::soulsync",
    "artist_id": "ar1::soulsync",
    "title": "Song One",
    "track_artist": "The Testers feat. Guest",
    "track_number": 1,
    "disc_number": 2,
    "duration": 123000,
    "year": 1999,
    "file_path": "/Media/Music/song-one.flac",
    "server_path": "/music/song-one.flac",
    "file_size": 12345678,
    "recording_disambiguation": "live",
    "amazon_id": "amz-tr",
    "amazon_match_status": "matched",
    "amazon_last_attempted": "2026-01-09 00:00:00",
    "jiosaavn_id": "jio-tr",
    "jiosaavn_match_status": "matched",
    "jiosaavn_last_attempted": "2026-01-10 00:00:00",
    "bandcamp_id": "bc-tr",
    "bandcamp_url": "https://testers.bandcamp.com/track/song-one",
    "bandcamp_tags": '["indie"]',
    "bandcamp_label": "BC Label",
    "bandcamp_match_status": "matched",
    "bandcamp_last_attempted": "2026-01-11 00:00:00",
    "metadata_locked": 1,
}


def test_serialize_artist_new_fields():
    out = serialize_artist(dict(ARTIST_ROW))
    assert out["aliases"] == ["The Testers UK", "Testers"]
    assert out["soul_id_path"] == "canonical"
    assert out["discogs_id"] == "111"
    assert out["discogs_bio"] == "A band from nowhere."
    assert out["discogs_members"] == ["Ann A", "Bob B"]
    assert out["discogs_urls"] == ["https://discogs.example/artist/111"]
    assert out["discogs_match_status"] == "matched"
    assert out["discogs_last_attempted"] == "2026-01-02 03:04:05"
    assert out["amazon_id"] == "amz-ar"
    assert out["amazon_match_status"] == "matched"
    assert out["amazon_last_attempted"] == "2026-01-03 00:00:00"
    assert out["jiosaavn_id"] == "jio-ar"
    assert out["jiosaavn_match_status"] == "not_found"
    assert out["jiosaavn_last_attempted"] == "2026-01-04 00:00:00"
    # Lock columns are INTEGER in the DB and booleans on the wire.
    assert out["art_locked"] is True
    assert out["genres_locked"] is False


def test_serialize_album_new_fields():
    out = serialize_album(dict(ALBUM_ROW))
    assert out["release_date"] == "1999-05-04"
    assert out["discogs_genres"] == ["Rock", "Pop"]
    assert out["discogs_styles"] == ["Indie Rock"]
    assert out["discogs_label"] == "Test Label"
    assert out["discogs_catno"] == "TL-001"
    assert out["discogs_country"] == "US"
    assert out["discogs_rating"] == 4.5
    assert out["discogs_rating_count"] == 37
    assert out["api_track_count"] == 12
    assert out["track_count"] == 2  # observed count stays its own field
    assert out["canonical_track_count"] == 12
    assert out["canonical_source"] == "discogs"
    assert out["canonical_album_id"] == "222"
    assert out["canonical_score"] == 0.93
    assert out["canonical_resolved_at"] == "2026-02-01 00:00:00"
    assert out["canonical_locked"] is True
    assert out["discogs_id"] == "222"
    assert out["amazon_id"] == "amz-al"
    assert out["jiosaavn_id"] == "jio-al"
    assert out["bandcamp_id"] == "bc-al"
    assert out["bandcamp_url"] == "https://testers.bandcamp.com/album/first-record"
    assert out["bandcamp_tags"] == ["indie", "rock"]
    assert out["bandcamp_label"] == "BC Label"
    assert out["discogs_match_status"] == "matched"
    assert out["amazon_match_status"] == "matched"
    assert out["jiosaavn_match_status"] == "matched"
    assert out["bandcamp_match_status"] == "matched"
    assert out["discogs_last_attempted"] == "2026-01-05 00:00:00"
    assert out["amazon_last_attempted"] == "2026-01-06 00:00:00"
    assert out["jiosaavn_last_attempted"] == "2026-01-07 00:00:00"
    assert out["bandcamp_last_attempted"] == "2026-01-08 00:00:00"
    assert out["art_locked"] is False
    assert out["genres_locked"] is True
    assert out["metadata_locked"] is False


def test_serialize_track_new_fields():
    out = serialize_track(dict(TRACK_ROW))
    assert out["disc_number"] == 2
    assert out["year"] == 1999
    assert out["track_artist"] == "The Testers feat. Guest"
    assert out["recording_disambiguation"] == "live"
    assert out["server_path"] == "/music/song-one.flac"
    assert out["file_path"] == "/Media/Music/song-one.flac"
    assert out["file_size"] == 12345678
    assert out["amazon_id"] == "amz-tr"
    assert out["jiosaavn_id"] == "jio-tr"
    assert out["bandcamp_id"] == "bc-tr"
    assert out["bandcamp_url"] == "https://testers.bandcamp.com/track/song-one"
    assert out["bandcamp_tags"] == ["indie"]
    assert out["bandcamp_label"] == "BC Label"
    assert out["amazon_match_status"] == "matched"
    assert out["jiosaavn_match_status"] == "matched"
    assert out["bandcamp_match_status"] == "matched"
    assert out["amazon_last_attempted"] == "2026-01-09 00:00:00"
    assert out["jiosaavn_last_attempted"] == "2026-01-10 00:00:00"
    assert out["bandcamp_last_attempted"] == "2026-01-11 00:00:00"
    assert out["metadata_locked"] is True


def test_serialize_rows_without_new_columns_do_not_raise():
    artist = serialize_artist({"id": "1", "name": "Bare"})
    assert artist["aliases"] == []
    assert artist["discogs_members"] == []
    assert artist["discogs_urls"] == []
    assert artist["discogs_id"] is None
    assert artist["soul_id_path"] is None
    assert artist["art_locked"] is None
    assert artist["genres_locked"] is None

    album = serialize_album({"id": "2", "title": "Bare"})
    assert album["discogs_genres"] == []
    assert album["discogs_styles"] == []
    assert album["bandcamp_tags"] == []
    assert album["release_date"] is None
    assert album["canonical_locked"] is None
    assert album["metadata_locked"] is None

    track = serialize_track({"id": "3", "title": "Bare"})
    assert track["bandcamp_tags"] == []
    assert track["server_path"] is None
    assert track["file_size"] is None
    assert track["recording_disambiguation"] is None
    assert track["metadata_locked"] is None


def test_serialize_list_fields_accept_lists_and_reject_garbage():
    # Already-a-list values pass through untouched.
    out = serialize_artist({"aliases": ["A", "B"]})
    assert out["aliases"] == ["A", "B"]
    # Valid JSON of the wrong shape is not a list — [] rather than leaking it.
    out = serialize_album({"bandcamp_tags": '{"not": "a list"}'})
    assert out["bandcamp_tags"] == []
    out = serialize_track({"bandcamp_tags": "42"})
    assert out["bandcamp_tags"] == []
    # Broken JSON must not raise either (the _parse_genres tolerance).
    out = serialize_artist({"discogs_members": "{{{garbage"})
    assert isinstance(out["discogs_members"], list)


def test_fields_filter_can_select_new_fields():
    out = serialize_artist(dict(ARTIST_ROW), fields={"aliases", "discogs_id"})
    assert out == {"aliases": ["The Testers UK", "Testers"], "discogs_id": "111"}
    out = serialize_album(dict(ALBUM_ROW), fields={"release_date", "canonical_locked"})
    assert out == {"release_date": "1999-05-04", "canonical_locked": True}
    out = serialize_track(dict(TRACK_ROW), fields={"server_path", "file_size"})
    assert out == {"server_path": "/music/song-one.flac", "file_size": 12345678}


# ── Regression: pre-existing key sets unchanged, values spot-checked ──

_OLD_ARTIST_KEYS = {
    "id", "name", "thumb_url", "banner_url", "genres", "summary", "style",
    "mood", "label", "server_source", "created_at", "updated_at", "soul_id",
    "musicbrainz_id", "spotify_artist_id", "itunes_artist_id", "audiodb_id",
    "deezer_id", "tidal_id", "qobuz_id", "genius_id",
    "musicbrainz_match_status", "spotify_match_status", "itunes_match_status",
    "audiodb_match_status", "deezer_match_status", "lastfm_match_status",
    "genius_match_status", "tidal_match_status", "qobuz_match_status",
    "musicbrainz_last_attempted", "spotify_last_attempted",
    "itunes_last_attempted", "audiodb_last_attempted", "deezer_last_attempted",
    "lastfm_last_attempted", "genius_last_attempted", "tidal_last_attempted",
    "qobuz_last_attempted",
    "lastfm_listeners", "lastfm_playcount", "lastfm_tags", "lastfm_similar",
    "lastfm_bio", "lastfm_url",
    "genius_description", "genius_alt_names", "genius_url",
}

_NEW_ARTIST_KEYS = {
    "soul_id_path", "aliases", "discogs_bio", "discogs_members", "discogs_urls",
    "discogs_id", "amazon_id", "jiosaavn_id",
    "discogs_match_status", "amazon_match_status", "jiosaavn_match_status",
    "discogs_last_attempted", "amazon_last_attempted", "jiosaavn_last_attempted",
    "art_locked", "genres_locked",
}

_OLD_ALBUM_KEYS = {
    "id", "artist_id", "title", "year", "thumb_url", "genres", "track_count",
    "duration", "style", "mood", "label", "explicit", "record_type",
    "server_source", "created_at", "updated_at", "upc", "copyright", "soul_id",
    "musicbrainz_release_id", "spotify_album_id", "itunes_album_id",
    "audiodb_id", "deezer_id", "tidal_id", "qobuz_id",
    "musicbrainz_match_status", "spotify_match_status", "itunes_match_status",
    "audiodb_match_status", "deezer_match_status", "lastfm_match_status",
    "tidal_match_status", "qobuz_match_status",
    "musicbrainz_last_attempted", "spotify_last_attempted",
    "itunes_last_attempted", "audiodb_last_attempted", "deezer_last_attempted",
    "lastfm_last_attempted", "tidal_last_attempted", "qobuz_last_attempted",
    "lastfm_listeners", "lastfm_playcount", "lastfm_tags", "lastfm_wiki",
    "lastfm_url",
}

_NEW_ALBUM_KEYS = {
    "release_date", "api_track_count",
    "discogs_genres", "discogs_styles", "discogs_label", "discogs_catno",
    "discogs_country", "discogs_rating", "discogs_rating_count",
    "canonical_track_count", "canonical_source", "canonical_album_id",
    "canonical_score", "canonical_resolved_at", "canonical_locked",
    "discogs_id", "amazon_id", "jiosaavn_id",
    "bandcamp_id", "bandcamp_url", "bandcamp_tags", "bandcamp_label",
    "discogs_match_status", "amazon_match_status", "jiosaavn_match_status",
    "bandcamp_match_status",
    "discogs_last_attempted", "amazon_last_attempted",
    "jiosaavn_last_attempted", "bandcamp_last_attempted",
    "art_locked", "genres_locked", "metadata_locked",
    "musicbrainz_release_group_id",
}

_OLD_TRACK_KEYS = {
    "id", "album_id", "artist_id", "title", "track_number", "duration",
    "file_path", "bitrate", "bpm", "explicit", "style", "mood", "repair_status",
    "repair_last_checked", "server_source", "created_at", "updated_at",
    "isrc", "copyright", "soul_id", "album_soul_id",
    "musicbrainz_recording_id", "spotify_track_id", "itunes_track_id",
    "audiodb_id", "deezer_id", "tidal_id", "qobuz_id", "genius_id",
    "musicbrainz_match_status", "spotify_match_status", "itunes_match_status",
    "audiodb_match_status", "deezer_match_status", "lastfm_match_status",
    "genius_match_status", "tidal_match_status", "qobuz_match_status",
    "musicbrainz_last_attempted", "spotify_last_attempted",
    "itunes_last_attempted", "audiodb_last_attempted", "deezer_last_attempted",
    "lastfm_last_attempted", "genius_last_attempted", "tidal_last_attempted",
    "qobuz_last_attempted",
    "lastfm_listeners", "lastfm_playcount", "lastfm_tags", "lastfm_url",
    "genius_lyrics", "genius_description", "genius_url",
}

_NEW_TRACK_KEYS = {
    "track_artist", "disc_number", "year", "server_path", "file_size",
    "recording_disambiguation",
    "amazon_id", "jiosaavn_id", "bandcamp_id", "bandcamp_url",
    "bandcamp_tags", "bandcamp_label",
    "amazon_match_status", "jiosaavn_match_status", "bandcamp_match_status",
    "amazon_last_attempted", "jiosaavn_last_attempted", "bandcamp_last_attempted",
    "metadata_locked",
}


def test_regression_old_fields_unchanged_artist():
    row = {"id": "7", "name": "Old Row", "genres": '["Jazz"]',
           "musicbrainz_id": "mb-7", "lastfm_bio": "bio"}
    out = serialize_artist(row)
    assert set(out) == _OLD_ARTIST_KEYS | _NEW_ARTIST_KEYS
    assert out["id"] == "7"
    assert out["name"] == "Old Row"
    assert out["genres"] == ["Jazz"]
    assert out["musicbrainz_id"] == "mb-7"
    assert out["lastfm_bio"] == "bio"
    assert out["thumb_url"] is None
    assert out["created_at"] is None


def test_regression_old_fields_unchanged_album():
    row = {"id": "8", "artist_id": "7", "title": "Old Album", "year": 1980,
           "explicit": 1, "track_count": 9}
    out = serialize_album(row)
    assert set(out) == _OLD_ALBUM_KEYS | _NEW_ALBUM_KEYS
    assert out["title"] == "Old Album"
    assert out["year"] == 1980
    assert out["explicit"] is True
    assert out["track_count"] == 9
    assert out["genres"] == []


def test_regression_old_fields_unchanged_track():
    row = {"id": "9", "album_id": "8", "artist_id": "7", "title": "Old Track",
           "duration": 1000, "file_path": "/x.flac", "genius_lyrics": "la"}
    out = serialize_track(row)
    assert set(out) == _OLD_TRACK_KEYS | _NEW_TRACK_KEYS
    assert out["title"] == "Old Track"
    assert out["duration"] == 1000
    assert out["file_path"] == "/x.flac"
    assert out["genius_lyrics"] == "la"
    assert out["explicit"] is None


# ── Route tests against a real (temp) database ───────────────


def _seed(db):
    conn = db._get_connection()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO artists (id, name, genres, server_source, soul_id,
               soul_id_path, aliases, discogs_id, discogs_bio, discogs_members,
               discogs_urls, discogs_match_status, discogs_last_attempted,
               amazon_id, amazon_match_status, amazon_last_attempted,
               jiosaavn_id, jiosaavn_match_status, jiosaavn_last_attempted,
               art_locked, genres_locked)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("ar1::soulsync", "The Testers", '["Rock"]', "plex", "soul-ar1",
         "canonical", '["The Testers UK", "Testers"]', "111",
         "A band from nowhere.", '["Ann A", "Bob B"]',
         '["https://discogs.example/artist/111"]', "matched",
         "2026-01-02 03:04:05", "amz-ar", "matched", "2026-01-03 00:00:00",
         "jio-ar", "not_found", "2026-01-04 00:00:00", 1, 0),
    )
    cur.execute(
        """INSERT INTO artists (id, name, server_source)
           VALUES (?, ?, ?)""",
        ("42", "Numeric Artist", "plex"),
    )
    cur.execute(
        """INSERT INTO albums (id, artist_id, title, year, release_date,
               track_count, api_track_count, server_source, discogs_id,
               discogs_genres, discogs_styles, discogs_label, discogs_catno,
               discogs_country, discogs_rating, discogs_rating_count,
               discogs_match_status, discogs_last_attempted, amazon_id,
               amazon_match_status, amazon_last_attempted, jiosaavn_id,
               jiosaavn_match_status, jiosaavn_last_attempted, bandcamp_id,
               bandcamp_url, bandcamp_tags, bandcamp_label,
               bandcamp_match_status, bandcamp_last_attempted,
               canonical_source, canonical_album_id, canonical_score,
               canonical_resolved_at, canonical_locked, canonical_track_count,
               art_locked, genres_locked, metadata_locked)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                   ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("al1::soulsync", "ar1::soulsync", "First Record", 1999, "1999-05-04",
         2, 12, "plex", "222", '["Rock", "Pop"]', '["Indie Rock"]',
         "Test Label", "TL-001", "US", 4.5, 37, "matched",
         "2026-01-05 00:00:00", "amz-al", "matched", "2026-01-06 00:00:00",
         "jio-al", "matched", "2026-01-07 00:00:00", "bc-al",
         "https://testers.bandcamp.com/album/first-record",
         '["indie", "rock"]', "BC Label", "matched", "2026-01-08 00:00:00",
         "discogs", "222", 0.93, "2026-02-01 00:00:00", 1, 12, 0, 1, 0),
    )
    cur.execute(
        """INSERT INTO albums (id, artist_id, title, server_source)
           VALUES (?, ?, ?, ?)""",
        ("77", "42", "Numeric Album", "plex"),
    )
    cur.execute(
        """INSERT INTO tracks (id, album_id, artist_id, title, track_artist,
               track_number, disc_number, duration, year, file_path,
               server_path, file_size, server_source,
               recording_disambiguation, amazon_id, amazon_match_status,
               amazon_last_attempted, jiosaavn_id, jiosaavn_match_status,
               jiosaavn_last_attempted, bandcamp_id, bandcamp_url,
               bandcamp_tags, bandcamp_label, bandcamp_match_status,
               bandcamp_last_attempted, metadata_locked)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                   ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("tr1::soulsync", "al1::soulsync", "ar1::soulsync", "Song One",
         "The Testers feat. Guest", 1, 2, 123000, 1999,
         "/Media/Music/song-one.flac", "/music/song-one.flac", 12345678,
         "plex", "live", "amz-tr", "matched", "2026-01-09 00:00:00",
         "jio-tr", "matched", "2026-01-10 00:00:00", "bc-tr",
         "https://testers.bandcamp.com/track/song-one", '["indie"]',
         "BC Label", "matched", "2026-01-11 00:00:00", 1),
    )
    cur.execute(
        """INSERT INTO tracks (id, album_id, artist_id, title, server_source)
           VALUES (?, ?, ?, ?, ?)""",
        ("99", "77", "42", "Numeric Song", "plex"),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(database_path=str(tmp_path / "music.db"))


@pytest.fixture
def client(db, monkeypatch):
    _seed(db)
    monkeypatch.setattr(library_mod, "get_database", lambda: db)
    app = Flask(__name__)
    bp = Blueprint("v1", __name__, url_prefix="/api/v1")
    with patch.object(library_mod, "require_api_key", lambda f: f):
        library_mod.register_routes(bp)
    app.register_blueprint(bp)
    return app.test_client()


def test_artist_detail_accepts_opaque_id(client):
    resp = client.get("/api/v1/library/artists/ar1::soulsync")
    assert resp.status_code == 200, resp.get_json()
    data = resp.get_json()["data"]
    assert data["artist"]["discogs_bio"] == "A band from nowhere."
    assert data["artist"]["aliases"] == ["The Testers UK", "Testers"]
    assert data["artist"]["art_locked"] is True
    assert [a["id"] for a in data["albums"]] == ["al1::soulsync"]
    assert data["albums"][0]["release_date"] == "1999-05-04"


def test_artist_albums_accepts_opaque_id(client):
    resp = client.get("/api/v1/library/artists/ar1::soulsync/albums")
    assert resp.status_code == 200, resp.get_json()
    albums = resp.get_json()["data"]["albums"]
    assert len(albums) == 1
    assert albums[0]["canonical_source"] == "discogs"
    assert albums[0]["bandcamp_tags"] == ["indie", "rock"]


def test_album_detail_accepts_opaque_id(client):
    resp = client.get("/api/v1/library/albums/al1::soulsync")
    assert resp.status_code == 200, resp.get_json()
    data = resp.get_json()["data"]
    assert data["album"]["discogs_rating"] == 4.5
    assert data["album"]["canonical_locked"] is True
    assert [t["id"] for t in data["tracks"]] == ["tr1::soulsync"]
    assert data["tracks"][0]["server_path"] == "/music/song-one.flac"


def test_album_tracks_accepts_opaque_id(client):
    resp = client.get("/api/v1/library/albums/al1::soulsync/tracks")
    assert resp.status_code == 200, resp.get_json()
    tracks = resp.get_json()["data"]["tracks"]
    assert len(tracks) == 1
    assert tracks[0]["recording_disambiguation"] == "live"
    assert tracks[0]["file_size"] == 12345678


def test_track_detail_accepts_opaque_id(client):
    resp = client.get("/api/v1/library/tracks/tr1::soulsync")
    assert resp.status_code == 200, resp.get_json()
    track = resp.get_json()["data"]["track"]
    assert track["track_artist"] == "The Testers feat. Guest"
    assert track["disc_number"] == 2
    assert track["metadata_locked"] is True


def test_numeric_string_ids_still_work(client):
    assert client.get("/api/v1/library/artists/42").status_code == 200
    assert client.get("/api/v1/library/artists/42/albums").status_code == 200
    assert client.get("/api/v1/library/albums/77").status_code == 200
    assert client.get("/api/v1/library/albums/77/tracks").status_code == 200
    assert client.get("/api/v1/library/tracks/99").status_code == 200


def test_unknown_opaque_id_is_404_not_400(client):
    resp = client.get("/api/v1/library/artists/nope::soulsync")
    assert resp.status_code == 404
    resp = client.get("/api/v1/library/tracks/nope::soulsync")
    assert resp.status_code == 404


def test_albums_artist_id_filter_accepts_opaque_id(client):
    resp = client.get("/api/v1/library/albums?artist_id=ar1::soulsync")
    assert resp.status_code == 200, resp.get_json()
    albums = resp.get_json()["data"]["albums"]
    assert [a["id"] for a in albums] == ["al1::soulsync"]

    resp = client.get("/api/v1/library/albums?artist_id=42")
    assert resp.status_code == 200, resp.get_json()
    albums = resp.get_json()["data"]["albums"]
    assert [a["id"] for a in albums] == ["77"]


def test_artist_list_carries_new_fields(client):
    resp = client.get("/api/v1/library/artists")
    assert resp.status_code == 200, resp.get_json()
    artists = {a["id"]: a for a in resp.get_json()["data"]["artists"]}
    artist = artists["ar1::soulsync"]
    assert artist["aliases"] == ["The Testers UK", "Testers"]
    assert artist["soul_id_path"] == "canonical"
    assert artist["discogs_bio"] == "A band from nowhere."
    assert artist["discogs_members"] == ["Ann A", "Bob B"]
    assert artist["jiosaavn_id"] == "jio-ar"
    assert artist["discogs_match_status"] == "matched"
    assert artist["art_locked"] is True
    assert artist["genres_locked"] is False


# ── Lookup ────────────────────────────────────────────────────


@pytest.mark.parametrize("entity,provider,ext_id,expect_id", [
    ("artist", "discogs", "111", "ar1::soulsync"),
    ("album", "discogs", "222", "al1::soulsync"),
    ("album", "amazon", "amz-al", "al1::soulsync"),
    ("track", "amazon", "amz-tr", "tr1::soulsync"),
    ("artist", "jiosaavn", "jio-ar", "ar1::soulsync"),
    ("track", "jiosaavn", "jio-tr", "tr1::soulsync"),
    ("album", "bandcamp", "bc-al", "al1::soulsync"),
    ("track", "bandcamp", "bc-tr", "tr1::soulsync"),
])
def test_lookup_new_providers(client, entity, provider, ext_id, expect_id):
    resp = client.get(
        f"/api/v1/library/lookup?type={entity}&provider={provider}&id={ext_id}")
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["data"][entity]["id"] == expect_id


def test_lookup_bandcamp_artist_is_400(client):
    resp = client.get("/api/v1/library/lookup?type=artist&provider=bandcamp&id=bc-al")
    assert resp.status_code == 400
    assert "Bandcamp" in resp.get_json()["error"]["message"]


def test_lookup_genius_album_still_400(client):
    resp = client.get("/api/v1/library/lookup?type=album&provider=genius&id=x")
    assert resp.status_code == 400


def test_lookup_discogs_track_is_400(client):
    # Tracks carry no Discogs ID (enrichment is artist/album level), so the
    # combination is rejected like genius/album and bandcamp/artist.
    resp = client.get("/api/v1/library/lookup?type=track&provider=discogs&id=222")
    assert resp.status_code == 400
    assert "Discogs" in resp.get_json()["error"]["message"]


def test_albums_artist_id_empty_means_no_filter(client):
    # An empty/blank artist_id was "no filter" under the old int()
    # conversion (falsy -> None); it must not become "matches nothing".
    for raw in ("", "%20"):
        resp = client.get(f"/api/v1/library/albums?artist_id={raw}")
        assert resp.status_code == 200, resp.get_json()
        ids = {a["id"] for a in resp.get_json()["data"]["albums"]}
        assert ids == {"al1::soulsync", "77"}
