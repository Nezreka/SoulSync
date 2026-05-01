"""Regression tests for featured-artist track matching.

Discord-reported scenario: a single "super single" by Artist1 feat.
Artist2 also appears on the album "super album" (Artist1). When the
album is fully owned, Artist1's discography shows the single as
complete, but Artist2's discography (which lists the same track as
their own single) shows it as missing — even though the same
recording exists in the library under Artist1's album.

Two layers of fix pinned by these tests:

- Scanner: store ALL Jellyfin/Emby ArtistItems in tracks.track_artist
  (joined with "; "), not just ArtistItems[0]. The first artist
  often equals the album artist and used to suppress the row.
- Scoring: split track_artist on common multi-artist delimiters
  (",", ";", "&", "feat.", "ft.", "featuring", "vs.", "x") and
  score each piece independently against the search artist.
"""

import sqlite3
from pathlib import Path

import pytest

from database.music_database import DatabaseTrack, MusicDatabase


@pytest.fixture
def db_with_feat_track(tmp_path: Path):
    """Build a real MusicDatabase with the featured-artist scenario.

    "Super Single" by "Artist1, Artist2" stored under Artist1's
    album. Mirrors what the Jellyfin scanner now writes when a
    track has multiple ArtistItems.
    """
    db_path = tmp_path / "feat.db"
    db = MusicDatabase(database_path=str(db_path))
    conn = db._get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO artists (id, name, server_source) VALUES (?, ?, ?)",
        ("ar-1", "Artist1", "jellyfin"),
    )
    cursor.execute(
        "INSERT INTO albums (id, artist_id, title, server_source) VALUES (?, ?, ?, ?)",
        ("al-1", "ar-1", "Super Album", "jellyfin"),
    )
    cursor.execute(
        """
        INSERT INTO tracks (
            id, album_id, artist_id, title, track_number, duration,
            file_path, bitrate, server_source, track_artist
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("tr-1", "al-1", "ar-1", "Super Single", 3, 200000,
         "/m/super.mp3", 320, "jellyfin", "Artist1; Artist2"),
    )
    conn.commit()
    conn.close()
    return db


# ---------------------------------------------------------------------------
# Scoring: featured artist matches via split
# ---------------------------------------------------------------------------


def test_featured_artist_matches_via_track_artist_split(db_with_feat_track: MusicDatabase) -> None:
    """The reported scenario: searching for the featured artist
    (Artist2) finds the track stored under the primary artist's
    album because track_artist contains both names."""
    track, confidence = db_with_feat_track.check_track_exists(
        title="Super Single",
        artist="Artist2",
        confidence_threshold=0.7,
    )
    assert track is not None
    assert confidence >= 0.7


def test_primary_artist_still_matches(db_with_feat_track: MusicDatabase) -> None:
    """Forward compat: searching for the primary artist must still
    work — the original album-artist match path is preserved."""
    track, confidence = db_with_feat_track.check_track_exists(
        title="Super Single",
        artist="Artist1",
        confidence_threshold=0.7,
    )
    assert track is not None
    assert confidence >= 0.7


@pytest.mark.parametrize("track_artist_value", [
    "Artist1, Artist2",
    "Artist1; Artist2",
    "Artist1 & Artist2",
    "Artist1 feat. Artist2",
    "Artist1 ft. Artist2",
    "Artist1 featuring Artist2",
    "Artist1 vs. Artist2",
    "Artist1 x Artist2",
])
def test_scoring_handles_common_multi_artist_separators(
    db_with_feat_track: MusicDatabase, track_artist_value: str,
) -> None:
    """Score must find the featured artist regardless of which
    delimiter the metadata source / tag uses."""
    track = DatabaseTrack(
        id="x", album_id="y", artist_id="z",
        title="Super Single", track_number=1, duration=200000,
        file_path="/m/x.mp3", bitrate=320,
    )
    track.artist_name = "Artist1"
    track.track_artist = track_artist_value
    conf = db_with_feat_track._calculate_track_confidence(
        "Super Single", "Artist2", track,
    )
    assert conf >= 0.7, (
        f"separator '{track_artist_value}' should still let Artist2 match"
    )


def test_split_does_not_inflate_score_beyond_whole_string_floor(
    db_with_feat_track: MusicDatabase,
) -> None:
    """Splitting must only ADD to the score (best-of), never pull it
    below the whole-string baseline. Same artist on both sides should
    score 1.0 the same way it always did, with or without delimiters."""
    track = DatabaseTrack(
        id="x", album_id="y", artist_id="z",
        title="Solo Song", track_number=1, duration=200000,
        file_path="/m/x.mp3", bitrate=320,
    )
    track.artist_name = "Solo Artist"
    track.track_artist = "Solo Artist"  # No delimiters at all
    conf = db_with_feat_track._calculate_track_confidence(
        "Solo Song", "Solo Artist", track,
    )
    assert conf >= 0.99, "exact-match score must not regress"


# ---------------------------------------------------------------------------
# Scanner: Jellyfin ArtistItems propagation
# ---------------------------------------------------------------------------


class _StubJellyfinTrack:
    """Minimal stub mimicking JellyfinTrack: real attributes the scanner
    reads (ratingKey, title, trackNumber, duration, path, bitRate) plus
    the ``_data`` raw dict where ArtistItems live."""
    def __init__(self, track_id, title, track_artists, album_artist,
                 track_number=1, duration=200000, file_path="/m/x.mp3",
                 bit_rate=320):
        self.ratingKey = track_id
        self.title = title
        self.trackNumber = track_number
        self.duration = duration
        self.path = file_path
        self.bitRate = bit_rate
        self._data = {
            'ArtistItems': [{'Name': n} for n in track_artists],
            'AlbumArtists': [{'Name': album_artist}],
        }


def test_jellyfin_scanner_stores_all_track_artists(tmp_path: Path) -> None:
    """The scanner must persist EVERY name from ArtistItems, not just
    the first. Pre-fix the scanner kept only [0] which was usually
    equal to the album artist, so nothing distinguishing was stored.
    """
    db = MusicDatabase(database_path=str(tmp_path / "scan.db"))
    conn = db._get_connection()
    cursor = conn.cursor()

    # Seed the artist + album the track will hang off
    cursor.execute(
        "INSERT INTO artists (id, name, server_source) VALUES (?, ?, ?)",
        ("ar-1", "Artist1", "jellyfin"),
    )
    cursor.execute(
        "INSERT INTO albums (id, artist_id, title, server_source) VALUES (?, ?, ?, ?)",
        ("al-1", "ar-1", "Super Album", "jellyfin"),
    )
    conn.commit()
    conn.close()

    track_obj = _StubJellyfinTrack(
        track_id="tr-1",
        title="Super Single",
        track_artists=["Artist1", "Artist2"],
        album_artist="Artist1",
    )
    db.insert_or_update_media_track(
        track_obj, album_id="al-1", artist_id="ar-1", server_source="jellyfin",
    )

    conn = db._get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT track_artist FROM tracks WHERE id = ?", ("tr-1",))
    row = cursor.fetchone()
    conn.close()
    assert row is not None
    assert row[0] is not None, "scanner should not drop multi-artist track credits"
    assert "Artist2" in row[0], (
        f"track_artist must contain every ArtistItem — got {row[0]!r}"
    )
