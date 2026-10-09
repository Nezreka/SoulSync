"""Recording comments are stored with their MBID without changing titles."""

import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

from core.musicbrainz_service import MusicBrainzService


def test_match_recording_returns_disambiguation_from_live_and_cached_results():
    service = MusicBrainzService.__new__(MusicBrainzService)
    candidate = {"id": "rec-acoustic", "title": "Song", "disambiguation": "acoustic"}
    service.mb_client = SimpleNamespace(search_recording=Mock(return_value=[candidate]))
    service._score_recording_candidates = Mock(return_value=(candidate, 100))
    service._save_to_cache = Mock()
    service._check_cache = Mock(side_effect=[
        None,
        {"musicbrainz_id": "rec-acoustic", "metadata": candidate, "confidence": 100},
    ])

    live = service.match_recording("Song", "Artist")
    cached = service.match_recording("Song", "Artist")

    assert live["recording_disambiguation"] == "acoustic"
    assert cached["recording_disambiguation"] == "acoustic"


def test_track_mbid_update_takes_the_comment_and_stores_the_recording(tmp_path):
    """Ours: upstream's call shape (``recording_disambiguation``) works on the
    Library v2 row. lib2 keeps no comment column -- nothing reads one; the
    comment reaches the file as MUSICBRAINZ_TRACKCOMMENT -- so only the id
    lands, on lib2_tracks.musicbrainz_id."""
    from database.music_database import MusicDatabase
    from tests.lib2_seed import track

    db = MusicDatabase(str(tmp_path / "m.db"))
    with db._get_connection() as conn:
        track_id = track(conn, "Artist", "Album", "Song")
        conn.commit()

    service = MusicBrainzService.__new__(MusicBrainzService)
    service.db = db
    service.update_track_mbid(track_id, "rec-a", "matched", "acoustic")

    with db._get_connection() as conn:
        assert conn.execute("SELECT musicbrainz_id FROM lib2_tracks WHERE id=?",
                            (track_id,)).fetchone()[0] == "rec-a"
