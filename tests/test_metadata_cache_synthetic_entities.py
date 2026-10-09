"""Synthetic cache entries were never actually cached (Boulder).

    "are we caching any of the track position data? like if i load a playlist
    of 1500 tracks... then restart server and do again. it will do the entire
    process again."

Worse than that: it re-did the work on EVERY load, restart or not.

Loading a Deezer playlist looks up each unique album to learn a track's real
position on it — Deezer's playlist payload omits ``track_position``, and
numbering by playlist index would ride onto the downloaded file's tag. That is
~1,000 rate-limited requests on a 1500-track playlist, and it is the bulk of
the multi-minute wait.

Those results were supposed to be cached. They never were. ``store_entity``
skips its junk check for "synthetic cache entries", but it recognised them by
entity_ID SUFFIX (``..._features`` / ``..._tracks``) while every real caller
identifies them by entity_TYPE:

    cache.store_entity('deezer', 'album_tracks', '12345', {...})
    cache.store_entity(src, 'album_tracks', 'album_tracks_12345', {...})
    cache.store_entity(src, 'artist_discography', 'explorer_disco_x', {...})

None of those ids end in ``_tracks``. So the exemption never fired, the junk
check saw a blank name (synthetic payloads have none), ``''`` is in
``_JUNK_NAMES``, and every row was dropped — at debug level, silently.
"""

from __future__ import annotations

import pytest

from core.metadata.cache import MetadataCache


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_PATH', str(tmp_path / 'cache.db'))
    from core.metadata import cache as cache_module
    from database.music_database import MusicDatabase
    monkeypatch.setattr(cache_module, '_cache_instance', None, raising=False)
    db = MusicDatabase(str(tmp_path / 'cache.db'))
    result = MetadataCache()
    monkeypatch.setattr(result, '_get_db', lambda: db)
    return result


# ── the synthetic types must persist ──────────────────────────────────────

def test_album_tracks_round_trip(cache):
    payload = {'data': [{'id': 1, 'track_position': 3}, {'id': 2, 'track_position': 4}]}
    cache.store_entity('deezer', 'album_tracks', '987654', payload)
    got = cache.get_entity('deezer', 'album_tracks', '987654')
    assert got and got.get('data'), "album track positions were dropped again"
    assert got['data'][0]['track_position'] == 3


def test_album_tracks_with_a_prefixed_key_round_trips(cache):
    """The other caller keys them 'album_tracks_<id>'."""
    cache.store_entity('plex', 'album_tracks', 'album_tracks_55', {'tracks': [{'id': 'x'}]})
    assert (cache.get_entity('plex', 'album_tracks', 'album_tracks_55') or {}).get('tracks')


def test_artist_discography_round_trips(cache):
    cache.store_entity('plex', 'artist_discography', 'explorer_disco_radiohead',
                       {'albums': [{'id': 'a1'}]})
    got = cache.get_entity('plex', 'artist_discography', 'explorer_disco_radiohead')
    assert got and got.get('albums')


def test_the_legacy_id_suffix_exemption_still_works(cache):
    """Kept for anything that did use the '..._features' convention."""
    cache.store_entity('spotify', 'audio', 'abc123_features', {'tempo': 120})
    assert cache.get_entity('spotify', 'audio', 'abc123_features')


# ── but real junk must still be refused ───────────────────────────────────

def test_a_placeholder_named_entity_is_still_rejected(cache):
    """The guard exists for a reason — widening the exemption must not let
    'Unknown Album' rows into the cache."""
    cache.store_entity('deezer', 'album', '111', {'title': 'Unknown Album'})
    assert cache.get_entity('deezer', 'album', '111') is None


def test_a_nameless_ordinary_entity_is_still_rejected(cache):
    cache.store_entity('deezer', 'album', '112', {'title': ''})
    assert cache.get_entity('deezer', 'album', '112') is None


