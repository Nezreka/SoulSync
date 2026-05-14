"""Pin the Deezer per-track cache validity check.

Issue #588: contributors tagging worked for some tracks and not others.
Root cause was cache pollution — `/album/<id>/tracks` cached partial
records under the same key as `/track/<id>`, and `get_track_details`
was using `track_position` alone as the "full payload" sentinel. Both
endpoints set track_position; only `/track/<id>` sets contributors.

These tests pin the corrected sentinel (`_is_full_track_payload`) so
the regression can't silently come back.
"""

from unittest.mock import MagicMock, patch

import pytest

from core.deezer_client import _is_full_track_payload


# ────────────────────────────────────────────────────────────────────
# Pure helper — payload-shape classification
# ────────────────────────────────────────────────────────────────────

def test_full_track_endpoint_payload_is_valid():
    payload = {
        'id': 12345,
        'title': 'Erased',
        'track_position': 1,
        'contributors': [{'name': 'Whipped Cream'}, {'name': 'Andrea Botez'}],
        'artist': {'name': 'Whipped Cream'},
        'album': {'id': 1, 'title': 'Erased'},
    }
    assert _is_full_track_payload(payload) is True


def test_full_track_with_empty_contributors_list_is_valid():
    # Single-artist track from /track/<id> still emits contributors=[]
    # The KEY presence is what matters, not truthiness.
    payload = {
        'id': 12345,
        'title': 'Solo Track',
        'track_position': 1,
        'contributors': [],
        'artist': {'name': 'Solo Artist'},
    }
    assert _is_full_track_payload(payload) is True


def test_album_tracks_payload_missing_contributors_is_partial():
    # The exact shape /album/<id>/tracks returns per item — has
    # track_position but no contributors. Pre-fix this passed the
    # `track_position in cached` check; post-fix it correctly falls
    # through to a fresh /track/<id> fetch.
    payload = {
        'id': 12345,
        'title': 'Sacrifice',
        'track_position': 1,
        'duration': 180,
        'artist': {'name': 'Andrea Botez'},
    }
    assert _is_full_track_payload(payload) is False


def test_search_payload_without_track_position_is_partial():
    payload = {
        'id': 12345,
        'title': 'Sacrifice',
        'artist': {'name': 'Andrea Botez'},
        'isrc': 'XX1234567890',
    }
    assert _is_full_track_payload(payload) is False


def test_none_or_non_dict_payload_is_partial():
    assert _is_full_track_payload(None) is False
    assert _is_full_track_payload([]) is False
    assert _is_full_track_payload('string') is False
    assert _is_full_track_payload(0) is False


def test_empty_dict_is_partial():
    assert _is_full_track_payload({}) is False


# ────────────────────────────────────────────────────────────────────
# get_track_details — cache + fetch interaction
# ────────────────────────────────────────────────────────────────────

@pytest.fixture
def deezer_client():
    """Build a DeezerClient with mocked HTTP + cache. Bypasses __init__
    auth/config requirements."""
    from core.deezer_client import DeezerClient
    client = DeezerClient.__new__(DeezerClient)
    client._api_get = MagicMock()
    return client


def _patch_cache(cached_payload):
    """Patch the module-level cache lookup. Returns the patched cache
    mock so callers can assert on store_entity calls."""
    cache = MagicMock()
    cache.get_entity.return_value = cached_payload
    cache.store_entity = MagicMock()
    return patch('core.deezer_client.get_metadata_cache', return_value=cache), cache


def test_cache_hit_with_full_payload_skips_api_call(deezer_client):
    full = {
        'id': 12345,
        'title': 'Erased',
        'track_position': 1,
        'contributors': [{'name': 'Whipped Cream'}, {'name': 'Andrea Botez'}],
        'artist': {'name': 'Whipped Cream'},
        'album': {'id': 1, 'title': 'Erased', 'nb_tracks': 1},
    }
    cache_patch, cache = _patch_cache(full)
    with cache_patch:
        result = deezer_client.get_track_details('12345')

    assert result is not None
    assert result['artists'] == ['Whipped Cream', 'Andrea Botez']
    deezer_client._api_get.assert_not_called()
    cache.store_entity.assert_not_called()


def test_cache_hit_with_partial_album_tracks_payload_refetches(deezer_client):
    """The bug from #588 — partial album-tracks data should NOT be
    treated as a full hit. Post-fix the client re-fetches."""
    partial = {
        'id': 12345,
        'title': 'Sacrifice',
        'track_position': 1,
        'artist': {'name': 'Andrea Botez'},
    }
    fresh = {
        'id': 12345,
        'title': 'Sacrifice',
        'track_position': 1,
        'contributors': [{'name': 'Andrea Botez'}, {'name': 'Grabbitz'}],
        'artist': {'name': 'Andrea Botez'},
        'album': {'id': 1, 'title': 'Sacrifice', 'nb_tracks': 1},
    }
    cache_patch, cache = _patch_cache(partial)
    deezer_client._api_get.return_value = fresh
    with cache_patch:
        result = deezer_client.get_track_details('12345')

    assert result is not None
    assert result['artists'] == ['Andrea Botez', 'Grabbitz']
    deezer_client._api_get.assert_called_once_with('track/12345')
    cache.store_entity.assert_called_once_with('deezer', 'track', '12345', fresh)


def test_cache_miss_fetches_fresh(deezer_client):
    cache_patch, cache = _patch_cache(None)
    fresh = {
        'id': 12345,
        'title': 'Sacrifice',
        'track_position': 1,
        'contributors': [{'name': 'Andrea Botez'}, {'name': 'Grabbitz'}],
        'artist': {'name': 'Andrea Botez'},
        'album': {'id': 1, 'title': 'Sacrifice', 'nb_tracks': 1},
    }
    deezer_client._api_get.return_value = fresh
    with cache_patch:
        result = deezer_client.get_track_details('12345')

    assert result is not None
    assert result['artists'] == ['Andrea Botez', 'Grabbitz']
    deezer_client._api_get.assert_called_once_with('track/12345')
    cache.store_entity.assert_called_once()


def test_cache_hit_with_search_shape_refetches(deezer_client):
    """Search results lack track_position — same fall-through path as
    partial album-tracks data."""
    search_shape = {
        'id': 12345,
        'title': 'Sacrifice',
        'artist': {'name': 'Andrea Botez'},
        'isrc': 'XX1234567890',
    }
    fresh = {
        'id': 12345,
        'title': 'Sacrifice',
        'track_position': 1,
        'contributors': [{'name': 'Andrea Botez'}, {'name': 'Grabbitz'}],
        'artist': {'name': 'Andrea Botez'},
        'album': {'id': 1, 'title': 'Sacrifice', 'nb_tracks': 1},
    }
    cache_patch, _ = _patch_cache(search_shape)
    deezer_client._api_get.return_value = fresh
    with cache_patch:
        result = deezer_client.get_track_details('12345')

    assert result is not None
    assert result['artists'] == ['Andrea Botez', 'Grabbitz']
    deezer_client._api_get.assert_called_once()


def test_api_failure_returns_none(deezer_client):
    cache_patch, _ = _patch_cache(None)
    deezer_client._api_get.return_value = None
    with cache_patch:
        result = deezer_client.get_track_details('12345')

    assert result is None
