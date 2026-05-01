"""Regression tests for soundtrack/compilation track-artist matching.

The Discord-reported bug: a Vaiana OST track ("Where You Are" by
Christopher Jackson) failed to match against a Plex/Emby library
because the album's primary artist was Lin-Manuel Miranda. SoulSync's
DB stores the per-track artist in ``tracks.track_artist`` (from
Plex's ``originalTitle`` or Jellyfin's ``ArtistItems[0]``), but the
confidence scorer only compared against the album-artist JOIN and
never looked at ``track_artist``.

These tests pin the new behaviour:

- ``_calculate_track_confidence`` scores against ``track_artist`` too,
  taking the better artist similarity, so soundtrack tracks credited
  to the actual performer match.
- ``_rows_to_tracks`` propagates ``track_artist`` from row to object.
- The album-aware fallback constructs DatabaseTrack with the right
  dataclass fields (it used to TypeError on every row).
"""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from database.music_database import DatabaseTrack, MusicDatabase


@pytest.fixture
def db_with_soundtrack(tmp_path: Path):
    """Build a real MusicDatabase with one OST-style row inserted by hand.

    Mirrors the Discord scenario: album artist ("Lin-Manuel Miranda")
    differs from the actual performer of the track ("Christopher
    Jackson"), and the per-track artist is stored in
    ``tracks.track_artist``.
    """
    db_path = tmp_path / "test.db"
    db = MusicDatabase(database_path=str(db_path))

    conn = db._get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "INSERT INTO artists (id, name, server_source) VALUES (?, ?, ?)",
        ("artist-1", "Lin-Manuel Miranda", "plex"),
    )
    cursor.execute(
        "INSERT INTO albums (id, artist_id, title, server_source) VALUES (?, ?, ?, ?)",
        ("album-1", "artist-1", "Vaiana (English Version/Original Motion Picture Soundtrack)", "plex"),
    )
    cursor.execute(
        """
        INSERT INTO tracks (
            id, album_id, artist_id, title, track_number, duration,
            file_path, bitrate, server_source, track_artist
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("track-1", "album-1", "artist-1", "Where You Are", 4, 210000,
         "/music/where_you_are.mp3", 320, "plex", "Christopher Jackson"),
    )
    conn.commit()
    conn.close()
    return db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_check_track_exists_matches_via_track_artist(db_with_soundtrack: MusicDatabase) -> None:
    """The reported scenario: search by per-track performer must succeed
    even when the album sits under a different primary artist."""
    track, confidence = db_with_soundtrack.check_track_exists(
        title="Where You Are",
        artist="Christopher Jackson",
        confidence_threshold=0.8,
    )
    assert track is not None, "soundtrack track should match via track_artist"
    assert track.title == "Where You Are"
    assert confidence >= 0.8


def test_check_track_exists_still_matches_via_album_artist(db_with_soundtrack: MusicDatabase) -> None:
    """Searching by the album artist must still work (regression
    guard — we want to ADD a fallback, not replace the original path)."""
    track, confidence = db_with_soundtrack.check_track_exists(
        title="Where You Are",
        artist="Lin-Manuel Miranda",
        confidence_threshold=0.8,
    )
    assert track is not None, "album-artist match must keep working"
    assert track.title == "Where You Are"


def test_calculate_track_confidence_uses_better_artist_match(
    db_with_soundtrack: MusicDatabase,
) -> None:
    """Scorer must take the BETTER of (album-artist sim, track-artist sim)."""
    track = DatabaseTrack(
        id="t1", album_id="a1", artist_id="ar1",
        title="Where You Are", track_number=4, duration=210000,
        file_path="/x.mp3", bitrate=320,
    )
    track.artist_name = "Lin-Manuel Miranda"
    track.track_artist = "Christopher Jackson"

    # Search by the per-track artist scores high
    track_artist_conf = db_with_soundtrack._calculate_track_confidence(
        "Where You Are", "Christopher Jackson", track,
    )
    # Search by the album artist also scores high
    album_artist_conf = db_with_soundtrack._calculate_track_confidence(
        "Where You Are", "Lin-Manuel Miranda", track,
    )
    assert track_artist_conf >= 0.8
    assert album_artist_conf >= 0.8


def test_calculate_track_confidence_handles_missing_track_artist(
    db_with_soundtrack: MusicDatabase,
) -> None:
    """Tracks without a per-track artist (the common case for non-
    compilations) must keep working — the scorer must not crash on a
    missing attribute and must fall through to the album-artist score."""
    track = DatabaseTrack(
        id="t2", album_id="a2", artist_id="ar2",
        title="Some Song", track_number=1, duration=200000,
        file_path="/y.mp3", bitrate=320,
    )
    track.artist_name = "Some Artist"
    # Deliberately do NOT set track_artist — most rows leave it None.
    conf = db_with_soundtrack._calculate_track_confidence(
        "Some Song", "Some Artist", track,
    )
    assert conf >= 0.8


def test_search_tracks_attaches_track_artist(db_with_soundtrack: MusicDatabase) -> None:
    """The search path must propagate track_artist onto returned objects
    so the confidence scorer can use it. This used to be silently
    dropped during row→object conversion."""
    rows = db_with_soundtrack.search_tracks(
        title="Where You Are", artist="Christopher Jackson", limit=10,
    )
    assert rows, "search must find the soundtrack track"
    track = rows[0]
    assert getattr(track, 'track_artist', None) == "Christopher Jackson"
    assert track.artist_name == "Lin-Manuel Miranda"


def test_album_aware_fallback_does_not_over_match_wrong_album(tmp_path: Path) -> None:
    """Fallback must reject when the album-name hint doesn't actually
    match the row's album. Otherwise re-enabling the previously-dead
    fallback would surface false positives whenever the search title
    happens to exist on a different album.

    Album threshold is 0.8 — a clearly different album name like
    "Some Other Album" must not pass.
    """
    db_path = tmp_path / "negative_fallback.db"
    db = MusicDatabase(database_path=str(db_path))

    conn = db._get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO artists (id, name, server_source) VALUES (?, ?, ?)",
        ("ar-y", "Madonna", "plex"),
    )
    cursor.execute(
        "INSERT INTO albums (id, artist_id, title, server_source) VALUES (?, ?, ?, ?)",
        ("al-y", "ar-y", "Ray of Light", "plex"),
    )
    cursor.execute(
        """
        INSERT INTO tracks (
            id, album_id, artist_id, title, track_number, duration,
            file_path, bitrate, server_source, track_artist
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("tr-y", "al-y", "ar-y", "Frozen", 1, 200000,
         "/m/frozen.mp3", 320, "plex", None),
    )
    conn.commit()
    conn.close()

    # Search by a clearly different artist + a totally unrelated album
    # hint. Main path scores low artist similarity → falls through to
    # the album-aware fallback. Fallback's 0.8 album-title floor must
    # reject "Disney Hits" against "Ray of Light".
    track, _ = db.check_track_exists(
        title="Frozen",
        artist="Idina Menzel",
        confidence_threshold=0.7,
        album="Disney Hits",
    )
    assert track is None, (
        "fallback must reject mismatched album hints — otherwise "
        "re-enabling the previously-dead path leaks false positives"
    )


def test_album_aware_fallback_actually_works(tmp_path: Path) -> None:
    """The album-aware fallback path used to TypeError on every row
    because DatabaseTrack(...) was called with kwargs that don't exist
    on the dataclass (artist_name, album_title, server_source). Every
    fallback row silently failed, so this entire branch never matched
    anything since track_artist was added.

    Pin the new behaviour by forcing the main path to miss (artist
    string nowhere in the row) and verifying the fallback succeeds
    when an album-name hint is provided.
    """
    db_path = tmp_path / "fallback_test.db"
    db = MusicDatabase(database_path=str(db_path))

    conn = db._get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO artists (id, name, server_source) VALUES (?, ?, ?)",
        ("ar-x", "Various Artists", "plex"),
    )
    cursor.execute(
        "INSERT INTO albums (id, artist_id, title, server_source) VALUES (?, ?, ?, ?)",
        ("al-x", "ar-x", "Awesome Mix Vol. 1", "plex"),
    )
    cursor.execute(
        """
        INSERT INTO tracks (
            id, album_id, artist_id, title, track_number, duration,
            file_path, bitrate, server_source, track_artist
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("tr-x", "al-x", "ar-x", "Hooked on a Feeling", 2, 175000,
         "/m/hooked.mp3", 320, "plex", None),  # No per-track artist set
    )
    conn.commit()
    conn.close()

    # Search by an artist that doesn't match either album_artist or
    # track_artist. Main path will fail; album hint kicks in fallback.
    track, confidence = db.check_track_exists(
        title="Hooked on a Feeling",
        artist="Blue Swede",  # Real performer, not in the DB row
        confidence_threshold=0.7,
        album="Awesome Mix Vol. 1",
    )
    # Fallback matches on album name + title only — the artist mismatch
    # doesn't disqualify the result. Pre-fix this would have raised
    # TypeError internally and returned (None, 0.0).
    assert track is not None, "album-aware fallback must find the track"
    assert track.title == "Hooked on a Feeling"
    assert confidence >= 0.7