def test_a_real_entity_still_caches(cache):
    cache.store_entity('deezer', 'album', '222', {'title': 'In Rainbows'})
    assert cache.get_entity('deezer', 'album', '222')


# ── the predicate itself ──────────────────────────────────────────────────

def test_synthetic_detection(cache):
    assert cache._is_synthetic_entity('album_tracks', '12345')
    assert cache._is_synthetic_entity('artist_discography', 'explorer_disco_x')
    assert cache._is_synthetic_entity('audio', 'abc_features')
    assert not cache._is_synthetic_entity('album', '12345')
    assert not cache._is_synthetic_entity('track', '12345')
    # 'track' must not be swept in by a naive '_tracks' substring test
    assert not cache._is_synthetic_entity('track', 'sometrack')


def test_full_retag_refreshes_the_same_provider_client_despite_cached_metadata(cache, monkeypatch):
    from core.deezer_client import DeezerClient
    from core.library2.native_enrich import refresh_native_metadata
    db = cache._get_db()
    monkeypatch.setattr('core.deezer_client.get_metadata_cache', lambda: cache)
    client = DeezerClient()
    old = {'id': 999, 'title': 'Old provider title', 'artist': {'name': 'Artist'},
           'album': {'id': 888, 'title': 'Album'}, 'isrc': 'USABC2400001', 'bpm': 100,
           'track_position': 1, 'contributors': []}
    cache.store_entity('deezer', 'track', '999', old)
    assert cache.get_entity('deezer', 'track', '999')['title'] == 'Old provider title'
    calls = []
    def fetch(path):
        calls.append(path)
        return {**old, 'title': 'Fresh provider title', 'bpm': 125}
    monkeypatch.setattr(client, '_api_get', fetch)
    monkeypatch.setattr('core.metadata.registry.get_client_for_source', lambda *a, **kw: client)
    monkeypatch.setattr('core.library2.match_status.configured_services', lambda: {'deezer'})
    with db._get_connection() as conn:
        aid = conn.execute("INSERT INTO lib2_artists(name) VALUES('Artist')").lastrowid
        album_id = conn.execute("INSERT INTO lib2_albums(primary_artist_id,title) VALUES(?,'Album')", (aid,)).lastrowid
        tid = conn.execute("INSERT INTO lib2_tracks(album_id,title,external_ids) VALUES(?,'Old provider title',?)", (album_id, '{"deezer":"999"}')).lastrowid
        conn.execute("INSERT INTO lib2_track_files(track_id,path) VALUES(?,'/missing/test.flac')", (tid,))
        conn.commit()
    result = refresh_native_metadata(db, [tid])
    assert result['refreshed'] == 1 and not result['errors']
    assert calls == ['track/999']
    with db._get_connection() as conn:
        assert conn.execute('SELECT title,bpm FROM lib2_tracks WHERE id=?', (tid,)).fetchone()[:] == ('Fresh provider title', 125)
    assert cache.get_entity('deezer', 'track', '999')['title'] == 'Fresh provider title'


def test_provider_refresh_cache_bypass_is_scoped_and_keeps_offline_data(cache):
    from concurrent.futures import ThreadPoolExecutor
    from core.metadata.cache import refresh_cached_entity
    cache.store_entity('deezer', 'album', '222', {'title': 'Cached album'})
    cache.store_entity('deezer', 'album', '333', {'title': 'Other album'})
    with pytest.raises(RuntimeError), refresh_cached_entity('deezer', 'album', '222'):
        assert cache.get_entity('deezer', 'album', '222') is None
        assert cache.get_entity('deezer', 'album', '333')['title'] == 'Other album'
        found, missing = cache.get_entities_batch('deezer', 'album', ['222', '333'])
        assert set(found) == {'333'} and missing == ['222']
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(cache.get_entity, 'deezer', 'album', '222').result()['title'] == 'Cached album'
        raise RuntimeError('provider unavailable')
    assert cache.get_entity('deezer', 'album', '222')['title'] == 'Cached album'
