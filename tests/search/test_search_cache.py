"""Tests for core/search/cache.py — TTL+LRU cache for enhanced-search responses."""

from __future__ import annotations

import time

import pytest

from core.search import cache as search_cache


@pytest.fixture
def fresh_cache():
    """Each test gets a clean instance — module-level _cache is shared otherwise."""
    return search_cache.EnhancedSearchCache(ttl=10, max_entries=3)


def test_set_and_get_round_trip(fresh_cache):
    key = ('q', 'plex', 'spotify', False, 'auto')
    fresh_cache.set(key, {'result': 'data'})
    assert fresh_cache.get(key) == {'result': 'data'}


def test_get_returns_none_for_missing_key(fresh_cache):
    assert fresh_cache.get(('absent', 'plex', 'spotify', False, 'auto')) is None


def test_ttl_expiration_evicts_entry():
    c = search_cache.EnhancedSearchCache(ttl=0.05, max_entries=10)
    key = ('q', 'plex', 'spotify', False, 'auto')
    c.set(key, {'x': 1})
    assert c.get(key) == {'x': 1}
    time.sleep(0.1)
    assert c.get(key) is None


def test_max_entries_evicts_lru(fresh_cache):
    # max_entries = 3
    for i in range(3):
        fresh_cache.set((f"q{i}", 'plex', 'spotify', False, 'auto'), {'i': i})

    fresh_cache.set(('q3', 'plex', 'spotify', False, 'auto'), {'i': 3})
    # q0 should be evicted (oldest)
    assert fresh_cache.get(('q0', 'plex', 'spotify', False, 'auto')) is None
    assert fresh_cache.get(('q3', 'plex', 'spotify', False, 'auto')) == {'i': 3}


def test_get_promotes_lru(fresh_cache):
    fresh_cache.set(('q0', 'plex', 'spotify', False, 'auto'), {'i': 0})
    fresh_cache.set(('q1', 'plex', 'spotify', False, 'auto'), {'i': 1})
    fresh_cache.set(('q2', 'plex', 'spotify', False, 'auto'), {'i': 2})
    fresh_cache.get(('q0', 'plex', 'spotify', False, 'auto'))  # touch q0
    fresh_cache.set(('q3', 'plex', 'spotify', False, 'auto'), {'i': 3})
    # q1 should be evicted, not q0 (which was just touched)
    assert fresh_cache.get(('q1', 'plex', 'spotify', False, 'auto')) is None
    assert fresh_cache.get(('q0', 'plex', 'spotify', False, 'auto')) == {'i': 0}


def test_clear_empties_cache(fresh_cache):
    fresh_cache.set(('q', 'plex', 'spotify', False, 'auto'), {})
    fresh_cache.clear()
    assert fresh_cache.get(('q', 'plex', 'spotify', False, 'auto')) is None


# ---------------------------------------------------------------------------
# Key builder
# ---------------------------------------------------------------------------

def _providers(server='plex', source='spotify', hb=False):
    return {
        'active_server_provider': lambda: server,
        'fallback_source_provider': lambda: source,
        'hydrabase_active_provider': lambda: hb,
    }


def test_key_normalizes_query():
    key = search_cache.get_cache_key("  Pink FLOYD  ", None, **_providers())
    assert key[0] == "pink floyd"


def test_key_includes_active_server_and_fallback():
    key = search_cache.get_cache_key('q', None, **_providers(server='jellyfin', source='deezer'))
    assert key[1] == 'jellyfin'
    assert key[2] == 'deezer'


def test_key_includes_hydrabase_flag():
    k1 = search_cache.get_cache_key('q', None, **_providers(hb=False))
    k2 = search_cache.get_cache_key('q', None, **_providers(hb=True))
    assert k1 != k2


def test_key_includes_source_tag():
    k_auto = search_cache.get_cache_key('q', None, **_providers())
    k_explicit = search_cache.get_cache_key('q', 'spotify', **_providers())
    assert k_auto != k_explicit
    assert k_auto[4] == 'auto'
    assert k_explicit[4] == 'spotify'


def test_key_provider_failure_falls_back_to_unknown():
    def boom():
        raise RuntimeError("config dead")

    key = search_cache.get_cache_key('q', None,
                                     active_server_provider=boom,
                                     fallback_source_provider=lambda: 'spotify',
                                     hydrabase_active_provider=lambda: False)
    assert key[1] == 'unknown'


def test_key_hydrabase_provider_failure_falls_back_to_false():
    def boom():
        raise RuntimeError("hydrabase init failed")

    key = search_cache.get_cache_key('q', None,
                                     active_server_provider=lambda: 'plex',
                                     fallback_source_provider=lambda: 'spotify',
                                     hydrabase_active_provider=boom)
    assert key[3] is False


def test_key_preserves_falsy_provider_returns():
    """Original behavior: if provider returns None / '' on success, store it
    as-is. Don't coerce to 'unknown' — that's reserved for exceptions."""
    key = search_cache.get_cache_key('q', None,
                                     active_server_provider=lambda: None,
                                     fallback_source_provider=lambda: '',
                                     hydrabase_active_provider=lambda: 0)
    assert key[1] is None
    assert key[2] == ''
    assert key[3] == 0
