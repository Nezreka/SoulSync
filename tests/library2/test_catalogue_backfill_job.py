"""Scheduled catalogue completion uses the album-page loader and real storage."""

import json
from types import SimpleNamespace

import pytest

from core.library2.monitor_rules import PROVENANCE_USER, record_rule
from core.library2.wanted import recompute_wanted_for_entity
from core.repair_jobs.base import JobContext


class Config:
    def __init__(self, **settings):
        self.values = {
            'repair.jobs.album_catalogue_backfill.settings': settings,
            'library_v2.track_identity_reconcile.auto_after_import': False,
        }

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


def job():
    from core.repair_jobs import get_all_jobs

    cls = get_all_jobs().get('album_catalogue_backfill')
    assert cls is not None, 'Catalogue completion must be available as a scheduled job'
    return cls()


@pytest.fixture(autouse=True)
def configured_providers(monkeypatch):
    # No real network: cached tracklists still use the real loader/persistence.
    monkeypatch.setattr('core.library2.match_status.configured_services', lambda: set())
    monkeypatch.setattr('core.library2.provider_adapters.fetch_album_tracklist', lambda *a, **kw: None)


def release(conn, title, *, owned=False, monitored=False, origin='library',
            cached=True, file_state='active'):
    artist_id = conn.execute("SELECT id FROM lib2_artists WHERE name='Drake'").fetchone()[0]
    tracks = [
        {'title': f'{title} A', 'track_number': 1, 'disc_number': 1},
        {'title': f'{title} B', 'track_number': 2, 'disc_number': 1},
    ]
    album_id = int(conn.execute(
        'INSERT INTO lib2_albums(primary_artist_id,title,album_type,origin,'
        'expected_track_count,tracklist_json,monitored,spotify_id) '
        "VALUES(?,?,'single',?,NULL,?,0,?)",
        (artist_id, title, origin, json.dumps(tracks) if cached else None, f'sp-{title}'),
    ).lastrowid)
    track_id = int(conn.execute(
        'INSERT INTO lib2_tracks(album_id,title,track_number,disc_number,monitored) '
        'VALUES(?,?,1,1,0)', (album_id, tracks[0]['title']),
    ).lastrowid)
    record_rule(conn, 'track', track_id, monitored, PROVENANCE_USER)
    if owned:
        conn.execute(
            'INSERT INTO lib2_track_files(track_id,path,file_state,owner_profile_id) VALUES(?,?,?,7)',
            (track_id, f'/music/{title}.flac', file_state),
        )
    recompute_wanted_for_entity(conn, 'album', album_id)
    return album_id


def titles(conn, album_id):
    return [r[0] for r in conn.execute(
        'SELECT title FROM lib2_tracks WHERE album_id=? ORDER BY track_number', (album_id,))]


def test_background_job_fills_owned_unmonitored_single_without_album_click(
        imported_conn, legacy_db):
    album_id = release(imported_conn, 'Owned', owned=True)
    imported_conn.commit()
    changes = []
    ctx = JobContext(legacy_db, '/music', Config(), report_change=lambda **kw: changes.append(kw))

    result = job().scan(ctx)

    assert titles(imported_conn, album_id) == ['Owned A', 'Owned B']
    missing = imported_conn.execute(
        "SELECT id,monitored FROM lib2_tracks WHERE album_id=? AND title='Owned B'", (album_id,)
    ).fetchone()
    assert missing['monitored'] == 0
    assert imported_conn.execute(
        'SELECT wanted FROM lib2_wanted_tracks WHERE track_id=? AND profile_id=1', (missing['id'],)
    ).fetchone()[0] == 0
    assert imported_conn.execute(
        'SELECT COUNT(*) FROM lib2_track_files WHERE track_id=?', (missing['id'],)
    ).fetchone()[0] == 0
    assert result.auto_fixed >= 1
    assert any(c['entity_id'] == f'lib2:{album_id}' for c in changes)


