"""Listening history, play counts and the media-server link.

A play count arrives keyed by the media server's own id, and a history row has
only a title and an artist name. Both have to land on a catalogue row, so v2
carries the server's id (`server_source` + `server_id`) and `listening_history`
carries the catalogue id next to the server one (§50.4.4.25).
"""

from __future__ import annotations

import json

import pytest

from core.library2.importer import normalize_name
from core.listening_stats_worker import ListeningStatsWorker
from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path) -> MusicDatabase:
    return MusicDatabase(database_path=str(tmp_path / "listening.db"))


def _track(db, *, title='Uprising', artist='Muse', server_source=None,
           server_id=None, genres=None, legacy_track_id=None) -> int:
    conn = db._get_connection()
    try:
        artist_id = conn.execute(
            "INSERT INTO lib2_artists(name, name_key, genres) VALUES(?,?,?)",
            (artist, normalize_name(artist), json.dumps(genres or []))).lastrowid
        album_id = conn.execute(
            "INSERT INTO lib2_albums(primary_artist_id, title, origin) "
            "VALUES(?,'The Resistance','library')", (artist_id,)).lastrowid
        track_id = conn.execute(
            "INSERT INTO lib2_tracks(album_id, title, server_source, server_id,"
            "                        legacy_track_id) VALUES(?,?,?,?,?)",
            (album_id, title, server_source, server_id, legacy_track_id)).lastrowid
        conn.commit()
        return int(track_id)
    finally:
        conn.close()


def _worker(db) -> ListeningStatsWorker:
    worker = ListeningStatsWorker.__new__(ListeningStatsWorker)
    worker.db = db
    return worker


# ── play counts ────────────────────────────────────────────────────────────

def test_play_counts_reach_the_catalogue_through_the_server_id(db):
    track_id = _track(db, server_source='plex', server_id='rk-42')

    updates = _worker(db)._map_play_counts_to_db({'rk-42': 7}, 'plex')

    assert updates == [{'db_track_id': 'rk-42', 'lib2_track_id': track_id,
                        'play_count': 7, 'last_played': None}]


def test_a_server_id_only_counts_for_its_own_server(db):
    """Rating keys are small integers — two servers hand out the same ones."""
    _track(db, server_source='plex', server_id='7')

    assert _worker(db)._map_play_counts_to_db({'7': 3}, 'jellyfin') == []


def test_play_counts_use_requested_mapping_when_another_server_was_seen_last(db):
    track_id = _track(db, server_source='jellyfin', server_id='j-track')
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO lib2_media_server_mappings "
            "(entity_type,entity_id,server_source,server_id) "
            "VALUES('track',?,'plex','p-track')", (track_id,),
        )

    updates = _worker(db)._map_play_counts_to_db({'p-track': 4}, 'plex')

    assert updates[0]['lib2_track_id'] == track_id


def test_a_track_the_catalogue_never_saw_is_skipped(db):
    _track(db, server_source='plex', server_id='rk-1')

    assert _worker(db)._map_play_counts_to_db({'unknown': 5}, 'plex') == []


def test_update_track_play_counts_writes_the_catalogue_row(db):
    track_id = _track(db, server_source='plex', server_id='rk-42')

    db.update_track_play_counts([
        {'db_track_id': 'rk-42', 'lib2_track_id': track_id,
         'play_count': 9, 'last_played': '2026-08-12T10:00:00'}])

    conn = db._get_connection()
    try:
        row = conn.execute(
            "SELECT play_count, last_played FROM lib2_tracks WHERE id=?",
            (track_id,)).fetchone()
    finally:
        conn.close()
    assert (row[0], row[1]) == (9, '2026-08-12T10:00:00')


# ── history rows ───────────────────────────────────────────────────────────

def test_a_history_event_stores_the_catalogue_id(db):
    track_id = _track(db)

    db.insert_listening_events([{
        'track_id': 'rk-42', 'title': 'Uprising', 'artist': 'Muse',
        'album': 'The Resistance', 'played_at': '2026-08-12T10:00:00',
        'server_source': 'plex', 'lib2_track_id': track_id,
    }])

    conn = db._get_connection()
    try:
        assert conn.execute(
            "SELECT lib2_track_id FROM listening_history").fetchone()[0] == track_id
    finally:
        conn.close()


def test_the_history_resolver_matches_across_accents(db):
    """A server reports whatever its tags say; the catalogue's fold is the one
    that has to be forgiving (iss29-D13)."""
    track_id = _track(db, title='Hyperballad', artist='Björk')

    resolved = _worker(db)._resolve_db_track_ids_batch(
        [{'title': 'hyperballad', 'artist': 'BJÖRK'}])

    assert resolved == {('hyperballad', 'björk'): track_id}


def test_genre_breakdown_reads_the_catalogue(db):
    track_id = _track(db, genres=['Rock', 'Alternative'])
    db.insert_listening_events([{
        'track_id': 'rk-42', 'title': 'Uprising', 'artist': 'Muse',
        'played_at': '2026-08-12T10:00:00', 'server_source': 'plex',
        'lib2_track_id': track_id,
    }])

    genres = {g['genre'] for g in db.get_genre_breakdown('all')}

    assert genres == {'Rock', 'Alternative'}


def test_old_history_rows_find_their_catalogue_row(db):
    """Rows written before the catalogue link existed carry the LEGACY track id.
    lib2's own back-reference maps them over — no legacy table is read for it,
    and running the migration twice changes nothing."""
    track_id = _track(db, legacy_track_id=555)
    conn = db._get_connection()
    try:
        conn.execute(
            "INSERT INTO listening_history(track_id, title, artist, played_at,"
            "                              server_source, db_track_id)"
            " VALUES('rk-9','Uprising','Muse','2026-08-01T10:00:00','plex',555)")
        conn.commit()
    finally:
        conn.close()

    db._initialize_database()  # idempotent; carries the backfill

    conn = db._get_connection()
    try:
        assert conn.execute(
            "SELECT lib2_track_id FROM listening_history").fetchone()[0] == track_id
    finally:
        conn.close()
