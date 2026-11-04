"""Automatic and approved manual links use release-independent evidence."""

import pytest

from core.library2.duplicate_relationship import (
    DuplicateRelationshipError, same_recording, validate_duplicate_pair,
)
from core.library2.schema import ensure_library_v2_schema
from tests.lib2_seed import row_conn, track


@pytest.fixture
def conn(tmp_path):
    connection = row_conn(str(tmp_path / "native.db"))
    ensure_library_v2_schema(connection)
    yield connection
    connection.close()


def test_release_spotify_ids_do_not_contradict_matching_recording_ids(conn):
    one = track(conn, "Artist", "Single", "Song", duration=180000,
                isrc="CHABC1234567", musicbrainz_id="recording", spotify_id="single-id")
    two = track(conn, "Artist", "Album", "Song", duration=180500,
                isrc="CHABC1234567", musicbrainz_id="recording", spotify_id="album-id")
    rows = [conn.execute("SELECT * FROM lib2_tracks WHERE id=?", (tid,)).fetchone()
            for tid in (one, two)]
    assert same_recording(*rows)
    assert validate_duplicate_pair(conn, one, two)["target"]["id"] == two


@pytest.mark.parametrize("column,value,error", [
    ("isrc", "DIFFERENT", "ISRC"),
    ("musicbrainz_id", "different-recording", "MusicBrainz"),
    ("duration", 240000, "durations"),
])
def test_hard_conflicts_are_rejected_in_both_paths(conn, column, value, error):
    one = track(conn, "Artist", "Single", "Song", duration=180000,
                isrc="ISRC", musicbrainz_id="recording")
    two = track(conn, "Artist", "Album", "Song", duration=180000,
                isrc="ISRC", musicbrainz_id="recording")
    conn.execute(f"UPDATE lib2_tracks SET {column}=? WHERE id=?", (value, two))
    rows = [conn.execute("SELECT * FROM lib2_tracks WHERE id=?", (tid,)).fetchone()
            for tid in (one, two)]
    assert not same_recording(*rows)
    with pytest.raises(DuplicateRelationshipError, match=error):
        validate_duplicate_pair(conn, one, two)
