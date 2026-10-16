"""a refetch replaces an artist's similar artists instead of adding to them.

the old upsert only ever added: a wrong match from an older fetch (Colde
stored for Cold, a thai pop act as Korn) sat next to the right one forever,
and every refetch bumped occurrence_count, which the reader sums.
"""

from __future__ import annotations

import types

from database.music_database import MusicDatabase


def _rows(db, source='seed-1', profile=1):
    with db._get_connection() as conn:
        cur = conn.execute(
            "SELECT similar_artist_name, similar_artist_deezer_id, occurrence_count, last_featured "
            "FROM similar_artists WHERE source_artist_id = ? AND profile_id = ? "
            "ORDER BY similarity_rank", (source, profile))
        return [tuple(r) for r in cur.fetchall()]


def _seed_old(db):
    db.add_or_update_similar_artist(source_artist_id='seed-1', similar_artist_name='Colde',
                                    similar_artist_deezer_id='13423415', profile_id=1)
    db.add_or_update_similar_artist(source_artist_id='seed-1', similar_artist_name='Incubus',
                                    similar_artist_deezer_id='1', profile_id=1)
    # a second fetch: the upsert bumps the count
    db.add_or_update_similar_artist(source_artist_id='seed-1', similar_artist_name='Incubus',
                                    similar_artist_deezer_id='1', profile_id=1)
    with db._get_connection() as conn:
        conn.execute("UPDATE similar_artists SET last_featured = '2026-10-01 12:00:00' "
                     "WHERE similar_artist_name = 'Incubus'")
        conn.commit()


def test_the_fresh_set_replaces_the_old_one(tmp_path):
    db = MusicDatabase(str(tmp_path / 'music.db'))
    _seed_old(db)
    stored = db.replace_similar_artists('seed-1', [
        dict(similar_artist_name='Cold', similar_artist_deezer_id='318', similarity_rank=1),
        dict(similar_artist_name='Incubus', similar_artist_deezer_id='1', similarity_rank=2),
    ], profile_id=1)
    assert stored == 2
    # Colde is gone, the count is one again, the hero's rotation history stays
    assert _rows(db) == [('Cold', '318', 1, None), ('Incubus', '1', 1, '2026-10-01 12:00:00')]


def test_an_empty_fetch_leaves_the_old_set(tmp_path):
    db = MusicDatabase(str(tmp_path / 'music.db'))
    _seed_old(db)
    before = _rows(db)
    assert db.replace_similar_artists('seed-1', [], profile_id=1) == 0
    assert _rows(db) == before


def test_other_artists_and_profiles_are_untouched(tmp_path):
    db = MusicDatabase(str(tmp_path / 'music.db'))
    _seed_old(db)
    db.add_or_update_similar_artist(source_artist_id='seed-2', similar_artist_name='Korn',
                                    similar_artist_deezer_id='1327', profile_id=1)
    db.add_or_update_similar_artist(source_artist_id='seed-1', similar_artist_name='Colde',
                                    similar_artist_deezer_id='13423415', profile_id=2)
    db.replace_similar_artists('seed-1', [dict(similar_artist_name='Cold', similar_artist_deezer_id='318')],
                               profile_id=1)
    assert [r[0] for r in _rows(db, 'seed-2')] == ['Korn']
    assert [r[0] for r in _rows(db, 'seed-1', profile=2)] == ['Colde']


def test_the_rematch_migration_requeues_everything_once(tmp_path):
    db = MusicDatabase(str(tmp_path / 'music.db'))
    _seed_old(db)
    with db._get_connection() as conn:
        conn.execute("INSERT INTO artists (name, server_source, deezer_id, similar_artists_match_status) "
                     "VALUES ('Audioslave', 'plex', '1', 'matched')")
        conn.execute("DROP TABLE _similar_artists_rematch_applied")
        db._requeue_similar_artists_rematch(conn.cursor())
        conn.commit()
        status = conn.execute("SELECT similar_artists_match_status FROM artists").fetchone()[0]
    assert status is None                                    # the worker runs it again
    assert {r[2] for r in _rows(db)} == {1}                  # inflated counts reset
    assert db.has_fresh_similar_artists('seed-1', profile_id=1) is False  # watchlist refetches

    # once: a second startup leaves the new results alone
    with db._get_connection() as conn:
        conn.execute("UPDATE artists SET similar_artists_match_status = 'matched'")
        db._requeue_similar_artists_rematch(conn.cursor())
        conn.commit()
        status = conn.execute("SELECT similar_artists_match_status FROM artists").fetchone()[0]
    assert status == 'matched'


def test_a_watchlist_refresh_replaces_too(tmp_path, monkeypatch):
    from core.watchlist_scanner import WatchlistScanner

    db = MusicDatabase(str(tmp_path / 'music.db'))
    _seed_old(db)
    scanner = WatchlistScanner(metadata_service=types.SimpleNamespace(spotify=None, itunes=None))
    scanner._database = db
    monkeypatch.setattr(scanner, '_fetch_similar_artists_from_musicmap', lambda name, limit=10: [
        {'name': 'Cold', 'deezer_id': '318'},
        {'name': 'Incubus', 'deezer_id': '1'},
    ])
    artist = types.SimpleNamespace(artist_name='Audioslave')
    assert scanner.update_similar_artists(artist, profile_id=1, source_artist_id='seed-1',
                                          source_provider='deezer') is True
    assert [r[0] for r in _rows(db)] == ['Cold', 'Incubus']
