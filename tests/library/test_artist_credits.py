"""every artist credited on a track, kept by the enrichment workers.

a user compared our schema to navidrome's: tracks.artist_id is one artist, so
"Calvin Harris feat. Rihanna" only exists under calvin and rihanna's page never
knew about it. spotify and deezer already hand back every credited artist with
an id when the workers match a track, the workers just threw that away.

now the credits land in track_artist_credits (one row per artist per source),
link to library artists through the provider id columns at read time, and
never create artist rows for people who aren't in the library.
"""

import sqlite3
import types

import pytest
from flask import Flask

import core.deezer_worker as deezer_worker_mod
import core.spotify_worker as spotify_worker_mod
from core.deezer_worker import DeezerWorker
from core.library import artist_credits as ac
from core.library_scope import reset_library_scope, set_library_scope
from core.spotify_worker import SpotifyWorker
from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(database_path=str(tmp_path / "music.db"))


def _raw(db):
    return sqlite3.connect(str(db.database_path))


def _seed(db):
    """calvin owns the album, rihanna is in the library too (matched on both
    sources). drake only ever shows up as a feature."""
    c = _raw(db)
    c.execute("INSERT INTO artists (id, name, server_source, spotify_artist_id, deezer_id) "
              "VALUES (1, 'Calvin Harris', 'plex', 'sp_calvin', 'dz_calvin')")
    c.execute("INSERT INTO artists (id, name, server_source, spotify_artist_id, deezer_id) "
              "VALUES (2, 'Rihanna', 'plex', 'sp_rihanna', 'dz_rihanna')")
    c.execute("INSERT INTO albums (id, artist_id, title, year, server_source, spotify_album_id) "
              "VALUES (10, 1, '18 Months', 2012, 'plex', 'sp_album_18')")
    c.execute("INSERT INTO albums (id, artist_id, title, year, server_source) "
              "VALUES (20, 2, 'Anti', 2016, 'plex')")
    c.execute("INSERT INTO tracks (id, album_id, artist_id, title, track_number, server_source) "
              "VALUES (100, 10, 1, 'We Found Love', 1, 'plex')")
    c.execute("INSERT INTO tracks (id, album_id, artist_id, title, track_number, server_source) "
              "VALUES (101, 10, 1, 'Feel So Close', 2, 'plex')")
    c.execute("INSERT INTO tracks (id, album_id, artist_id, title, track_number, server_source) "
              "VALUES (200, 20, 2, 'Work', 1, 'plex')")
    c.commit()
    c.close()


SP_WE_FOUND_LOVE = [{'id': 'sp_calvin', 'name': 'Calvin Harris'},
                    {'id': 'sp_rihanna', 'name': 'Rihanna'}]


def _credits(db, track_id, source=None):
    c = _raw(db)
    sql = "SELECT source, position, name, source_artist_id FROM track_artist_credits WHERE track_id = ?"
    params = [str(track_id)]
    if source:
        sql += " AND source = ?"
        params.append(source)
    rows = c.execute(sql + " ORDER BY source, position", params).fetchall()
    c.close()
    return rows


def _appears_on(db, artist_id, **kw):
    with db._get_connection() as conn:
        return ac.appears_on(conn.cursor(), artist_id, **kw)


# ---------------------------------------------------------------------------
# schema + the core helper
# ---------------------------------------------------------------------------

def test_fresh_db_has_table_index_and_triggers(db):
    c = _raw(db)
    names = {r[0] for r in c.execute("SELECT name FROM sqlite_master")}
    c.close()
    for n in ("track_artist_credits", "track_artist_credits_pending", "idx_track_artist_credits_artist",
              "trg_track_artist_credits_gone",
              "trg_track_artist_credits_spotify_rematch",
              "trg_track_artist_credits_deezer_rematch"):
        assert n in names, n


def test_featured_track_shows_up_on_the_featured_artists_page(db):
    """the report: rihanna's page never saw a track filed under calvin."""
    _seed(db)
    with db._get_connection() as conn:
        assert ac.save_track_credits(conn.cursor(), 100, 'spotify', SP_WE_FOUND_LOVE) == 2
        conn.commit()

    rows = _appears_on(db, 2)
    assert [r['title'] for r in rows] == ['We Found Love']
    assert rows[0]['artist_name'] == 'Calvin Harris'
    assert rows[0]['album_title'] == '18 Months'
    assert rows[0]['credits'] == ['Calvin Harris', 'Rihanna']
    # calvin's own track is already on calvin's page
    assert _appears_on(db, 1) == []


