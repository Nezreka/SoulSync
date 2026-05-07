"""Tests for the Discogs collection source on Your Albums.

Discord request (Jhones + BoulderBadgeDad): pull user's Discogs
collection into the Your Albums section on Discover, similar to how
Spotify Liked Albums works. Implementation adds Discogs as a fourth
source to the existing 3-source pipeline (Spotify / Tidal / Deezer)
with click-context dispatch so Discogs albums open with Discogs
release detail (vinyl/CD format info, year, label, tracklist).

Tests pin:
- DiscogsClient.get_user_collection — pagination, response
  normalization, disambiguation suffix stripping, missing-token
  defensive return.
- DiscogsClient.get_release — passthrough to /releases/{id}.
- liked_albums_pool — discogs_release_id column round-trips through
  the upsert + get path.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from core.discogs_client import DiscogsClient


# ---------------------------------------------------------------------------
# DiscogsClient.get_user_collection
# ---------------------------------------------------------------------------


@pytest.fixture
def authed_client():
    """A DiscogsClient with a fake token so is_authenticated() returns True
    without hitting the real API."""
    return DiscogsClient(token='dummy_test_token')


def test_get_user_collection_returns_empty_without_token(monkeypatch):
    """Defensive: no token → empty list, never raises. Discogs collection
    is private so an unauthenticated call would 403 anyway.

    DiscogsClient's constructor falls back to ``config_manager.get(
    'discogs.token')`` when no token is passed — including when the
    empty-string sentinel is passed (because empty-string is falsy).
    Stub the config lookup so this test stays deterministic regardless
    of the developer's local config (which may have a real token set
    after using the Your Albums Discogs source feature)."""
    from config.settings import config_manager
    monkeypatch.setattr(config_manager, 'get',
                        lambda key, default=None: '' if key == 'discogs.token' else default)
    client = DiscogsClient(token='')
    assert client.get_user_collection() == []


def test_get_user_collection_normalizes_response_shape(authed_client):
    """Each release becomes the dict shape upsert_liked_album expects."""
    fake_response = {
        'pagination': {'pages': 1, 'page': 1},
        'releases': [
            {'id': 12345, 'basic_information': {
                'title': 'GNX',
                'artists': [{'name': 'Kendrick Lamar'}],
                'cover_image': 'https://img.discogs.com/x.jpg',
                'year': 2024,
            }},
        ],
    }

    def _fake_get(endpoint, params=None):
        if endpoint == '/oauth/identity':
            return {'username': 'testuser'}
        return fake_response

    with patch.object(authed_client, '_api_get', side_effect=_fake_get):
        result = authed_client.get_user_collection()

    assert len(result) == 1
    r = result[0]
    assert r['album_name'] == 'GNX'
    assert r['artist_name'] == 'Kendrick Lamar'
    assert r['release_id'] == 12345
    assert r['image_url'] == 'https://img.discogs.com/x.jpg'
    assert r['release_date'] == '2024'


def test_get_user_collection_strips_discogs_disambiguation_suffix(authed_client):
    """Discogs appends '(N)' to artist names when there are multiple
    artists with the same name (e.g. 'Madonna (3)'). Strip it so the
    name matches what Spotify/Tidal/Deezer use."""
    fake_response = {
        'pagination': {'pages': 1, 'page': 1},
        'releases': [
            {'id': 1, 'basic_information': {
                'title': 'X', 'artists': [{'name': 'Madonna (3)'}],
                'cover_image': '', 'year': 2020,
            }},
        ],
    }
    with patch.object(authed_client, '_api_get',
                      side_effect=lambda e, p=None: ({'username': 'u'} if e == '/oauth/identity' else fake_response)):
        result = authed_client.get_user_collection()

    assert result[0]['artist_name'] == 'Madonna'


def test_get_user_collection_handles_missing_year(authed_client):
    """Year 0 / missing → empty release_date string (NOT '0')."""
    fake_response = {
        'pagination': {'pages': 1, 'page': 1},
        'releases': [
            {'id': 1, 'basic_information': {
                'title': 'Album',
                'artists': [{'name': 'Artist'}],
                'cover_image': '',
                'year': 0,
            }},
        ],
    }
    with patch.object(authed_client, '_api_get',
                      side_effect=lambda e, p=None: ({'username': 'u'} if e == '/oauth/identity' else fake_response)):
        result = authed_client.get_user_collection()

    assert result[0]['release_date'] == ''


def test_get_user_collection_skips_releases_with_missing_required_fields(authed_client):
    """Defensive: releases without title or artist are skipped, not crashed on."""
    fake_response = {
        'pagination': {'pages': 1, 'page': 1},
        'releases': [
            {'id': 1, 'basic_information': {'title': 'Has Both', 'artists': [{'name': 'Artist'}]}},
            {'id': 2, 'basic_information': {'title': '', 'artists': [{'name': 'No Title'}]}},
            {'id': 3, 'basic_information': {'title': 'No Artists', 'artists': []}},
        ],
    }
    with patch.object(authed_client, '_api_get',
                      side_effect=lambda e, p=None: ({'username': 'u'} if e == '/oauth/identity' else fake_response)):
        result = authed_client.get_user_collection()

    assert len(result) == 1
    assert result[0]['album_name'] == 'Has Both'


def test_get_user_collection_paginates(authed_client):
    """Walks all pages until pagination.pages is reached."""
    page_responses = {
        1: {'pagination': {'pages': 2, 'page': 1},
            'releases': [{'id': 1, 'basic_information': {'title': 'A', 'artists': [{'name': 'X'}]}}]},
        2: {'pagination': {'pages': 2, 'page': 2},
            'releases': [{'id': 2, 'basic_information': {'title': 'B', 'artists': [{'name': 'Y'}]}}]},
    }
    call_count = {'n': 0}

    def _fake_get(endpoint, params=None):
        if endpoint == '/oauth/identity':
            return {'username': 'u'}
        page = (params or {}).get('page', 1)
        call_count['n'] += 1
        return page_responses.get(page)

    with patch.object(authed_client, '_api_get', side_effect=_fake_get):
        result = authed_client.get_user_collection()

    assert len(result) == 2
    assert {r['release_id'] for r in result} == {1, 2}


def test_get_user_collection_caps_at_max_pages(authed_client):
    """Guard against runaway pagination — stops after max_pages even if
    the API claims more pages exist."""
    fake_response = {
        'pagination': {'pages': 9999, 'page': 1},
        'releases': [{'id': 1, 'basic_information': {'title': 'A', 'artists': [{'name': 'X'}]}}],
    }
    with patch.object(authed_client, '_api_get',
                      side_effect=lambda e, p=None: ({'username': 'u'} if e == '/oauth/identity' else fake_response)):
        # max_pages=2 — should request exactly 2 pages and stop
        result = authed_client.get_user_collection(max_pages=2)

    # Each page returned 1 release — capped at 2 pages = 2 releases
    assert len(result) == 2


def test_get_user_collection_uses_explicit_username(authed_client):
    """When username is passed explicitly, skip the /oauth/identity
    lookup. Useful for callers that already know the username."""
    captured_endpoints = []

    def _fake_get(endpoint, params=None):
        captured_endpoints.append(endpoint)
        return {'pagination': {'pages': 1, 'page': 1}, 'releases': []}

    with patch.object(authed_client, '_api_get', side_effect=_fake_get):
        authed_client.get_user_collection(username='explicituser')

    # /oauth/identity should NOT have been called
    assert '/oauth/identity' not in captured_endpoints
    # Collection endpoint includes the explicit username
    assert any('explicituser' in e for e in captured_endpoints)


# ---------------------------------------------------------------------------
# DiscogsClient.get_release
# ---------------------------------------------------------------------------


def test_get_release_passes_id_through_to_api(authed_client):
    """Thin wrapper — confirm endpoint shape."""
    captured = []
    with patch.object(authed_client, '_api_get',
                      side_effect=lambda e, p=None: captured.append(e) or {'id': 999}):
        result = authed_client.get_release(999)
    assert captured == ['/releases/999']
    assert result == {'id': 999}


def test_get_release_returns_none_for_invalid_id(authed_client):
    """Defensive: non-numeric / falsy id → None, no API call."""
    with patch.object(authed_client, '_api_get') as mock_api:
        assert authed_client.get_release(None) is None
        assert authed_client.get_release('not_a_number') is None
        assert authed_client.get_release(0) is None
    mock_api.assert_not_called()


# ---------------------------------------------------------------------------
# liked_albums_pool — discogs_release_id column
# ---------------------------------------------------------------------------


def test_liked_albums_discogs_release_id_roundtrip():
    """upsert with source_id_type='discogs' stores in discogs_release_id;
    get_liked_albums returns it on the row."""
    from database.music_database import get_database
    db = get_database()

    # Use a high profile_id to avoid colliding with real data
    test_profile = 9991
    try:
        ok = db.upsert_liked_album(
            album_name='Test Disc Album', artist_name='Test Disc Artist',
            source_service='discogs',
            source_id='987654', source_id_type='discogs',
            image_url=None, release_date='2023', total_tracks=10,
            profile_id=test_profile,
        )
        assert ok is True

        result = db.get_liked_albums(profile_id=test_profile, page=1, per_page=10)
        assert result['total'] == 1
        row = result['albums'][0]
        assert row['discogs_release_id'] == '987654'
        assert row['album_name'] == 'Test Disc Album'
        assert 'discogs' in row['source_services']
    finally:
        # Clean up
        conn = db._get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM liked_albums_pool WHERE profile_id = ?", (test_profile,))
        conn.commit()
        conn.close()


def test_liked_albums_multi_source_carries_both_ids():
    """If an album is added from Spotify AND from Discogs, both
    spotify_album_id and discogs_release_id end up on the same row
    via the dedup-by-normalized-key upsert."""
    from database.music_database import get_database
    db = get_database()

    test_profile = 9992
    try:
        # Add via Spotify first
        db.upsert_liked_album(
            album_name='Same Album', artist_name='Same Artist',
            source_service='spotify',
            source_id='spotify_id_xyz', source_id_type='spotify',
            image_url=None, release_date='', total_tracks=0,
            profile_id=test_profile,
        )
        # Then add the same album via Discogs — should dedupe
        db.upsert_liked_album(
            album_name='Same Album', artist_name='Same Artist',
            source_service='discogs',
            source_id='discogs_id_999', source_id_type='discogs',
            image_url=None, release_date='', total_tracks=0,
            profile_id=test_profile,
        )

        result = db.get_liked_albums(profile_id=test_profile, page=1, per_page=10)
        assert result['total'] == 1  # deduped to one row
        row = result['albums'][0]
        assert row['spotify_album_id'] == 'spotify_id_xyz'
        assert row['discogs_release_id'] == 'discogs_id_999'
        assert set(row['source_services']) == {'spotify', 'discogs'}
    finally:
        conn = db._get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM liked_albums_pool WHERE profile_id = ?", (test_profile,))
        conn.commit()
        conn.close()
