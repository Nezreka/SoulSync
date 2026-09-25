import os
import json
import sqlite3
from types import SimpleNamespace

import pytest

from core.imports import side_effects
from core.library2.schema import ensure_library_v2_schema


class _FakeDB:
    def __init__(self, conn):
        self._conn = conn

    def _get_connection(self):
        return self._conn


def _make_soulsync_db():
    """The catalogue, as every other path sees it. SoulSync-as-media-server is
    just another `server_source`, and its stable hash ids are its server ids
    (§50.4.4.29)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_library_v2_schema(conn)
    conn.commit()
    return conn


def _artist_row(conn):
    return conn.execute("SELECT * FROM lib2_artists").fetchone()


def _album_row(conn):
    return conn.execute("SELECT * FROM lib2_albums").fetchone()


def _track_row(conn):
    return conn.execute(
        "SELECT t.*, f.path AS file_path, f.size AS file_size, f.bitrate AS bitrate"
        "  FROM lib2_tracks t"
        "  LEFT JOIN lib2_track_files f ON f.track_id = t.id AND f.is_primary = 1"
    ).fetchone()


def _external(row, provider):
    return json.loads(row["external_ids"] or "{}").get(provider)


def test_record_soulsync_library_entry_writes_artist_album_and_track(tmp_path, monkeypatch):
    conn = _make_soulsync_db()
    fake_db = _FakeDB(conn)
    final_path = tmp_path / "track.flac"
    final_path.write_bytes(b"audio")

    monkeypatch.setattr(side_effects, "get_database", lambda: fake_db)
    monkeypatch.setattr(
        side_effects,
        "_get_config_manager",
        lambda: SimpleNamespace(get_active_media_server=lambda: "soulsync"),
    )

    import core.genre_filter as genre_filter

    monkeypatch.setattr(genre_filter, "filter_genres", lambda genres, _cfg: [genre.upper() for genre in genres])

    context = {
        "source": "spotify",
        "artist": {"id": "sp-artist", "name": "Artist One"},
        "album": {
            "id": "sp-album",
            "name": "Album One",
            "release_date": "2024-02-03",
            "total_tracks": 12,
            "image_url": "https://img.example/album.jpg",
        },
        "track_info": {
            "id": "sp-track",
            "name": "Song One",
            "track_number": 7,
            "duration_ms": 210000,
            "artists": [{"name": "Guest Artist"}],
            "_source": "spotify",
        },
        "original_search_result": {
            "title": "Song One",
            "artists": [{"name": "Guest Artist"}],
            "_source": "spotify",
        },
        "_final_processed_path": str(final_path),
    }

    artist_context = {"name": "Artist One", "genres": ["rock", "indie"]}
    album_info = {"is_album": True, "album_name": "Album One", "track_number": 7}

    side_effects.record_soulsync_library_entry(context, artist_context, album_info)

    artist_row = _artist_row(conn)
    album_row = _album_row(conn)
    track_row = _track_row(conn)

    assert artist_row["name"] == "Artist One"
    assert artist_row["server_source"] == "soulsync"
    assert artist_row["spotify_id"] == "sp-artist"
    assert artist_row["genres"] == '["ROCK", "INDIE"]'

    assert album_row["title"] == "Album One"
    assert album_row["server_source"] == "soulsync"
    assert album_row["spotify_id"] == "sp-album"
    assert album_row["year"] == 2024
    assert album_row["track_count"] == 12
    assert album_row["duration"] == 210000
    assert album_row["primary_artist_id"] == artist_row["id"]

    assert track_row["title"] == "Song One"
    assert track_row["server_source"] == "soulsync"
    assert track_row["spotify_id"] == "sp-track"
    assert track_row["track_number"] == 7
    assert track_row["duration"] == 210000
    assert track_row["track_artist"] == "Guest Artist"
    assert track_row["album_id"] == album_row["id"]
    assert track_row["file_path"] == str(final_path)
    # File size in bytes — populates the Library Disk Usage card on Stats.
    # Read via os.path.getsize at insert time since SoulSync standalone is
    # the only flow where the file is local at the moment we write the row.
    assert track_row["file_size"] == os.path.getsize(str(final_path))
    # No override on this item — v2 says so with `quality_profile_explicit=0`
    # ("follow the app-wide default at read time"), the id column being a plain
    # pointer that always has a value.
    assert track_row["quality_profile_explicit"] == 0


def test_record_soulsync_library_entry_estimates_opus_bitrate(tmp_path, monkeypatch):
    """mutagen.oggopus has no bitrate field. The library enhanced table was
    storing 0 and rendering a red dash, while completed-download chips
    already estimated from size/duration."""
    conn = _make_soulsync_db()
    fake_db = _FakeDB(conn)
    final_path = tmp_path / "Autumnal Embrace.opus"
    final_path.write_bytes(b"x" * 40_000)

    class _Info:
        length = 2.0
        channels = 2

    class _Opus:
        def __init__(self, _p, easy=False):
            self.info = _Info()
            self.tags = None

    monkeypatch.setattr(side_effects, "get_database", lambda: fake_db)
    monkeypatch.setattr(
        side_effects,
        "_get_config_manager",
        lambda: SimpleNamespace(get_active_media_server=lambda: "soulsync"),
    )
    monkeypatch.setattr("mutagen.File", _Opus)

    import core.genre_filter as genre_filter

    monkeypatch.setattr(genre_filter, "filter_genres", lambda genres, _cfg: genres)

    context = {
        "source": "spotify",
        "artist": {"id": "sp-artist", "name": "Skyforest"},
        "album": {"id": "sp-album", "name": "Autumn's Dawn"},
        "track_info": {
            "id": "sp-track",
            "name": "Autumnal Embrace",
            "track_number": 3,
            "duration_ms": 2000,
            "_source": "spotify",
        },
        "original_search_result": {"title": "Autumnal Embrace", "_source": "spotify"},
        "_final_processed_path": str(final_path),
    }

    side_effects.record_soulsync_library_entry(
        context, {"name": "Skyforest", "genres": []}, {"is_album": True, "album_name": "Autumn's Dawn"},
    )

    track_row = _track_row(conn)
    assert track_row["bitrate"] == 160


def test_record_soulsync_library_entry_persists_quality_profile_id(tmp_path, monkeypatch):
    """Whatever the pipeline resolved for this item (a wishlist row's or
    Auto-Import's own profile override) must land on the library track row —
    otherwise a later Quality Check / Quality Upgrade Finder pass re-judges
    the track against the default profile instead of the one it was actually
    imported under (see `_resolve_context_quality_profile` in
    core/imports/pipeline.py)."""
    conn = _make_soulsync_db()
    conn.execute("INSERT INTO quality_profiles(id, name, ranked_targets)"
                 " VALUES(42, 'Imported Under This', '[]')")
    conn.commit()
    fake_db = _FakeDB(conn)
    final_path = tmp_path / "track.flac"
    final_path.write_bytes(b"audio")

    monkeypatch.setattr(side_effects, "get_database", lambda: fake_db)
    monkeypatch.setattr(
        side_effects,
        "_get_config_manager",
        lambda: SimpleNamespace(get_active_media_server=lambda: "soulsync"),
    )

    import core.genre_filter as genre_filter

    monkeypatch.setattr(genre_filter, "filter_genres", lambda genres, _cfg: genres)

    context = {
        "source": "spotify",
        "artist": {"id": "sp-artist", "name": "Artist One"},
        "album": {"id": "sp-album", "name": "Album One"},
        "track_info": {
            "id": "sp-track",
            "name": "Song One",
            "quality_profile_id": 42,
            "_source": "spotify",
        },
        "original_search_result": {"title": "Song One", "_source": "spotify"},
        "_final_processed_path": str(final_path),
    }
    artist_context = {"name": "Artist One", "genres": []}
    album_info = {"is_album": True, "album_name": "Album One"}

    side_effects.record_soulsync_library_entry(context, artist_context, album_info)

    track_row = _track_row(conn)
    assert track_row["quality_profile_id"] == 42
    assert track_row["quality_profile_explicit"] == 1


def test_record_soulsync_library_entry_ignores_numeric_spotify_ids(tmp_path, monkeypatch):
    conn = _make_soulsync_db()
    fake_db = _FakeDB(conn)
    final_path = tmp_path / "track.flac"
    final_path.write_bytes(b"audio")

    monkeypatch.setattr(side_effects, "get_database", lambda: fake_db)
    monkeypatch.setattr(
        side_effects,
        "_get_config_manager",
        lambda: SimpleNamespace(get_active_media_server=lambda: "soulsync"),
    )

    import core.genre_filter as genre_filter

    monkeypatch.setattr(genre_filter, "filter_genres", lambda genres, _cfg: genres)

    context = {
        "source": "spotify",
        "artist": {"id": "396753", "name": "Artist One"},
        "album": {
            "id": "284076172",
            "name": "Album One",
            "release_date": "2024-02-03",
            "total_tracks": 12,
        },
        "track_info": {
            "id": "1607091752",
            "name": "Song One",
            "track_number": 7,
            "duration_ms": 210000,
            "artists": [{"name": "Guest Artist"}],
            "_source": "spotify",
        },
        "original_search_result": {
            "title": "Song One",
            "artists": [{"name": "Guest Artist"}],
            "_source": "spotify",
        },
        "_final_processed_path": str(final_path),
    }

    artist_context = {"name": "Artist One", "genres": ["rock"]}
    album_info = {"is_album": True, "album_name": "Album One", "track_number": 7}

    side_effects.record_soulsync_library_entry(context, artist_context, album_info)

    artist_row = _artist_row(conn)
    album_row = _album_row(conn)
    track_row = _track_row(conn)

    assert artist_row["spotify_id"] is None
    assert album_row["spotify_id"] is None
    assert track_row["spotify_id"] is None


# ---------------------------------------------------------------------------
# SoulSync standalone parity — auto-import / direct download must write the
# same field richness a Plex/Jellyfin/Navidrome scan would write. Pin the
# per-recording identifier columns (`musicbrainz_recording_id`, `isrc`)
# AND the source-aware ID columns (`deezer_id`, etc.) for non-Spotify
# sources so dev work can't silently drop them.
# ---------------------------------------------------------------------------


def test_record_soulsync_library_entry_writes_mbid_and_isrc(tmp_path, monkeypatch):
    """Per-recording IDs land on the tracks row when the metadata source
    provides them (Picard-tagged libraries, MusicBrainz-enriched
    Spotify, etc.). Without this, watchlist re-download checks fall
    back to fuzzy name matching and re-download tracks the user
    already has."""
    conn = _make_soulsync_db()
    fake_db = _FakeDB(conn)
    final_path = tmp_path / "track.flac"
    final_path.write_bytes(b"audio")

    monkeypatch.setattr(side_effects, "get_database", lambda: fake_db)
    monkeypatch.setattr(
        side_effects,
        "_get_config_manager",
        lambda: SimpleNamespace(get_active_media_server=lambda: "soulsync"),
    )
    import core.genre_filter as genre_filter
    monkeypatch.setattr(genre_filter, "filter_genres", lambda genres, _cfg: genres)

    context = {
        "source": "spotify",
        "artist": {"id": "sp-artist", "name": "Picard Artist"},
        "album": {
            "id": "sp-album", "name": "Tagged Album",
            "release_date": "2022-01-01", "total_tracks": 10,
        },
        "track_info": {
            "id": "sp-track", "name": "Tagged Track",
            "track_number": 3, "duration_ms": 195000,
            "artists": [{"name": "Picard Artist"}],
            # Per-recording IDs — read by Mutagen from MUSICBRAINZ_TRACKID
            # tag (Picard) or surfaced from the metadata source's response.
            "musicbrainz_recording_id": "abcd1234-mbid-uuid-form",
            "isrc": "USABC1234567",
        },
        "original_search_result": {"title": "Tagged Track"},
        "_final_processed_path": str(final_path),
    }
    artist_context = {"name": "Picard Artist", "genres": []}
    album_info = {"is_album": True, "album_name": "Tagged Album", "track_number": 3}

    side_effects.record_soulsync_library_entry(context, artist_context, album_info)

    row = conn.execute("SELECT musicbrainz_id AS musicbrainz_recording_id, isrc FROM lib2_tracks").fetchone()
    assert row["musicbrainz_recording_id"] == "abcd1234-mbid-uuid-form"
    assert row["isrc"] == "USABC1234567"


def test_record_soulsync_library_entry_handles_deezer_source(tmp_path, monkeypatch):
    """Deezer source maps all three (artist/album/track) IDs onto the
    `deezer_id` column. Verify the source-aware column resolver routes
    correctly — a regression here means deezer-primary users get
    soulsync rows with no source ID at all."""
    conn = _make_soulsync_db()
    fake_db = _FakeDB(conn)
    final_path = tmp_path / "track.flac"
    final_path.write_bytes(b"audio")

    monkeypatch.setattr(side_effects, "get_database", lambda: fake_db)
    monkeypatch.setattr(
        side_effects,
        "_get_config_manager",
        lambda: SimpleNamespace(get_active_media_server=lambda: "soulsync"),
    )
    import core.genre_filter as genre_filter
    monkeypatch.setattr(genre_filter, "filter_genres", lambda genres, _cfg: genres)

    context = {
        "source": "deezer",
        "artist": {"id": "12345", "name": "DZ Artist"},
        "album": {"id": "67890", "name": "DZ Album", "total_tracks": 5},
        "track_info": {
            "id": "111213",
            "name": "DZ Track",
            "track_number": 1,
            "duration_ms": 180000,
            "artists": [{"name": "DZ Artist"}],
        },
        "original_search_result": {"title": "DZ Track"},
        "_final_processed_path": str(final_path),
    }
    artist_context = {"name": "DZ Artist", "genres": []}
    album_info = {"is_album": True, "album_name": "DZ Album", "track_number": 1}

    side_effects.record_soulsync_library_entry(context, artist_context, album_info)

    track_row = conn.execute("SELECT external_ids FROM lib2_tracks").fetchone()
    # Deezer source map writes the track's source-id onto the deezer_id
    # column (same column name the artist + album use; deezer doesn't
    # split per-entity-type ID columns the way Spotify / iTunes do).
    assert json.loads(track_row["external_ids"])["deezer"] == "111213"


# ---------------------------------------------------------------------------
# Auto-import labelling — library history + provenance must show
# "Auto-Import" / "auto_import" instead of falling back to "Soulseek".
# ---------------------------------------------------------------------------


def _make_history_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE library_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT, title TEXT, artist_name TEXT, album_name TEXT,
            quality TEXT, file_path TEXT, thumb_url TEXT, download_source TEXT,
            source_track_id TEXT, source_track_title TEXT, source_filename TEXT,
            acoustid_result TEXT, source_artist TEXT, created_at TEXT
        )
        """
    )
    return conn