def test_features_outside_the_library_never_become_artists(db):
    _seed(db)
    with db._get_connection() as conn:
        ac.save_track_credits(conn.cursor(), 200, 'spotify',
                              [{'id': 'sp_rihanna', 'name': 'Rihanna'},
                               {'id': 'sp_drake', 'name': 'Drake'}])
        conn.commit()
    c = _raw(db)
    assert c.execute("SELECT COUNT(*) FROM artists").fetchone()[0] == 2
    c.close()
    assert [r[2] for r in _credits(db, 200)] == ['Rihanna', 'Drake']


def test_names_without_ids_write_nothing(db):
    """names alone can't link, and a names-only row would stop the backfill
    from ever going back for the real ids."""
    _seed(db)
    with db._get_connection() as conn:
        assert ac.save_track_credits(conn.cursor(), 100, 'spotify', ['Calvin Harris', 'Rihanna']) == 0
        conn.commit()
    assert _credits(db, 100) == []


def test_saving_again_replaces_that_source_only(db):
    _seed(db)
    with db._get_connection() as conn:
        cur = conn.cursor()
        ac.save_track_credits(cur, 100, 'spotify', SP_WE_FOUND_LOVE)
        ac.save_track_credits(cur, 100, 'deezer', [{'id': 'dz_calvin', 'name': 'Calvin Harris'}])
        ac.save_track_credits(cur, 100, 'spotify', [{'id': 'sp_calvin', 'name': 'Calvin Harris'}])
        conn.commit()
    assert [(r[0], r[2]) for r in _credits(db, 100)] == [('deezer', 'Calvin Harris'),
                                                          ('spotify', 'Calvin Harris')]


def test_rematch_drops_that_sources_credits(db):
    """credits belong to the match. a manual rematch that writes a new id
    straight to the row must not leave the old track's artists behind."""
    _seed(db)
    c = _raw(db)
    c.execute("UPDATE tracks SET spotify_track_id = 'sp_t1', deezer_id = 'dz_t1' WHERE id = 100")
    c.commit()
    with db._get_connection() as conn:
        cur = conn.cursor()
        ac.save_track_credits(cur, 100, 'spotify', SP_WE_FOUND_LOVE)
        ac.save_track_credits(cur, 100, 'deezer', [{'id': 'dz_rihanna', 'name': 'Rihanna'}])
        conn.commit()

    # same id written again: nothing changed, credits stay
    c.execute("UPDATE tracks SET spotify_track_id = 'sp_t1' WHERE id = 100")
    c.commit()
    assert len(_credits(db, 100, 'spotify')) == 2

    c.execute("UPDATE tracks SET spotify_track_id = 'sp_other' WHERE id = 100")
    c.commit()
    assert _credits(db, 100, 'spotify') == []
    assert len(_credits(db, 100, 'deezer')) == 1

    c.execute("UPDATE tracks SET deezer_id = NULL WHERE id = 100")
    c.commit()
    assert _credits(db, 100) == []
    c.close()


def test_deleted_track_takes_its_credits(db):
    _seed(db)
    with db._get_connection() as conn:
        ac.save_track_credits(conn.cursor(), 100, 'spotify', SP_WE_FOUND_LOVE)
        conn.commit()
    c = _raw(db)
    c.execute("DELETE FROM tracks WHERE id = 100")
    c.commit()
    c.close()
    assert _credits(db, 100) == []


def test_appears_on_respects_library_scope(db):
    """own library per profile: profile 2's collab must not show up for
    anyone else."""
    _seed(db)
    c = _raw(db)
    c.execute("UPDATE tracks SET owner_profile_id = 2 WHERE id = 100")
    c.commit()
    c.close()
    with db._get_connection() as conn:
        ac.save_track_credits(conn.cursor(), 100, 'spotify', SP_WE_FOUND_LOVE)
        conn.commit()
    shared_sql, shared_params = db._owner_scope_sql('shared', 't.owner_profile_id')
    own_sql, own_params = db._owner_scope_sql(2, 't.owner_profile_id')
    assert _appears_on(db, 2, scope_sql=shared_sql, scope_params=shared_params) == []
    assert len(_appears_on(db, 2, scope_sql=own_sql, scope_params=own_params)) == 1


