"""Repair migration: watchlist iTunes ids that are actually Deezer ids.

_match_to_itunes used to search the PRIMARY source's client (the misnamed
MetadataService.itunes slot) and store that source's artist id in the
itunes column — with a Deezer primary, Deezer ids landed as "iTunes" ids
(verified in a live DB: 6 of 9 rows). Since the backfill only fills EMPTY
ids, the migration must clear the corrupted ones (signature: itunes ==
deezer) so the fixed matcher can re-fill them.
"""

from __future__ import annotations

import sqlite3

import database.music_database as mdb
from database.music_database import MusicDatabase


def _open_raw(db_path):
    return sqlite3.connect(db_path)


def _reinit(db_path):
    """Schema init runs once per process per path (module memo) — clear the
    memo so re-construction replays the migration block like a real app
    restart (fresh process) would."""
    mdb._database_initialized_paths.clear()
    return MusicDatabase(db_path)


def test_deezer_as_itunes_ids_cleared_and_legit_kept(tmp_path):
    db_path = str(tmp_path / 'm.db')
    MusicDatabase(db_path)  # create schema (migration runs, table empty)

    conn = _open_raw(db_path)
    c = conn.cursor()
    c.execute("""INSERT INTO watchlist_artists (artist_name, spotify_artist_id, itunes_artist_id, deezer_artist_id)
                 VALUES ('Taylor Swift', 'sp1', '12246', '12246')""")        # corrupted
    c.execute("""INSERT INTO watchlist_artists (artist_name, spotify_artist_id, itunes_artist_id, deezer_artist_id)
                 VALUES ('Eminem', 'sp2', '111051', '13')""")                # legit
    c.execute("""INSERT INTO watchlist_artists (artist_name, spotify_artist_id, itunes_artist_id, deezer_artist_id)
                 VALUES ('NoDeezer', 'sp3', '999', NULL)""")                 # no deezer id -> keep
    conn.commit()
    conn.close()

    _reinit(db_path)  # like an app restart -> migration sweeps existing rows

    conn = _open_raw(db_path)
    c = conn.cursor()
    c.execute("SELECT artist_name, itunes_artist_id FROM watchlist_artists ORDER BY artist_name")
    got = dict(c.fetchall())
    conn.close()

    assert got['Taylor Swift'] is None          # corruption cleared
    assert got['Eminem'] == '111051'            # real id untouched
    assert got['NoDeezer'] == '999'             # equality needs a deezer id


def test_migration_idempotent(tmp_path):
    db_path = str(tmp_path / 'm.db')
    MusicDatabase(db_path)
    conn = _open_raw(db_path)
    conn.execute("""INSERT INTO watchlist_artists (artist_name, spotify_artist_id, itunes_artist_id, deezer_artist_id)
                    VALUES ('A', 'sp', '5', '5')""")
    conn.commit()
    conn.close()
    _reinit(db_path)
    _reinit(db_path)  # second run: nothing left to clear, no error
    conn = _open_raw(db_path)
    val = conn.execute("SELECT itunes_artist_id FROM watchlist_artists").fetchone()[0]
    conn.close()
    assert val is None