def test_a_release_that_stays_partial_is_reported_only_when_it_gains_tracks(imported_conn, legacy_db):
    album_id = release(imported_conn, 'Partial', owned=True)
    imported_conn.execute('UPDATE lib2_albums SET expected_track_count=3 WHERE id=?', (album_id,))
    imported_conn.commit()
    changes = []
    ctx = JobContext(legacy_db, '/music', Config(), report_change=lambda **kw: changes.append(kw))
    job().scan(ctx)
    assert [c['details']['tracks_added'] for c in changes] == [1]
    job().scan(ctx)  # still partial, nothing new: no hourly history entry
    assert len(changes) == 1


def test_background_job_includes_monitored_track_but_not_artist_entire_discography(
        imported_conn, legacy_db):
    wanted = release(imported_conn, 'Requested', monitored=True, origin='discography')
    browse = release(imported_conn, 'Browse', origin='discography')
    forgotten = release(imported_conn, 'Deleted', owned=True, file_state='deleted')
    imported_conn.commit()

    job().scan(JobContext(legacy_db, '/music', Config()))

    assert titles(imported_conn, wanted) == ['Requested A', 'Requested B']
    assert titles(imported_conn, browse) == ['Browse A']
    assert titles(imported_conn, forgotten) == ['Deleted A']


def test_batch_rotates_past_provider_failures_instead_of_starving_later_albums(
        imported_conn, legacy_db, monkeypatch):
    from core.library2 import provider_adapters

    monkeypatch.setattr(provider_adapters, 'fetch_album_tracklist', lambda *a, **kw: None)
    unresolved = release(imported_conn, 'Unresolved', owned=True, cached=False)
    later = release(imported_conn, 'Later', owned=True)
    imported_conn.commit()
    cfg = Config(batch_size=1)
    # Start immediately before our two rows, excluding the imported fixture's releases.
    cfg.set('repair.jobs.album_catalogue_backfill.checkpoint_id', unresolved - 1)
    ctx = JobContext(legacy_db, '/music', cfg)

    first = job().scan(ctx)
    assert first.scanned == 1
    assert titles(imported_conn, unresolved) == ['Unresolved A']
    assert titles(imported_conn, later) == ['Later A']
    second = job().scan(ctx)
    assert second.scanned == 1
    assert titles(imported_conn, later) == ['Later A', 'Later B']


def test_stop_before_start_leaves_catalogue_untouched(imported_conn, legacy_db):
    album_id = release(imported_conn, 'Stopped', owned=True)
    imported_conn.commit()
    ctx = JobContext(legacy_db, '/music', Config(), should_stop=lambda: True)

    result = job().scan(ctx)

    assert result.scanned == 0
    assert titles(imported_conn, album_id) == ['Stopped A']


def test_background_job_respects_existing_edition_pin(imported_conn, legacy_db):
    from core.library2.editions import pin_album_release

    album_id = release(imported_conn, 'Pinned', owned=True)
    pin_album_release(imported_conn.cursor(), album_id, 'spotify', 'sp-Pinned')
    imported_conn.commit()

    job().scan(JobContext(legacy_db, '/music', Config()))

    pin = imported_conn.execute(
        'SELECT canonical_source,canonical_album_id,canonical_locked FROM lib2_albums WHERE id=?',
        (album_id,),
    ).fetchone()
    assert tuple(pin) == ('spotify', 'sp-Pinned', 1)
    assert titles(imported_conn, album_id) == ['Pinned A', 'Pinned B']