def test_one_row_per_track_when_both_sources_credit_it(db):
    _seed(db)
    with db._get_connection() as conn:
        cur = conn.cursor()
        ac.save_track_credits(cur, 100, 'spotify', SP_WE_FOUND_LOVE)
        ac.save_track_credits(cur, 100, 'deezer', [{'id': 'dz_calvin', 'name': 'Calvin Harris'},
                                                  {'id': 'dz_rihanna', 'name': 'Rihanna'}])
        conn.commit()
    assert len(_appears_on(db, 2)) == 1


def _match_all(db, col='spotify_track_id'):
    """tracks matched before credits existed: ids written, then the queue the
    trigger filled cleared, the way an existing library looks on first boot."""
    c = _raw(db)
    c.execute(f"UPDATE tracks SET {col} = 'x_' || id")
    c.execute("DELETE FROM track_artist_credits_pending")
    c.commit()
    c.close()


def _sweep(db, source='spotify', **kw):
    return ac.CreditsBackfill(source, **kw)


def _next(db, sweep):
    with db._get_connection() as conn:
        return [t[0] for t in sweep.next_batch(conn.cursor())]


def test_sweep_runs_once_and_never_again(db):
    """a sweep of a 300k track library with nothing left to do took 24s on
    boulder's db. it used to repeat every 6 hours on both workers. now it runs
    once per install; a track the source can't answer for isn't asked about
    again."""
    _seed(db)
    _match_all(db)
    sweep = _sweep(db, batch_size=2)
    assert _next(db, sweep) == ['100', '101']
    assert _next(db, sweep) == ['200']
    assert _next(db, sweep) == []
    # nothing got saved, but the sweep is done, including for a fresh worker
    assert _next(db, sweep) == []
    assert _next(db, _sweep(db, batch_size=2)) == []


def test_sweep_resumes_where_it_left_off_after_a_restart(db):
    _seed(db)
    _match_all(db)
    assert _next(db, _sweep(db, batch_size=2)) == ['100', '101']
    assert _next(db, _sweep(db, batch_size=2)) == ['200']


def test_album_backfill_hands_out_one_album_at_a_time(db):
    """spotify answers a whole album in one call. a batch of 50 tracks could
    span 50 albums, 50 calls in one item; one album per item is one call."""
    _seed(db)
    _match_all(db)
    sweep = _sweep(db, by_album=True)
    with db._get_connection() as conn:
        cur = conn.cursor()
        assert [(t[0], t[2]) for t in sweep.next_batch(cur)] == [('100', '10'), ('101', '10')]
        assert [(t[0], t[2]) for t in sweep.next_batch(cur)] == [('200', '20')]
        assert sweep.next_batch(cur) == []


def test_rematch_queues_the_track_and_the_queue_comes_first(db):
    """after the sweep, only rematched tracks get looked at, through the
    queue the trigger fills. a cleared match queues nothing."""
    _seed(db)
    _match_all(db)
    sweep = _sweep(db, batch_size=10)
    for _ in range(10):
        if not _next(db, sweep):
            break
    else:
        pytest.fail("the sweep never finished")
    c = _raw(db)
    c.execute("UPDATE tracks SET spotify_track_id = 'new' WHERE id = 101")
    c.execute("UPDATE tracks SET spotify_track_id = NULL WHERE id = 200")
    c.commit()
    assert c.execute("SELECT track_id FROM track_artist_credits_pending").fetchall() == [('101',)]
    c.close()
    assert _next(db, sweep) == ['101']
    # claimed, so a source with no answer doesn't get asked forever
    assert _next(db, sweep) == []


def test_saving_credits_clears_the_queue(db):
    """the worker's own match fires the trigger too; saving right after has
    to take the queue entry back off or every match gets fetched twice."""
    _seed(db)
    with db._get_connection() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE tracks SET spotify_track_id = 'sp_t1' WHERE id = 100")
        ac.save_track_credits(cur, 100, 'spotify', SP_WE_FOUND_LOVE)
        conn.commit()
    c = _raw(db)
    assert c.execute("SELECT COUNT(*) FROM track_artist_credits_pending").fetchone()[0] == 0
    c.close()


