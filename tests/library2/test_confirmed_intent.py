"""Confirmed Search intent is materialized before analysis starts."""

from __future__ import annotations

import pytest

from core.library2.confirmed_intent import (
    is_confirmed_search_process,
    materialize_confirmed_search_tracks,
)
from core.library2.monitor_rules import PROVENANCE_USER


def _default_profile_id(conn):
    return conn.execute(
        "SELECT id FROM quality_profiles ORDER BY is_default DESC, id LIMIT 1"
    ).fetchone()[0]


def test_search_process_prefixes_are_narrow():
    assert is_confirmed_search_process("enhanced_search_album_123")
    assert is_confirmed_search_process("gsearch_track_123")
    assert not is_confirmed_search_process("artist_album_123")
    assert not is_confirmed_search_process("wishlist")


def test_materializes_and_correlates_selected_spotify_track(imported_conn):
    profile_id = _default_profile_id(imported_conn)
    track = {
        "id": "sp-track-confirmed",
        "name": "Confirmed Track",
        "source": "spotify",
        "artists": [{"id": "sp-artist-confirmed", "name": "Confirmed Artist"}],
        "album": {
            "id": "sp-album-confirmed",
            "name": "Confirmed Album",
            "album_type": "album",
        },
        "track_number": 2,
        "disc_number": 1,
    }

    first = materialize_confirmed_search_tracks(
        imported_conn,
        [track],
        explicit_profile_id=profile_id,
        correlation_id="batch-123",
    )[0]
    second = materialize_confirmed_search_tracks(
        imported_conn,
        [track],
        explicit_profile_id=profile_id,
        correlation_id="batch-456",
    )[0]

    assert first["lib2_track_id"] == second["lib2_track_id"]
    assert first["quality_profile_id"] == profile_id
    assert first["quality_profile_source"] == "track"
    assert first["source_info"]["intent_correlation_id"] == "batch-123"
    assert second["source_info"]["intent_correlation_id"] == "batch-456"

    row = imported_conn.execute(
        """SELECT t.spotify_id AS track_spotify_id,
                  al.spotify_id AS album_spotify_id,
                  a.spotify_id AS artist_spotify_id
             FROM lib2_tracks t
             JOIN lib2_albums al ON al.id=t.album_id
             JOIN lib2_artists a ON a.id=al.primary_artist_id
            WHERE t.id=?""",
        (first["lib2_track_id"],),
    ).fetchone()
    assert dict(row) == {
        "track_spotify_id": "sp-track-confirmed",
        "album_spotify_id": "sp-album-confirmed",
        "artist_spotify_id": "sp-artist-confirmed",
    }
    assert imported_conn.execute(
        "SELECT COUNT(*) FROM lib2_tracks WHERE spotify_id='sp-track-confirmed'"
    ).fetchone()[0] == 1
    rule = imported_conn.execute(
        """SELECT monitored, provenance FROM lib2_monitor_rules
            WHERE entity_type='track' AND entity_id=? AND profile_id=1""",
        (first["lib2_track_id"],),
    ).fetchone()
    assert tuple(rule) == (1, PROVENANCE_USER)


def test_non_spotify_ids_are_not_written_to_legacy_spotify_columns(imported_conn):
    profile_id = _default_profile_id(imported_conn)
    result = materialize_confirmed_search_tracks(
        imported_conn,
        [{
            "id": "42",
            "name": "Provider Track",
            "source": "deezer",
            "artists": [{"id": "43", "name": "Provider Artist"}],
            "album": {"id": "44", "name": "Provider Album"},
        }],
        explicit_profile_id=profile_id,
    )[0]

    row = imported_conn.execute(
        """SELECT t.spotify_id AS track_spotify_id,
                  al.spotify_id AS album_spotify_id,
                  a.spotify_id AS artist_spotify_id
             FROM lib2_tracks t
             JOIN lib2_albums al ON al.id=t.album_id
             JOIN lib2_artists a ON a.id=al.primary_artist_id
            WHERE t.id=?""",
        (result["lib2_track_id"],),
    ).fetchone()
    assert tuple(row) == (None, None, None)
    namespaced = imported_conn.execute(
        """SELECT t.external_ids AS track_ids,
                  al.external_ids AS album_ids,
                  a.external_ids AS artist_ids
             FROM lib2_tracks t
             JOIN lib2_albums al ON al.id=t.album_id
             JOIN lib2_artists a ON a.id=al.primary_artist_id
            WHERE t.id=?""",
        (result["lib2_track_id"],),
    ).fetchone()
    import json

    assert json.loads(namespaced["track_ids"])["deezer"] == "42"
    assert json.loads(namespaced["album_ids"])["deezer"] == "44"
    assert json.loads(namespaced["artist_ids"])["deezer"] == "43"


@pytest.mark.parametrize(
    "tracks, message",
    [
        ([{"name": "No Context"}], "requires artist, album and track metadata"),
        (["not an object"], "must be an object"),
    ],
)
def test_rejects_unmaterializable_payloads(imported_conn, tracks, message):
    profile_id = _default_profile_id(imported_conn)
    with pytest.raises(ValueError, match=message):
        materialize_confirmed_search_tracks(
            imported_conn,
            tracks,
            explicit_profile_id=profile_id,
        )


def test_rejects_unknown_quality_profile(imported_conn):
    with pytest.raises(ValueError, match="unknown quality_profile_id"):
        materialize_confirmed_search_tracks(
            imported_conn,
            [{
                "name": "Track",
                "artists": [{"name": "Artist"}],
                "album": {"name": "Album"},
            }],
            explicit_profile_id=999_999,
        )