def test_background_catalogue_uses_only_confirmed_pinned_source(
        imported_conn, legacy_db, monkeypatch):
    from core.library2.editions import pin_album_release
    from core.library2.provider_adapters import TracklistProviderResult, TracklistTrack

    album_id = release(imported_conn, 'Exact', owned=True, cached=False)
    imported_conn.execute('UPDATE lib2_albums SET external_ids=? WHERE id=?',
                          (json.dumps({'deezer': 'confirmed'}), album_id))
    pin_album_release(imported_conn.cursor(), album_id, 'deezer', 'confirmed')
    imported_conn.commit()
    calls = []
    def fetch_exact(ids, **kwargs):
        calls.append(ids)
        return (TracklistProviderResult('deezer', 'confirmed', (
            TracklistTrack('Exact A', 1, 1, 180000, 'deezer', 'a'),
            TracklistTrack('Exact B', 2, 1, 180000, 'deezer', 'b'),
        )),)
    monkeypatch.setattr('core.library2.provider_adapters.fetch_matched_album_tracklists', fetch_exact)

    job().scan(JobContext(legacy_db, '/music', Config()))

    assert calls == [{'deezer': 'confirmed'}]
    assert titles(imported_conn, album_id) == ['Exact A', 'Exact B']


def test_hand_tagged_release_is_not_rematched_by_background_job(imported_conn, legacy_db):
    from database.music_database import MusicDatabase

    album_id = release(imported_conn, 'Bootleg', owned=True)
    imported_conn.commit()
    db = SimpleNamespace(
        _get_connection=legacy_db._get_connection,
        manual_path_keys=lambda: {MusicDatabase.manual_path_key('/music/Bootleg.flac')},
    )

    job().scan(JobContext(db, '/music', Config()))

    assert titles(imported_conn, album_id) == ['Bootleg A']


def test_cold_catalogue_is_persisted_once_and_reused_by_album_page(
        imported_conn, legacy_db, monkeypatch):
    from core.library2.completeness import load_album_catalogue
    from core.library2.provider_adapters import TracklistProviderResult, TracklistTrack

    album_id = release(imported_conn, 'Cold', owned=True, cached=False)
    imported_conn.commit()
    requests = []

    def fetch(title, artist, **kw):
        if title != 'Cold':
            return None
        requests.append(kw['source_album_ids'])
        return TracklistProviderResult('spotify', 'sp-Cold', tuple(
            TracklistTrack(f'Cold {name}', number, 1, 180000, 'spotify', f'cold-{number}')
            for number, name in enumerate(['A', 'B', 'C'], 1)
        ))

    monkeypatch.setattr('core.library2.provider_adapters.fetch_album_tracklist', fetch)
    cfg = Config()

    job().scan(JobContext(legacy_db, '/music', cfg))

    assert titles(imported_conn, album_id) == ['Cold A', 'Cold B', 'Cold C']
    assert requests == [{'spotify': 'sp-Cold'}]
    assert imported_conn.execute(
        'SELECT expected_track_count FROM lib2_albums WHERE id=?', (album_id,)
    ).fetchone()[0] == 3
    # The same real loader the detail route calls reuses the persisted snapshot.
    load_album_catalogue(legacy_db, cfg, imported_conn, album_id)
    assert requests == [{'spotify': 'sp-Cold'}]


def test_cached_unknown_size_catalogue_stays_reusable_after_background_completion(
        imported_conn, legacy_db, monkeypatch):
    from core.library2.completeness import load_album_catalogue

    album_id = release(imported_conn, 'Cached', owned=True)
    imported_conn.commit()
    requests = []

    def fetch(title, artist, **kwargs):
        requests.append(title)
        return None

    monkeypatch.setattr('core.library2.provider_adapters.fetch_album_tracklist', fetch)
    cfg = Config()
    job().scan(JobContext(legacy_db, '/music', cfg))
    tracks = load_album_catalogue(legacy_db, cfg, imported_conn, album_id)

    assert tracks is not None
    assert [track['title'] for track in tracks] == ['Cached A', 'Cached B']
    assert 'Cached' not in requests