def test_queue_skips_tracks_that_lost_their_match(db):
    _seed(db)
    _match_all(db)
    c = _raw(db)
    c.execute("INSERT INTO track_artist_credits_pending VALUES ('100', 'spotify')")
    c.execute("UPDATE tracks SET spotify_track_id = NULL WHERE id = 100")
    c.execute("INSERT INTO track_artist_credits_pending VALUES ('999', 'spotify')")
    c.commit()
    c.close()
    sweep = _sweep(db)
    sweep._sweep_done = True
    assert _next(db, sweep) == []
    c = _raw(db)
    assert c.execute("SELECT COUNT(*) FROM track_artist_credits_pending").fetchone()[0] == 0
    c.close()


def test_deleted_track_leaves_the_queue(db):
    _seed(db)
    c = _raw(db)
    c.execute("UPDATE tracks SET spotify_track_id = 'sp_t1' WHERE id = 100")
    c.execute("DELETE FROM tracks WHERE id = 100")
    c.commit()
    assert c.execute("SELECT COUNT(*) FROM track_artist_credits_pending").fetchone()[0] == 0
    c.close()


# ---------------------------------------------------------------------------
# spotify worker seam
# ---------------------------------------------------------------------------

def _spotify_worker(db, monkeypatch, client=None, cache=None):
    # the real constructor, so the backfill it sets up is the one under test
    monkeypatch.setattr(spotify_worker_mod, 'SpotifyClient', lambda: client or types.SimpleNamespace())
    w = SpotifyWorker(db)
    w.batch_inter_item_sleep = 0
    cache = cache or {}
    # the metadata cache lives in the shared db; never touch it from here
    monkeypatch.setattr(spotify_worker_mod, 'get_metadata_cache', lambda: types.SimpleNamespace(
        get_entity=lambda source, etype, eid: cache.get(eid)))
    monkeypatch.setattr(spotify_worker_mod, 'interruptible_sleep', lambda *_a, **_k: None)
    return w


def test_spotify_album_batch_saves_every_credited_artist(db, monkeypatch):
    """the real batch path: get_album_tracks items carry artists with ids."""
    _seed(db)
    client = types.SimpleNamespace(get_album_tracks=lambda album_id: {'items': [
        {'id': '4tCtwWceOPWzenK2HAIJSb', 'name': 'We Found Love', 'track_number': 1,
         'artists': SP_WE_FOUND_LOVE},
        {'id': '3xQwDQEuDgqvITe5VbRWbx', 'name': 'Feel So Close', 'track_number': 2,
         'artists': [{'id': 'sp_calvin', 'name': 'Calvin Harris'}]},
    ]})
    w = _spotify_worker(db, monkeypatch, client=client)
    w._process_track_batch({'album_id': 10, 'spotify_album_id': 'sp_album_18',
                            'album_name': '18 Months', 'artist_name': 'Calvin Harris'})

    assert [r[3] for r in _credits(db, 100)] == ['sp_calvin', 'sp_rihanna']
    assert [r[3] for r in _credits(db, 101)] == ['sp_calvin']
    assert [r['title'] for r in _appears_on(db, 2)] == ['We Found Love']


def test_spotify_search_match_takes_ids_from_the_cached_raw_track(db, monkeypatch):
    """the Track dataclass keeps names only; the raw search item is cached."""
    _seed(db)
    w = _spotify_worker(db, monkeypatch, cache={'sp_t1': {'id': 'sp_t1', 'artists': SP_WE_FOUND_LOVE}})
    w._update_track_from_search(100, types.SimpleNamespace(id='sp_t1'))
    assert [r[3] for r in _credits(db, 100)] == ['sp_calvin', 'sp_rihanna']


def test_spotify_stored_match_reads_raw_data_not_the_flattened_names(db, monkeypatch):
    """official get_track_details flattens artists to names, raw_data keeps ids."""
    _seed(db)
    w = _spotify_worker(db, monkeypatch)
    w._refresh_track_via_stored_id(100, 'sp_t1', {
        'id': 'sp_t1', 'artists': ['Calvin Harris', 'Rihanna'],
        'raw_data': {'id': 'sp_t1', 'artists': SP_WE_FOUND_LOVE},
    })
    assert [r[3] for r in _credits(db, 100)] == ['sp_calvin', 'sp_rihanna']


def test_spotify_credit_failure_does_not_lose_the_match(db, monkeypatch):
    _seed(db)
    w = _spotify_worker(db, monkeypatch)

    def boom(*_a, **_k):
        raise sqlite3.OperationalError("no such table: track_artist_credits")
    monkeypatch.setattr(ac, 'save_track_credits', boom)
    w._update_track(100, {'id': 'sp_t1', 'artists': SP_WE_FOUND_LOVE})
    c = _raw(db)
    assert c.execute("SELECT spotify_track_id, spotify_match_status FROM tracks WHERE id = 100").fetchone() == \
        ('sp_t1', 'matched')
    c.close()


