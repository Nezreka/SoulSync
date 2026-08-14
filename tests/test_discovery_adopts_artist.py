"""Discovery must FIX an 'Unknown Artist' row, not just annotate it.

Found via the PR #1136 review: playlist discovery wrote its match into
extra_data.matched_data and stopped — the mirrored row's artist_name column
kept 'Unknown Artist', so the mirror DISPLAYED unknown, artist-based search
used unknown, and the author's explored playlist stayed 75% unknown despite
confident matches sitting right there in extra_data.

The adoption is deliberately one-way and conservative: only a missing/blank/
'Unknown Artist' value is replaced. A real source-provided artist is truth;
a 0.7-confidence discovery guess never overwrites it.
"""

from __future__ import annotations

from core.discovery.playlist import _matched_primary_artist
from database.music_database import MusicDatabase


def _db_with_tracks(tmp_path, rows):
    db = MusicDatabase(str(tmp_path / "adopt.db"))
    with db._get_connection() as conn:
        c = conn.cursor()
        c.execute("""INSERT INTO mirrored_playlists (id, source, source_playlist_id, name, profile_id)
                     VALUES (10, 'youtube', 'plX', 'Explored Mix', 1)""")
        for track_id, artist in rows:
            c.execute("""INSERT INTO mirrored_playlist_tracks
                         (id, playlist_id, position, track_name, artist_name)
                         VALUES (?, 10, ?, 'Memories', ?)""", (track_id, track_id, artist))
        conn.commit()
    return db


def _artist_of(db, track_id):
    with db._get_connection() as conn:
        return conn.execute(
            "SELECT artist_name FROM mirrored_playlist_tracks WHERE id = ?",
            (track_id,)).fetchone()["artist_name"]


def test_unknown_artist_rows_adopt_the_match(tmp_path):
    # The schema makes artist_name NOT NULL, so the placeholder and the empty
    # string are the only unknown shapes that can exist in a real row.
    db = _db_with_tracks(tmp_path, [(1, "Unknown Artist"), (2, ""), (3, "  unknown artist ")])
    for track_id in (1, 2, 3):
        assert db.adopt_discovered_artist(track_id, "Maroon 5") is True
        assert _artist_of(db, track_id) == "Maroon 5"


def test_real_artist_is_never_overwritten(tmp_path):
    db = _db_with_tracks(tmp_path, [(1, "Daft Punk")])
    assert db.adopt_discovered_artist(1, "An Unknown Artist") is False
    assert _artist_of(db, 1) == "Daft Punk"


def test_placeholder_match_names_are_refused(tmp_path):
    # Adopting 'Unknown Artist' FROM a match would be laundering the
    # placeholder through discovery. Empty strings likewise.
    db = _db_with_tracks(tmp_path, [(1, "Unknown Artist")])
    assert db.adopt_discovered_artist(1, "Unknown Artist") is False
    assert db.adopt_discovered_artist(1, "   ") is False
    assert db.adopt_discovered_artist(1, None) is False
    assert _artist_of(db, 1) == "Unknown Artist"


def test_matched_primary_artist_handles_both_shapes():
    # matched_data built fresh carries [{'name': ...}]; older cache rows can
    # carry plain strings. Both must yield the primary artist.
    assert _matched_primary_artist({"artists": [{"name": "Maroon 5"}, {"name": "Feat"}]}) == "Maroon 5"
    assert _matched_primary_artist({"artists": ["Maroon 5"]}) == "Maroon 5"
    assert _matched_primary_artist({"artists": []}) == ""
    assert _matched_primary_artist({}) == ""
    assert _matched_primary_artist(None) == ""
