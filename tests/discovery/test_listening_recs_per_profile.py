"""listening recs are per profile.

the scan wrote recs + the listening mix to bare metadata keys, and the metadata
table is plain kv, so the last profile scanned overwrote everyone else and every
profile was served that one profile's picks.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from core.discovery.listening_recommendations import (
    RECS_ARTISTS_KEY, RECS_TRACKS_KEY, read_recs_raw, recs_key)


def test_keys_are_per_profile():
    assert recs_key(RECS_ARTISTS_KEY, 2) == 'listening_recs_artists:2'
    assert recs_key(RECS_ARTISTS_KEY, 1) != recs_key(RECS_ARTISTS_KEY, 2)
    assert recs_key(RECS_TRACKS_KEY, None) == 'listening_recs_tracks_full:1'


def test_only_profile_one_falls_back_to_the_old_bare_key():
    meta = {'listening_recs_artists': 'legacy'}
    assert read_recs_raw(meta.get, RECS_ARTISTS_KEY, 1) == 'legacy'
    assert read_recs_raw(meta.get, RECS_ARTISTS_KEY, 2) is None
    meta['listening_recs_artists:1'] = 'mine'
    assert read_recs_raw(meta.get, RECS_ARTISTS_KEY, 1) == 'mine'


@pytest.fixture()
def db(tmp_path):
    from database.music_database import MusicDatabase
    return MusicDatabase(str(tmp_path / 'm.db'))


@pytest.fixture()
def as_profile(db, monkeypatch):
    import web_server
    import api.discover_routes as routes

    current = {'pid': 1}
    monkeypatch.setattr('database.music_database.get_database', lambda *a, **k: db)
    monkeypatch.setattr(routes, 'get_database', lambda *a, **k: db)
    monkeypatch.setattr('core.profile_context.get_current_profile_id', lambda: current['pid'])
    monkeypatch.setattr(routes, 'get_current_profile_id', lambda: current['pid'])
    monkeypatch.setattr(routes, '_get_active_discovery_source', lambda: 'deezer')
    routes.invalidate_discover_shelf_cache()
    web_server.app.config['TESTING'] = True
    client = web_server.app.test_client()

    def get(pid, url):
        current['pid'] = pid
        return client.get(url).get_json()

    yield get
    routes.invalidate_discover_shelf_cache()


def test_each_profile_is_served_its_own_recs(db, as_profile):
    db.set_metadata('listening_recs_artists:1', json.dumps([
        {'name': 'Soen', 'seeds': ['Tool'], 'seed_count': 1, 'score': 1.0}]))
    db.set_metadata('listening_recs_artists:2', json.dumps([
        {'name': 'Kavinsky', 'seeds': ['Justice'], 'seed_count': 1, 'score': 1.0}]))
    url = '/api/discover/listening-recommendations'
    assert [a['artist_name'] for a in as_profile(2, url)['artists']] == ['Kavinsky']
    assert [a['artist_name'] for a in as_profile(1, url)['artists']] == ['Soen']


def test_each_profile_is_served_its_own_listening_mix(db, as_profile):
    db.set_metadata('listening_recs_tracks_full:1', json.dumps([{'track_name': 'Lucidity'}]))
    db.set_metadata('listening_recs_tracks_full:2', json.dumps([{'track_name': 'Nightcall'}]))
    url = '/api/discover/personalized/listening-mix'
    assert [t['track_name'] for t in as_profile(2, url)['tracks']] == ['Nightcall']
    assert [t['track_name'] for t in as_profile(1, url)['tracks']] == ['Lucidity']


def test_a_new_profile_never_sees_the_old_shared_recs(db, as_profile):
    db.set_metadata('listening_recs_artists', json.dumps([
        {'name': 'Soen', 'seeds': ['Tool'], 'seed_count': 1, 'score': 1.0}]))
    url = '/api/discover/listening-recommendations'
    assert as_profile(2, url)['artists'] == []
    assert [a['artist_name'] for a in as_profile(1, url)['artists']] == ['Soen']


def test_the_mix_generator_reads_the_profile_it_builds_for():
    from core.personalized.generators.listening_mix import generate
    from core.personalized.types import PlaylistConfig

    meta = {'listening_recs_tracks_full:2': json.dumps([{'track_name': 'Nightcall'}]),
            'listening_recs_tracks_full:1': json.dumps([{'track_name': 'Lucidity'}])}
    deps = SimpleNamespace(database=SimpleNamespace(get_metadata=meta.get),
                           get_current_profile_id=lambda: 2)
    assert [t.track_name for t in generate(deps, '', PlaylistConfig(limit=50))] == ['Nightcall']


def test_a_scan_for_one_profile_leaves_the_other_alone(db, monkeypatch):
    import core.watchlist_scanner as scanner_mod
    from core.watchlist_scanner import WatchlistScanner

    monkeypatch.setattr(scanner_mod, 'get_client_for_source', lambda *a, **k: None)  # no network
    other = json.dumps([{'name': 'Kavinsky', 'seeds': ['Justice'], 'seed_count': 1, 'score': 1.0}])
    db.set_metadata('listening_recs_artists:2', other)

    from tests.lib2_seed import artist as _artist, track as _track
    conn = db._get_connection()
    cur = conn.cursor()
    for aid, name, sp in ((1, 'Daft Punk', 'sp1'), (2, 'Justice', 'sp2')):
        _artist(conn, name, spotify_id=sp)
        for t in range(8):
            _track(conn, name, 'Al', f'{name} {t}', path=f'/m/{aid}-{t}.flac')
        for i in range(20):
            cur.execute("INSERT INTO listening_history (title, artist, played_at) "
                        "VALUES (?,?,datetime('now', ?))", (f'{name} {i % 8}', name, f'-{i} hours'))
        cur.execute("INSERT INTO similar_artists (source_artist_id, similar_artist_name, "
                    "similarity_rank, profile_id) VALUES (?, 'SebastiAn', 2, 1)", (sp,))
    conn.commit()
    conn.close()

    scanner = WatchlistScanner.__new__(WatchlistScanner)
    scanner._database = db
    scanner._build_listening_recommendations(1, [])

    assert 'SebastiAn' in [r['name'] for r in json.loads(db.get_metadata('listening_recs_artists:1'))]
    assert db.get_metadata('listening_recs_artists:2') == other
    assert db.get_metadata('listening_recs_artists') is None