def test_spotify_backfill_covers_tracks_matched_before_credits(db, monkeypatch):
    """a library enriched before this shipped: one album call covers the album,
    a track on an unmatched album falls back to the cache."""
    _seed(db)
    c = _raw(db)
    c.execute("UPDATE tracks SET spotify_track_id = 'sp_wfl', spotify_match_status = 'matched' WHERE id = 100")
    c.execute("UPDATE tracks SET spotify_track_id = 'sp_work', spotify_match_status = 'matched' WHERE id = 200")
    c.commit()
    c.close()
    calls = []

    def get_album_tracks(album_id):
        calls.append(album_id)
        return {'items': [{'id': 'sp_wfl', 'artists': SP_WE_FOUND_LOVE}]}
    w = _spotify_worker(db, monkeypatch, client=types.SimpleNamespace(get_album_tracks=get_album_tracks),
                        cache={'sp_work': {'id': 'sp_work', 'artists': [
                            {'id': 'sp_rihanna', 'name': 'Rihanna'}, {'id': 'sp_drake', 'name': 'Drake'}]}})
    # _is_spotify_id wants a real-looking id; the album batch guard uses it
    monkeypatch.setattr(w, '_is_spotify_id', lambda _id: True)

    for _ in range(10):
        with db._get_connection() as conn:
            batch = w._credits_backfill.next_batch(conn.cursor())
        if not batch:
            break
        w._process_credits_backfill({'type': 'credits_backfill', 'tracks': batch})
    else:
        pytest.fail("the backfill never finished")

    # one call for the matched album, none for the unmatched one
    assert calls == ['sp_album_18']
    assert [r[2] for r in _credits(db, 100)] == ['Calvin Harris', 'Rihanna']
    assert [r[2] for r in _credits(db, 200)] == ['Rihanna', 'Drake']


@pytest.mark.parametrize("details", [
    # spotify free: the raw track dict
    {'id': 'sp_work', 'artists': [{'id': 'sp_rihanna', 'name': 'Rihanna'},
                                  {'id': 'sp_drake', 'name': 'Drake'}]},
    # official: artists flattened to names, the raw dict under raw_data
    {'id': 'sp_work', 'artists': ['Rihanna', 'Drake'],
     'raw_data': {'artists': [{'id': 'sp_rihanna', 'name': 'Rihanna'},
                              {'id': 'sp_drake', 'name': 'Drake'}]}},
])
def test_spotify_search_matched_track_on_unmatched_album_fetches_the_track(db, monkeypatch, details):
    """19k of boulder's 248k spotify-matched tracks sit on albums spotify never
    matched. they matched through search, and free's search results never hit
    the cache, so without asking for the track they'd never get credits."""
    _seed(db)
    c = _raw(db)
    c.execute("UPDATE tracks SET spotify_track_id = 'sp_work' WHERE id = 200")
    c.commit()
    c.close()
    asked = []

    def get_track_details(track_id, allow_fallback=True):
        asked.append((track_id, allow_fallback))
        return details
    w = _spotify_worker(db, monkeypatch, client=types.SimpleNamespace(get_track_details=get_track_details))
    with db._get_connection() as conn:
        batch = w._credits_backfill.next_batch(conn.cursor())
    w._process_credits_backfill({'type': 'credits_backfill', 'tracks': batch})
    # no fallback source: itunes ids in a spotify credit would link to nobody
    assert asked == [('sp_work', False)]
    assert [r[3] for r in _credits(db, 200)] == ['sp_rihanna', 'sp_drake']


def test_spotify_rate_limit_puts_the_album_back_on_the_queue(db, monkeypatch):
    """the tracks were handed out already. a ban says nothing about them."""
    from core.spotify_client import SpotifyRateLimitError
    _seed(db)
    _match_all(db)

    def banned(_album_id):
        raise SpotifyRateLimitError("banned")
    w = _spotify_worker(db, monkeypatch, client=types.SimpleNamespace(get_album_tracks=banned))
    with db._get_connection() as conn:
        batch = w._credits_backfill.next_batch(conn.cursor())
    with pytest.raises(SpotifyRateLimitError):
        w._process_credits_backfill({'type': 'credits_backfill', 'tracks': batch})
    c = _raw(db)
    assert sorted(r[0] for r in c.execute("SELECT track_id FROM track_artist_credits_pending")) == ['100', '101']
    c.close()


