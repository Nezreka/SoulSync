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


def test_track_mbid_update_preserves_comment_only_for_same_recording(tmp_path):
    path = tmp_path / "tracks.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE tracks (id TEXT PRIMARY KEY, musicbrainz_recording_id TEXT, "
                     "recording_disambiguation TEXT, musicbrainz_last_attempted TEXT, "
                     "musicbrainz_match_status TEXT)")
        conn.execute("INSERT INTO tracks (id) VALUES ('track')")

    service = MusicBrainzService.__new__(MusicBrainzService)
    service.db = SimpleNamespace(_get_connection=lambda: sqlite3.connect(path))

    service.update_track_mbid("track", "rec-a", "matched", "acoustic")
    service.update_track_mbid("track", "rec-a", "matched")
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT recording_disambiguation FROM tracks").fetchone()[0] == "acoustic"

    service.update_track_mbid("track", "rec-b", "matched")
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT musicbrainz_recording_id, recording_disambiguation "
                            "FROM tracks").fetchone() == ("rec-b", None)

    service.update_track_mbid("track", "rec-b", "matched", "  live  ")
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT recording_disambiguation FROM tracks").fetchone()[0] == "live"