def test_concurrent_album_page_and_download_share_one_cold_catalogue(
        imported_conn, legacy_db, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from core.library2.completeness import load_album_catalogue
    from core.library2.provider_adapters import TracklistProviderResult, TracklistTrack

    album_id = release(imported_conn, 'Concurrent', owned=True, cached=False)
    imported_conn.commit()
    start = threading.Barrier(2)
    provider_call = threading.Barrier(2)
    requests = []

    def fetch(title, artist, **kwargs):
        requests.append(title)
        # Before the fix, both consumers enter the provider concurrently.
        # With a shared flight, the peer waits for the first persisted result.
        try:
            provider_call.wait(timeout=0.4)
        except threading.BrokenBarrierError:
            pass
        return TracklistProviderResult('spotify', 'sp-Concurrent', (
            TracklistTrack('Concurrent A', 1, 1, 180000, 'spotify', 'c-a'),
            TracklistTrack('Concurrent B', 2, 1, 180000, 'spotify', 'c-b'),
        ))

    monkeypatch.setattr('core.library2.provider_adapters.fetch_album_tracklist', fetch)

    def consumer(inherit):
        conn = legacy_db._get_connection()
        try:
            start.wait(timeout=3)
            return load_album_catalogue(legacy_db, Config(), conn, album_id,
                                        inherit_monitoring=inherit)
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(consumer, [False, True]))
    assert all(result and len(result) == 2 for result in results)
    assert requests == ['Concurrent']
    assert titles(imported_conn, album_id) == ['Concurrent A', 'Concurrent B']


def test_interest_in_own_library_is_seen_without_shared_monitoring(
        imported_conn, legacy_db, monkeypatch):
    monkeypatch.setattr('core.library_scope.own_library_ids', lambda: {7})
    album_id = release(imported_conn, 'Private', origin='discography')
    track_id = imported_conn.execute(
        'SELECT id FROM lib2_tracks WHERE album_id=?', (album_id,)
    ).fetchone()[0]
    record_rule(imported_conn, 'track', track_id, True, PROVENANCE_USER, profile_id=7)
    recompute_wanted_for_entity(imported_conn, 'album', album_id, profile_id=7)
    imported_conn.commit()

    job().scan(JobContext(legacy_db, '/music', Config()))

    assert titles(imported_conn, album_id) == ['Private A', 'Private B']
    states = imported_conn.execute(
        'SELECT profile_id,wanted FROM lib2_wanted_tracks WHERE track_id=? ORDER BY profile_id',
        (track_id,),
    ).fetchall()
    assert [tuple(row) for row in states] == [(1, 0), (7, 1)]


def test_explicit_album_monitoring_keeps_missing_siblings_wanted(
        imported_conn, legacy_db):
    album_id = release(imported_conn, 'Whole', owned=True)
    record_rule(imported_conn, 'album', album_id, True, PROVENANCE_USER)
    first = imported_conn.execute(
        'SELECT id FROM lib2_tracks WHERE album_id=?', (album_id,)
    ).fetchone()[0]
    record_rule(imported_conn, 'track', first, True, PROVENANCE_USER)
    recompute_wanted_for_entity(imported_conn, 'album', album_id)
    imported_conn.commit()

    job().scan(JobContext(legacy_db, '/music', Config()))

    missing = imported_conn.execute(
        "SELECT id FROM lib2_tracks WHERE album_id=? AND title='Whole B'", (album_id,)
    ).fetchone()[0]
    assert imported_conn.execute(
        'SELECT wanted FROM lib2_wanted_tracks WHERE track_id=? AND profile_id=1', (missing,)
    ).fetchone()[0] == 1


def test_checkpoint_write_failure_does_not_discard_successful_catalogue_update(
        imported_conn, legacy_db):
    class BrokenConfig(Config):
        def set(self, key, value):
            raise OSError('cannot save configuration')

    album_id = release(imported_conn, 'Saved', owned=True)
    imported_conn.commit()

    result = job().scan(JobContext(legacy_db, '/music', BrokenConfig()))

    assert titles(imported_conn, album_id) == ['Saved A', 'Saved B']
    assert result.auto_fixed >= 1
    assert result.errors == 1