def test_spotify_next_item_hands_out_backfill_after_real_work(db, monkeypatch):
    _seed(db)
    c = _raw(db)
    # everything attempted, so the only work left is the backfill
    c.execute("UPDATE artists SET spotify_match_status = 'matched'")
    c.execute("UPDATE albums SET spotify_match_status = 'matched'")
    c.execute("UPDATE tracks SET spotify_match_status = 'matched', spotify_track_id = 'sp_' || id")
    c.commit()
    c.close()
    w = _spotify_worker(db, monkeypatch)
    monkeypatch.setattr('core.worker_utils.read_enrichment_priority', lambda _s: None)
    item = w._get_next_item()
    assert item['type'] == 'credits_backfill'
    # one album per item
    assert [t[0] for t in item['tracks']] == ['100', '101']


# ---------------------------------------------------------------------------
# deezer worker seam
# ---------------------------------------------------------------------------

DZ_CONTRIBUTORS = [{'id': 'dz_calvin', 'name': 'Calvin Harris', 'role': 'Main'},
                   {'id': 'dz_rihanna', 'name': 'Rihanna', 'role': 'Featured'}]


def _deezer_worker(db, monkeypatch, client=None):
    monkeypatch.setattr(deezer_worker_mod, 'DeezerClient', lambda: client or types.SimpleNamespace())
    return DeezerWorker(db)


def test_deezer_match_saves_contributors(db, monkeypatch):
    """the full /track record has contributors; the search hit only the primary."""
    _seed(db)
    w = _deezer_worker(db, monkeypatch)
    w._update_track(100, {'id': 111, 'artist': {'id': 'dz_calvin', 'name': 'Calvin Harris'}},
                    {'id': 111, 'bpm': 124, 'contributors': DZ_CONTRIBUTORS})
    assert [r[3] for r in _credits(db, 100)] == ['dz_calvin', 'dz_rihanna']
    assert [r['title'] for r in _appears_on(db, 2)] == ['We Found Love']


def test_deezer_search_hit_alone_writes_no_credits(db, monkeypatch):
    """primary-only would look complete and the backfill would skip it."""
    _seed(db)
    w = _deezer_worker(db, monkeypatch)
    w._update_track(100, {'id': 111, 'artist': {'id': 'dz_calvin', 'name': 'Calvin Harris'}}, None)
    assert _credits(db, 100) == []


def test_deezer_backfill_fetches_the_full_track(db, monkeypatch):
    _seed(db)
    c = _raw(db)
    c.execute("UPDATE tracks SET deezer_id = '111', deezer_match_status = 'matched' WHERE id = 100")
    c.commit()
    c.close()
    asked = []

    def get_track_raw(tid):
        asked.append(tid)
        return {'id': 111, 'contributors': DZ_CONTRIBUTORS}
    w = _deezer_worker(db, monkeypatch, types.SimpleNamespace(get_track_raw=get_track_raw))
    with db._get_connection() as conn:
        batch = w._credits_backfill.next_batch(conn.cursor())
    w._process_item({'type': 'credits_backfill', 'id': batch[0][0], 'name': 'x', 'tracks': batch})
    assert asked == ['111']
    assert [r[2] for r in _credits(db, 100)] == ['Calvin Harris', 'Rihanna']


# ---------------------------------------------------------------------------
# endpoint
# ---------------------------------------------------------------------------

def test_appears_on_endpoint(db):
    from api import artist_detail
    _seed(db)
    with db._get_connection() as conn:
        ac.save_track_credits(conn.cursor(), 100, 'spotify', SP_WE_FOUND_LOVE)
        conn.commit()
    prev = artist_detail.get_database
    artist_detail.configure(get_database=lambda: db)
    token = set_library_scope(None)
    try:
        app = Flask(__name__)
        app.register_blueprint(artist_detail.bp)
        resp = app.test_client().get('/api/artist/2/appears-on')
    finally:
        reset_library_scope(token)
        artist_detail.configure(get_database=prev)
    body = resp.get_json()
    assert resp.status_code == 200, body
    assert [t['title'] for t in body['tracks']] == ['We Found Love']
    assert body['tracks'][0]['credits'] == ['Calvin Harris', 'Rihanna']
