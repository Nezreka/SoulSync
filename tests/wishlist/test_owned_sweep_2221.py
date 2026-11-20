"""#2221: the auto-wishlist cycle's library sweep at scale.

13.5k wishlist rows against a 1.13M track library: every cycle checked every
row before building a batch, each check opened fresh sqlite connections (the
pool lost them, see test_db_connection_pool), and the cycle never reached a
batch. These pin the shape that replaced it: pooled connections, one read of
the manual matches, an incremental sweep (never-checked first, stamped, capped
by rows and time), and the batch checking its own unchecked tracks before it
downloads them.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import core.library_scope as scope_mod
from core.wishlist import processing as wp
from core.wishlist.service import WishlistService
from database import connection_pool as cp
from database.music_database import MusicDatabase


WISHLIST_ROWS = 20_000
LIBRARY_TRACKS = 40_000
# rows the library owns, spread across the wishlist (row index -> title)
OWNED_AT = (5, 250, 4_000, 19_990)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    d = MusicDatabase(str(tmp_path / 'lib.db'))
    monkeypatch.setattr('database.music_database.get_database', lambda *a, **k: d)
    monkeypatch.setattr('core.library2.migration_gate.migration_required', lambda database: False)
    monkeypatch.setattr('core.profile_context.get_current_profile_id', lambda: 1)
    monkeypatch.setattr('core.settings.config_manager.get_active_media_server', lambda: 'plex')
    scope_mod.invalidate_library_scope_cache()
    yield d
    scope_mod.invalidate_library_scope_cache()


@pytest.fixture()
def service(db, monkeypatch):
    svc = WishlistService()
    svc._database = db
    monkeypatch.setattr(wp, 'get_wishlist_service', lambda: svc)
    return svc


def _seed_library(db, n_tracks=LIBRARY_TRACKS, owned_titles=()):
    """a synthetic library: n_tracks across 2,000 artists, 10 tracks an
    album, every one with a live file. ``owned_titles`` are (artist, album,
    title) that get a real row too."""
    from core.library2.importer import normalize_name
    with db._get_connection() as conn:
        artists = [(f'Lib Artist {a}', normalize_name(f'Lib Artist {a}'), 'plex', f'ar{a}')
                   for a in range(2_000)]
        conn.executemany("INSERT INTO lib2_artists(name, name_key, server_source, server_id)"
                         " VALUES(?,?,?,?)", artists)
        artist_ids = [r[0] for r in conn.execute("SELECT id FROM lib2_artists ORDER BY id")]
        n_albums = n_tracks // 10
        conn.executemany(
            "INSERT INTO lib2_albums(primary_artist_id, title, origin, server_source, server_id)"
            " VALUES(?,?,'library','plex',?)",
            [(artist_ids[a % len(artist_ids)], f'Lib Album {a}', f'al{a}') for a in range(n_albums)])
        album_rows = conn.execute("SELECT id, primary_artist_id FROM lib2_albums ORDER BY id").fetchall()
        conn.executemany(
            "INSERT INTO lib2_album_artists(album_id, artist_id, role) VALUES(?,?,'primary')",
            [(r[0], r[1]) for r in album_rows])
        conn.executemany(
            "INSERT INTO lib2_tracks(album_id, title, track_number, server_source, server_id)"
            " VALUES(?,?,?,'plex',?)",
            [(album_rows[t // 10][0], f'Lib Song {t}', t % 10 + 1, f'tr{t}') for t in range(n_tracks)])
        conn.execute(
            "INSERT INTO lib2_track_artists(track_id, artist_id, role, position)"
            " SELECT t.id, al.primary_artist_id, 'primary', 0 FROM lib2_tracks t"
            " JOIN lib2_albums al ON al.id = t.album_id")
        conn.execute(
            "INSERT INTO lib2_track_files(track_id, path, server_source, is_primary, file_state, import_status)"
            " SELECT id, '/music/' || id || '.flac', 'plex', 1, 'active', 'imported' FROM lib2_tracks")
        conn.commit()
        from tests.support.catalogue_seed import seed_library_track
        for i, (artist, album, title) in enumerate(owned_titles):
            seed_library_track(conn, artist=artist, album=album, title=title,
                               artist_server_id=f'own-ar{i}', album_server_id=f'own-al{i}',
                               track_server_id=f'own{i}', server_source='plex',
                               track_number=1, duration=200000, file_path=f'/music/own{i}.flac')
        conn.commit()


def _wish(i):
    return {'id': f'sp{i}', 'name': f'Wanted Song {i}', 'artists': [{'name': f'Wanted Artist {i % 500}'}],
            'album': {'name': f'Wanted Album {i}', 'album_type': 'single'}}


def _owned_wish(i):
    return {'id': f'sp{i}', 'name': f'Owned Song {i}', 'artists': [{'name': f'Owned Artist {i}'}],
            'album': {'name': f'Owned Album {i}', 'album_type': 'single'}}


def _seed_wishlist(db, n=WISHLIST_ROWS, owned_at=OWNED_AT):
    rows = []
    for i in range(n):
        data = _owned_wish(i) if i in owned_at else _wish(i)
        # date_added keeps row order = index order
        rows.append((data['id'], json.dumps(data), 'missing', 'playlist', '{}', 1,
                     f'2026-01-01 {i // 3600:02d}:{i // 60 % 60:02d}:{i % 60:02d}'))
    with db._get_connection() as conn:
        conn.executemany(
            "INSERT INTO wishlist_tracks(spotify_track_id, spotify_data, failure_reason, source_type,"
            " source_info, profile_id, date_added) VALUES(?,?,?,?,?,?,?)", rows)
        conn.commit()


def _owned_library(owned_at=OWNED_AT):
    return [(f'Owned Artist {i}', f'Owned Album {i}', f'Owned Song {i}') for i in owned_at]


def _profiles():
    return SimpleNamespace(get_all_profiles=lambda: [{'id': 1}])


def _stamps(db):
    with db._get_connection() as conn:
        return {r[0]: r[1] for r in conn.execute(
            "SELECT spotify_track_id, owned_checked_at FROM wishlist_tracks")}


def _wishlist_ids(db):
    with db._get_connection() as conn:
        return {r[0] for r in conn.execute("SELECT spotify_track_id FROM wishlist_tracks")}


class _Spy:
    """counts what one sweep does: rows it checked, connections it asked the
    pool for, and new connections it had to open."""

    def __init__(self, monkeypatch):
        self.checked = []
        self.acquired = 0
        real_check = wp._check_one_owned

        def check(*args, **kwargs):
            self.checked.append(args[4].get('spotify_track_id'))
            return real_check(*args, **kwargs)
        monkeypatch.setattr(wp, '_check_one_owned', check)
        real_acquire = cp.acquire

        def acquire(*a, **k):
            self.acquired += 1
            return real_acquire(*a, **k)
        monkeypatch.setattr(cp, 'acquire', acquire)
        self._misses = cp.stats['misses']

    @property
    def opened(self):
        return cp.stats['misses'] - self._misses


def _sweep(db, service, **kwargs):
    return wp.remove_tracks_already_in_library(service, _profiles(), db, 'plex', **kwargs)


def test_the_migration_adds_owned_checked_at(tmp_path):
    from database import music_database as mdb
    path = tmp_path / 'old.db'
    MusicDatabase(str(path))
    with mdb.MusicDatabase(str(path))._get_connection() as conn:
        conn.execute("ALTER TABLE wishlist_tracks DROP COLUMN owned_checked_at")
        conn.commit()
        assert 'owned_checked_at' not in {c[1] for c in conn.execute("PRAGMA table_info(wishlist_tracks)")}
    mdb._database_initialized_paths.discard(str(path.resolve()))
    again = MusicDatabase(str(path))
    with again._get_connection() as conn:
        assert 'owned_checked_at' in {c[1] for c in conn.execute("PRAGMA table_info(wishlist_tracks)")}


def test_a_capped_sweep_at_scale_reuses_connections_and_stays_bounded(db, service, monkeypatch):
    _seed_library(db, owned_titles=_owned_library())
    _seed_wishlist(db)
    spy = _Spy(monkeypatch)

    removed = _sweep(db, service, incremental=True, max_rows=300, recheck_after_s=86_400)

    assert len(spy.checked) == 300
    # the owned rows inside the first 300 went, the rest stayed
    assert removed == 2
    left = _wishlist_ids(db)
    assert 'sp5' not in left and 'sp250' not in left and 'sp4000' in left
    # nearly every connection came from the pool (#2221: every lookup opened a
    # new one, 200+ ms each on the reporter's database)
    assert spy.opened <= 4, f'{spy.opened} new sqlite connections for one sweep'
    # one sweep's database work is bounded per row, not per row x id x source
    per_row = spy.acquired / len(spy.checked)
    assert per_row <= 8, f'{per_row:.1f} connection checkouts per row'
    stamps = _stamps(db)
    assert sum(1 for v in stamps.values() if v) == 298
    assert stamps['sp299'] and not stamps['sp300']


def test_the_next_sweep_takes_the_rows_the_last_one_did_not_reach(db, service, monkeypatch):
    _seed_library(db, n_tracks=2_000, owned_titles=_owned_library())
    _seed_wishlist(db, n=5_000, owned_at=(5, 250, 4_000))
    spy = _Spy(monkeypatch)
    _sweep(db, service, incremental=True, max_rows=300, recheck_after_s=86_400)
    first = list(spy.checked)
    spy.checked.clear()
    _sweep(db, service, incremental=True, max_rows=300, recheck_after_s=86_400)
    second = list(spy.checked)
    assert first == [f'sp{i}' for i in range(300)]
    assert second == [f'sp{i}' for i in range(300, 600)]


def test_a_sweep_with_nothing_due_checks_nothing(db, service, monkeypatch):
    _seed_library(db, n_tracks=500)
    _seed_wishlist(db, n=200, owned_at=())
    _sweep(db, service)                       # a full sweep stamps every row it keeps
    assert all(_stamps(db).values())
    spy = _Spy(monkeypatch)
    assert _sweep(db, service, incremental=True, recheck_after_s=86_400) == 0
    assert spy.checked == []


def test_never_checked_rows_go_first_then_the_stalest(db, service, monkeypatch):
    _seed_library(db, n_tracks=500)
    _seed_wishlist(db, n=6, owned_at=())
    with db._get_connection() as conn:
        conn.executemany("UPDATE wishlist_tracks SET owned_checked_at = ? WHERE spotify_track_id = ?", [
            ('2026-01-03 00:00:00', 'sp0'),
            ('2026-01-01 00:00:00', 'sp1'),
            ('2099-01-01 00:00:00', 'sp2'),     # fresh: not due
        ])
        conn.commit()
    spy = _Spy(monkeypatch)
    _sweep(db, service, incremental=True, recheck_after_s=86_400)
    assert spy.checked == ['sp3', 'sp4', 'sp5', 'sp1', 'sp0']


def test_the_time_budget_stops_the_sweep(db, service, monkeypatch):
    _seed_library(db, n_tracks=500)
    _seed_wishlist(db, n=100, owned_at=())
    spy = _Spy(monkeypatch)
    ticks = iter(range(10_000))
    _sweep(db, service, incremental=True, time_budget_s=10, recheck_after_s=86_400,
           clock=lambda: float(next(ticks)))
    # clock: 0 at the start, then once before each row; row k starts at k+1
    assert len(spy.checked) == 9
    assert sum(1 for v in _stamps(db).values() if v) == 9


def test_a_full_sweep_still_checks_every_row(db, service, monkeypatch):
    _seed_library(db, n_tracks=500, owned_titles=_owned_library((3, 40)))
    _seed_wishlist(db, n=60, owned_at=(3, 40))
    spy = _Spy(monkeypatch)
    assert _sweep(db, service) == 2
    assert len(spy.checked) == 60


def test_the_batch_checks_the_tracks_the_sweep_did_not_reach(db, service, monkeypatch):
    _seed_library(db, n_tracks=500, owned_titles=_owned_library((3, 40)))
    _seed_wishlist(db, n=60, owned_at=(3, 40))
    _sweep(db, service, incremental=True, max_rows=10, recheck_after_s=86_400)
    assert 'sp3' not in _wishlist_ids(db)          # the sweep reached it
    assert 'sp40' in _wishlist_ids(db)             # it did not reach this one

    tracks = service.get_wishlist_tracks_for_download(profile_id=1)
    spy = _Spy(monkeypatch)
    owned = wp.check_batch_tracks_owned(db, 1, tracks, 'plex', recheck_after_s=86_400)

    by_id = {t['spotify_track_id']: t for t in tracks}
    assert set(owned) == {id(by_id['sp40'])}
    assert getattr(owned[id(by_id['sp40'])], 'title', None) == 'Owned Song 40'
    assert 'sp40' not in _wishlist_ids(db)
    # the rows the sweep already checked are not checked twice
    assert not set(spy.checked) & {f'sp{i}' for i in range(10)}
    # 59 rows left, 9 of them kept and stamped by the sweep
    assert len(spy.checked) == 59 - 9


def test_the_batch_check_leaves_an_upgrade_wish_alone(db, service, monkeypatch):
    _seed_library(db, n_tracks=100, owned_titles=_owned_library((1,)))
    _seed_wishlist(db, n=3, owned_at=(1,))
    with db._get_connection() as conn:
        conn.execute("UPDATE wishlist_tracks SET source_info = ? WHERE spotify_track_id = 'sp1'",
                     (json.dumps({'source': 'library_v2', 'upgrade_check': True, 'lib2_track_id': 1}),))
        conn.commit()
    tracks = service.get_wishlist_tracks_for_download(profile_id=1)
    assert wp.check_batch_tracks_owned(db, 1, tracks, 'plex') == {}
    assert 'sp1' in _wishlist_ids(db)


def test_the_auto_cycle_runs_an_incremental_budgeted_sweep(monkeypatch):
    """the cycle passes the limits through; it never runs the old full sweep"""
    seen = {}

    def _sweep_spy(*a, **k):
        seen.update(k)
        return 0
    monkeypatch.setattr(wp, 'remove_tracks_already_in_library', _sweep_spy)
    monkeypatch.setattr(wp, 'owned_sweep_limits', lambda: (60.0, 5000, 86_400.0))
    from tests.downloads import test_backend_review_wishlist_h9 as h9
    order = []
    monkeypatch.setattr(wp, 'get_wishlist_service', lambda: h9._make_fake_wishlist_service(order))
    wp.process_wishlist_automatically(h9._auto_runtime(order))
    assert seen.get('incremental') is True
    assert seen.get('time_budget_s') == 60.0
    assert seen.get('max_rows') == 5000


def test_auto_batches_ask_for_the_check_before_download():
    rt = SimpleNamespace(
        logger=wp.logger, tasks_lock=__import__('threading').Lock(), download_batches={},
        current_time_fn=lambda: 0, album_bundle_executor=None,
        missing_download_executor=SimpleNamespace(submit=lambda *a, **k: None),
        run_full_missing_tracks_process=lambda *a, **k: None,
        get_batch_max_concurrent=lambda: 3, profile_id=1)
    tracks = [dict(_wish(1), spotify_track_id='sp1', profile_id=1)]
    wp._run_wishlist_cycle(rt, playlist_id='wishlist', cycle='singles', tracks=tracks,
                           run_id='r', auto_initiated=True)
    assert [b.get('check_owned_before_download') for b in rt.download_batches.values()] == [True]
    rt.download_batches.clear()
    wp._run_wishlist_cycle(rt, playlist_id='wishlist', cycle='singles', tracks=tracks,
                           run_id='r', auto_initiated=False)
    assert [b.get('check_owned_before_download') for b in rt.download_batches.values()] == [None]


def test_the_manual_match_index_answers_like_the_database(db):
    rows = [
        (1, 'spotify', 'a1', 'Song A', 'Artist A', ''),
        (1, 'wishlist', 'a1', 'Song A', 'Artist A', 'plex'),
        (1, 'mirrored', 'b2', 'Ünïcode Song', 'The Band', ''),
        (1, 'spotify', 'c3', 'Other', 'Someone', 'jellyfin'),
        (2, 'spotify', 'a1', 'Song A', 'Artist A', ''),
    ]
    for pid, source, sid, title, artist, server in rows:
        assert db.save_manual_library_match(pid, source, sid, '101', source_title=title,
                                            source_artist=artist, server_source=server)
    from core.library.manual_library_match import ManualMatchIndex
    index = ManualMatchIndex.load(db, 1)

    def same(a, b):
        return (a or {}).get('id') == (b or {}).get('id')

    for args in [(1, 'spotify', 'a1', ''), (1, 'wishlist', 'a1', 'plex'), (1, 'wishlist', 'a1', ''),
                 (1, 'spotify', 'zz', '')]:
        assert same(index.get_manual_library_match(*args), db.get_manual_library_match(*args)), args
    for args in [(1, 'a1', ''), (1, 'a1', 'plex'), (1, 'c3', ''), (1, 'c3', 'jellyfin'), (1, 'nope', '')]:
        assert same(index.find_manual_library_match_by_source_track_id(*args),
                    db.find_manual_library_match_by_source_track_id(*args)), args
    for args in [(1, 'song a', 'ARTIST A', ''), (1, 'ünïcode song', 'the band', ''),
                 (1, 'Ünïcode Song', 'THE BAND', 'plex'), (1, 'Other', 'Someone', '')]:
        assert same(index.find_manual_library_match_by_metadata(*args),
                    db.find_manual_library_match_by_metadata(*args)), args
    # one profile's index never answers for another profile
    assert db.get_manual_library_match(2, 'spotify', 'a1', '')
    assert index.get_manual_library_match(2, 'spotify', 'a1', '') is None