def test_library_history_labels_auto_import(monkeypatch):
    """Auto-import sets `_download_username='auto_import'`; history row
    must read 'Auto-Import' instead of falling back to 'Soulseek'."""
    conn = _make_history_db()

    captured = {}

    class _DBStub:
        def add_library_history_entry(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(side_effects, "get_database", lambda: _DBStub())

    context = {
        "_download_username": "auto_import",
        "track_info": {
            "name": "Auto-Imported Track",
            "artists": [{"name": "Some Artist"}],
            "album": "Some Album",
            "id": "abc",
        },
        "original_search_result": {},
        "_final_processed_path": "/library/some-album/01.flac",
    }
    side_effects.record_library_history_download(context)
    assert captured["download_source"] == "Auto-Import"
    assert captured["title"] == "Auto-Imported Track"


@pytest.mark.parametrize(
    ("username", "expected"),
    [
        ("torrent", "Torrent"),
        ("usenet", "Usenet"),
        ("staging", "Staging"),
    ],
)
def test_library_history_labels_release_and_staging_sources(monkeypatch, username, expected):
    """Release/staging imports should not fall through to the Soulseek label."""
    captured = {}

    class _DBStub:
        def add_library_history_entry(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(side_effects, "get_database", lambda: _DBStub())

    context = {
        "track_info": {
            "name": "Imported Track",
            "artists": [{"name": "Some Artist"}],
            "album": "Some Album",
        },
        "original_search_result": {
            "username": username,
            "filename": "source-file.flac",
        },
        "_final_processed_path": "/library/some-album/01.flac",
    }

    side_effects.record_library_history_download(context)

    assert captured["download_source"] == expected


# ---------------------------------------------------------------------------
# Album duration parity — must equal sum of all track durations, not whatever
# the first imported track happened to be.
# ---------------------------------------------------------------------------


def test_album_duration_uses_album_total_not_single_track(tmp_path, monkeypatch):
    """Pre-fix `record_soulsync_library_entry` wrote
    `track_info.duration_ms` (one track's duration) into the album row's
    `duration` column. SoulSync standalone scanner sums every track's
    duration to populate that column — mirror it. This test passes
    `album.duration_ms` explicitly on the context (the worker computes
    it as `sum(match['track']['duration_ms'])`) and verifies the album
    row reads it instead of falling back to the per-track value."""
    conn = _make_soulsync_db()
    fake_db = _FakeDB(conn)
    final_path = tmp_path / "track5.flac"
    final_path.write_bytes(b"audio")

    monkeypatch.setattr(side_effects, "get_database", lambda: fake_db)
    monkeypatch.setattr(
        side_effects,
        "_get_config_manager",
        lambda: SimpleNamespace(get_active_media_server=lambda: "soulsync"),
    )
    import core.genre_filter as genre_filter
    monkeypatch.setattr(genre_filter, "filter_genres", lambda genres, _cfg: genres)

    context = {
        "source": "spotify",
        "artist": {"id": "sp-artist", "name": "Artist"},
        "album": {
            "id": "sp-album",
            "name": "Long Album",
            "release_date": "2024-01-01",
            "total_tracks": 12,
            # Sum across the album — the worker computes this from
            # match_result.matches. Single-track payload below is
            # 200_000ms but album total is 2_500_000.
            "duration_ms": 2_500_000,
        },
        "track_info": {
            "id": "sp-track-5",
            "name": "Track 5",
            "track_number": 5,
            "duration_ms": 200_000,
            "artists": [{"name": "Artist"}],
        },
        "original_search_result": {"title": "Track 5"},
        "_final_processed_path": str(final_path),
    }
    artist_context = {"name": "Artist", "genres": []}
    album_info = {"is_album": True, "album_name": "Long Album", "track_number": 5}

    side_effects.record_soulsync_library_entry(context, artist_context, album_info)

    album_row = conn.execute("SELECT duration FROM lib2_albums").fetchone()
    track_row = conn.execute("SELECT duration FROM lib2_tracks").fetchone()
    assert album_row["duration"] == 2_500_000, (
        f"Album duration must equal album total, got {album_row['duration']}. "
        f"Bug: it's writing the single track's duration (200_000) instead."
    )
    assert track_row["duration"] == 200_000


# ---------------------------------------------------------------------------
# Conservative UPDATE path — second import refreshes empty fields without
# clobbering populated ones.
# ---------------------------------------------------------------------------


def test_re_import_fills_empty_artist_fields(tmp_path, monkeypatch):
    """First import lands an artist row with no thumb_url + no genres
    (e.g. genre tags were absent on those tracks). Second import for
    the SAME artist comes in with thumb_url + genres present — those
    must land on the existing row instead of being silently ignored
    (the pre-fix behaviour was insert-only)."""
    conn = _make_soulsync_db()
    fake_db = _FakeDB(conn)
    final_path1 = tmp_path / "first.flac"
    final_path1.write_bytes(b"audio")
    final_path2 = tmp_path / "second.flac"
    final_path2.write_bytes(b"audio")

    monkeypatch.setattr(side_effects, "get_database", lambda: fake_db)
    monkeypatch.setattr(
        side_effects,
        "_get_config_manager",
        lambda: SimpleNamespace(get_active_media_server=lambda: "soulsync"),
    )
    import core.genre_filter as genre_filter
    monkeypatch.setattr(genre_filter, "filter_genres", lambda genres, _cfg: genres)

    # First import — artist with no thumb_url + no genres
    ctx1 = {
        "source": "spotify",
        "artist": {"id": "sp-artist", "name": "Same Artist"},
        "album": {"id": "sp-album-1", "name": "First Album", "total_tracks": 1},
        "track_info": {"id": "sp-track-1", "name": "T1", "track_number": 1,
                       "duration_ms": 200000, "artists": [{"name": "Same Artist"}]},
        "original_search_result": {},
        "_final_processed_path": str(final_path1),
    }
    side_effects.record_soulsync_library_entry(
        ctx1,
        {"name": "Same Artist", "genres": []},  # NO genres
        {"is_album": True, "album_name": "First Album", "track_number": 1},
    )

    artist_row = conn.execute("SELECT id, image_url, genres FROM lib2_artists").fetchone()
    artist_id_first = artist_row["id"]
    assert artist_row["image_url"] in (None, "")
    assert artist_row["genres"] in (None, "", "[]")   # v2 writes an empty list

    # Second import — artist with thumb + genres present
    ctx2 = dict(ctx1)
    ctx2["album"] = {"id": "sp-album-2", "name": "Second Album", "total_tracks": 1,
                     "image_url": "https://img.example/cover2.jpg"}
    ctx2["track_info"] = {"id": "sp-track-2", "name": "T2", "track_number": 1,
                          "duration_ms": 200000, "artists": [{"name": "Same Artist"}]}
    ctx2["_final_processed_path"] = str(final_path2)
    side_effects.record_soulsync_library_entry(
        ctx2,
        {"name": "Same Artist", "genres": ["Hip-Hop", "Rap"]},
        {"is_album": True, "album_name": "Second Album", "track_number": 1},
    )

    # Same artist row updated — empty fields filled
    artist_row2 = conn.execute("SELECT id, image_url, genres FROM lib2_artists").fetchone()
    assert artist_row2["id"] == artist_id_first, "Should reuse existing artist row"
    assert artist_row2["image_url"] == "https://img.example/cover2.jpg", (
        "Empty thumb_url should be filled from second import"
    )
    assert "Hip-Hop" in (artist_row2["genres"] or ""), (
        "Empty genres should be filled from second import"
    )
    # Two album rows now (different albums for same artist)
    album_count = conn.execute("SELECT COUNT(*) FROM lib2_albums").fetchone()[0]
    assert album_count == 2


def test_re_import_does_not_clobber_populated_artist_fields(tmp_path, monkeypatch):
    """First import with rich genres + thumb. Second import with
    DIFFERENT (worse) genres + DIFFERENT thumb. Existing populated
    values must be preserved, not overwritten — protects manual
    edits + enrichment-worker writes."""
    conn = _make_soulsync_db()
    fake_db = _FakeDB(conn)
    final_path1 = tmp_path / "first.flac"
    final_path1.write_bytes(b"audio")
    final_path2 = tmp_path / "second.flac"
    final_path2.write_bytes(b"audio")

    monkeypatch.setattr(side_effects, "get_database", lambda: fake_db)
    monkeypatch.setattr(
        side_effects,
        "_get_config_manager",
        lambda: SimpleNamespace(get_active_media_server=lambda: "soulsync"),
    )
    import core.genre_filter as genre_filter
    monkeypatch.setattr(genre_filter, "filter_genres", lambda genres, _cfg: genres)

    ctx1 = {
        "source": "spotify",
        "artist": {"id": "sp-artist", "name": "Stable Artist"},
        "album": {"id": "sp-album-1", "name": "A1", "total_tracks": 1,
                  "image_url": "https://img.example/original.jpg"},
        "track_info": {"id": "sp-track-1", "name": "T1", "track_number": 1,
                       "duration_ms": 200000, "artists": [{"name": "Stable Artist"}]},
        "original_search_result": {},
        "_final_processed_path": str(final_path1),
    }
    side_effects.record_soulsync_library_entry(
        ctx1,
        {"name": "Stable Artist", "genres": ["Hip-Hop", "Rap", "Trap"]},
        {"is_album": True, "album_name": "A1", "track_number": 1},
    )

    # Second import with worse / different metadata
    ctx2 = dict(ctx1)
    ctx2["album"] = {"id": "sp-album-2", "name": "A2", "total_tracks": 1,
                     "image_url": "https://img.example/replacement.jpg"}
    ctx2["track_info"] = {"id": "sp-track-2", "name": "T2", "track_number": 1,
                          "duration_ms": 200000, "artists": [{"name": "Stable Artist"}]}
    ctx2["_final_processed_path"] = str(final_path2)
    side_effects.record_soulsync_library_entry(
        ctx2,
        {"name": "Stable Artist", "genres": ["Pop"]},  # Different + worse
        {"is_album": True, "album_name": "A2", "track_number": 1},
    )

    artist_row = conn.execute("SELECT image_url, genres FROM lib2_artists").fetchone()
    # Original values preserved
    assert artist_row["image_url"] == "https://img.example/original.jpg", (
        "Existing thumb_url must NOT be clobbered by re-import"
    )
    assert "Hip-Hop" in artist_row["genres"], (
        "Existing genres must NOT be clobbered by re-import"
    )
    # NEW values must NOT have replaced the originals
    assert "replacement" not in (artist_row["image_url"] or "")
    assert "Pop" not in (artist_row["genres"] or "")


def test_re_import_fills_empty_source_id_when_missing(tmp_path, monkeypatch):
    """First import via fingerprint identification — no spotify_track_id
    on the artist row. Second import (same artist) via tag-based match
    that DOES carry a spotify_artist_id. The fill-empty UPDATE must
    populate the column."""
    conn = _make_soulsync_db()
    fake_db = _FakeDB(conn)
    final_path1 = tmp_path / "first.flac"
    final_path1.write_bytes(b"audio")
    final_path2 = tmp_path / "second.flac"
    final_path2.write_bytes(b"audio")

    monkeypatch.setattr(side_effects, "get_database", lambda: fake_db)
    monkeypatch.setattr(
        side_effects,
        "_get_config_manager",
        lambda: SimpleNamespace(get_active_media_server=lambda: "soulsync"),
    )
    import core.genre_filter as genre_filter
    monkeypatch.setattr(genre_filter, "filter_genres", lambda genres, _cfg: genres)

    # First import — no source artist ID
    ctx1 = {
        "source": "spotify",
        "artist": {"id": "", "name": "Same Artist"},  # No ID
        "album": {"id": "sp-album-1", "name": "A1", "total_tracks": 1},
        "track_info": {"id": "sp-track-1", "name": "T1", "track_number": 1,
                       "duration_ms": 200000, "artists": [{"name": "Same Artist"}]},
        "original_search_result": {},
        "_final_processed_path": str(final_path1),
    }
    side_effects.record_soulsync_library_entry(
        ctx1, {"name": "Same Artist", "genres": []},
        {"is_album": True, "album_name": "A1", "track_number": 1},
    )

    artist_row = conn.execute("SELECT spotify_id FROM lib2_artists").fetchone()
    assert artist_row["spotify_id"] in (None, "")

    # Second import — now carries a valid source ID
    ctx2 = dict(ctx1)
    ctx2["artist"] = {"id": "sp-artist-real", "name": "Same Artist"}
    ctx2["album"] = {"id": "sp-album-2", "name": "A2", "total_tracks": 1}
    ctx2["track_info"] = {"id": "sp-track-2", "name": "T2", "track_number": 1,
                          "duration_ms": 200000, "artists": [{"name": "Same Artist"}]}
    ctx2["_final_processed_path"] = str(final_path2)
    side_effects.record_soulsync_library_entry(
        ctx2, {"name": "Same Artist", "genres": []},
        {"is_album": True, "album_name": "A2", "track_number": 1},
    )

    artist_row2 = conn.execute("SELECT spotify_id FROM lib2_artists").fetchone()
    assert artist_row2["spotify_id"] == "sp-artist-real", (
        "Empty spotify_artist_id should be filled by the second import. "
        "This is what makes the watchlist scanner recognise the artist "
        "as already in library by stable source ID."
    )


def test_provenance_labels_auto_import(monkeypatch):
    """Same gate for provenance: `_download_username='auto_import'`
    must register the provenance row as `auto_import` (lowercase /
    canonical), not the `soulseek` fallback default."""
    captured = {}

    class _DBStub:
        def record_track_download(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(side_effects, "get_database", lambda: _DBStub())
    import core.library2.autolink as autolink
    import core.library2.unmapped_trigger as unmapped_trigger

    monkeypatch.setattr(
        autolink, "link_download_into_library_v2", lambda *_args, **_kwargs: None,
    )
    scheduled = []
    monkeypatch.setattr(
        unmapped_trigger,
        "schedule_unmapped_artist_reconcile",
        lambda *_args, **_kwargs: scheduled.append(True),
    )

    context = {
        "_download_username": "auto_import",
        "track_info": {
            "name": "Auto-Imported Track",
            "artists": [{"name": "Some Artist"}],
            "album": "Some Album",
            "id": "abc",
        },
        "original_search_result": {},
        "_final_processed_path": "/library/some-album/01.flac",
    }
    side_effects.record_download_provenance(context)
    assert captured.get("source_service") == "auto_import"
    assert scheduled == [], "no Lib2 row means there is no new artist to reconcile"


def test_required_library_registration_fails_before_completion_callbacks(monkeypatch):
    class _DBStub:
        def record_track_download(self, **_kwargs):
            return None

    monkeypatch.setattr(side_effects, "get_database", lambda: _DBStub())
    import core.library2.autolink as autolink
    import core.acquisition.pipeline_callback as callbacks

    monkeypatch.setattr(
        autolink, "link_download_into_library_v2",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("db unavailable")),
    )
    completed = []
    monkeypatch.setattr(
        callbacks, "notify_pipeline_import_success",
        lambda _context: completed.append("pipeline"),
    )
    monkeypatch.setattr(
        callbacks, "notify_manual_grab_import_success",
        lambda _context: completed.append("grab"),
    )

    with pytest.raises(RuntimeError, match="Library v2 could not register"):
        side_effects.record_download_provenance(
            {"_final_processed_path": "/library/song.flac"},
            require_library=True,
        )
    assert completed == []


def test_download_provenance_carries_retention_policy_for_media_server(monkeypatch):
    """The durable download record keeps the same transform truth that the
    synchronous Library-v2 autolink writes to the authoritative file row."""
    captured = {}

    class _DBStub:
        def record_track_download(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(side_effects, "get_database", lambda: _DBStub())
    context = {
        "track_info": {"name": "Portable", "artists": [{"name": "Artist"}]},
        "_final_processed_path": "/library/Portable.mp3",
        "_acquired_audio_quality": {
            "format": "flac", "bit_depth": 24, "sample_rate": 96000,
        },
        "_retention_transforms": [{
            "type": "lossy_copy", "source_replaced": True,
        }],
    }

    side_effects.record_download_provenance(context)

    assert json.loads(captured["acquired_quality_json"])["format"] == "flac"
    assert json.loads(captured["retention_json"])[0] == {
        "source_replaced": True,
        "type": "lossy_copy",
    }


def test_is_active_media_server_ready_standalone_always_ready(monkeypatch):
    monkeypatch.setattr(
        side_effects,
        "_get_config_manager",
        lambda: SimpleNamespace(get_active_media_server=lambda: "soulsync"),
    )

    ready, reason = side_effects.is_active_media_server_ready()

    assert ready is True
    assert reason == ""


def test_is_active_media_server_ready_true_when_server_connected(monkeypatch):
    monkeypatch.setattr(
        side_effects,
        "_get_config_manager",
        lambda: SimpleNamespace(get_active_media_server=lambda: "plex"),
    )

    import core.media_server.engine as media_server_engine

    monkeypatch.setattr(
        media_server_engine,
        "get_media_server_engine",
        lambda: SimpleNamespace(is_connected=lambda: True),
    )

    ready, reason = side_effects.is_active_media_server_ready()

    assert ready is True
    assert reason == ""


def test_is_active_media_server_ready_does_not_block_local_import_when_server_offline(monkeypatch):
    monkeypatch.setattr(
        side_effects,
        "_get_config_manager",
        lambda: SimpleNamespace(get_active_media_server=lambda: "jellyfin"),
    )

    import core.media_server.engine as media_server_engine

    monkeypatch.setattr(
        media_server_engine,
        "get_media_server_engine",
        lambda: SimpleNamespace(is_connected=lambda: False),
    )

    ready, reason = side_effects.is_active_media_server_ready()

    assert ready is True
    assert reason == ""


def test_the_registration_gate_accepts_a_row_the_autolink_did_not_write(tmp_path, monkeypatch):
    """The gate asks the catalogue, not one writer's return value (issues §38).

    ``link_download_into_library_v2`` returns None — without raising — for a file
    whose identity it cannot derive. ``record_soulsync_library_entry`` derives the
    same identity from ``artist_context`` and writes the same rows, so failing on
    the autolink's None reported a correctly imported file as failed and skipped
    the completion callbacks behind it.
    """
    import sqlite3

    from core.library2.schema import ensure_library_v2_schema

    path = str(tmp_path / "lib2.db")
    seed = sqlite3.connect(path)
    ensure_library_v2_schema(seed)
    seed.execute("INSERT INTO lib2_artists(name,sort_name) VALUES('Muse','Muse')")
    seed.execute("INSERT INTO lib2_albums(primary_artist_id,title) VALUES(1,'Absolution')")
    seed.execute("INSERT INTO lib2_tracks(album_id,title) VALUES(1,'Time')")
    seed.execute(
        "INSERT INTO lib2_track_files(track_id,path,is_primary,file_state)"
        " VALUES(1,'/library/song.flac',1,'active')")
    seed.commit()
    seed.close()

    class _DBStub:
        def record_track_download(self, **_kwargs):
            return None

        def _get_connection(self):
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            return conn

    monkeypatch.setattr(side_effects, "get_database", lambda: _DBStub())
    import core.library2.autolink as autolink

    monkeypatch.setattr(
        autolink, "link_download_into_library_v2", lambda *_a, **_kw: None)

    # Registered by the other writer: no raise.
    side_effects.record_download_provenance(
        {"_final_processed_path": "/library/song.flac"}, require_library=True)

    # Nothing in the catalogue for this path: still a hard failure.
    with pytest.raises(RuntimeError, match="did not create a file row"):
        side_effects.record_download_provenance(
            {"_final_processed_path": "/library/never-imported.flac"},
            require_library=True)
