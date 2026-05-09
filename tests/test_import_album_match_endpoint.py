"""Pin the ``/api/import/album/match`` endpoint's source-routing
behavior — github issue #524 regression guard.

The bug: clicking an album in the import page POSTed only ``album_id``,
dropping the ``source`` field that the backend needs to route the
lookup to the correct metadata client. The backend silently fell back
to its primary-source-priority chain, which fails for cross-source
album_ids (Deezer numeric id vs Spotify primary, etc.) → broken
fallback dict written to the library DB.

The frontend fix populates source on every match POST. These tests
pin the BACKEND defense: when source is dropped (curl, third-party,
regression in another caller), a clear warning lands in the logs so
the regression is grep-able instead of silent.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest


@pytest.fixture
def import_match_client(monkeypatch):
    """Flask test client, with the album-match payload builder mocked
    so we don't have to spin up real metadata clients."""
    with patch("web_server.add_activity_item"):
        with patch("web_server.SpotifyClient"):
            with patch("core.tidal_client.TidalClient"):
                from web_server import app as flask_app
                flask_app.config['TESTING'] = True
                yield flask_app.test_client()


def test_missing_source_logs_warning(import_match_client, caplog):
    """When the match POST omits source, backend logs a clear warning
    so the regression is visible in app.log even though the request
    still proceeds (best-effort lookup via primary-source priority).
    """
    fake_payload = {'success': True, 'album': {}, 'matches': [], 'unmatched_files': []}
    with caplog.at_level(logging.WARNING, logger='soulsync'):
        with patch(
            'web_server.build_album_import_match_payload',
            return_value=fake_payload,
        ):
            resp = import_match_client.post(
                '/api/import/album/match',
                json={'album_id': '1234567890'},  # no source
            )

    assert resp.status_code == 200
    # The defensive log must mention the missing source AND the album_id
    # so ops can grep app.log for the offending caller.
    assert any(
        "Missing 'source'" in r.message and '1234567890' in r.message
        for r in caplog.records
    ), (
        "Expected a warning naming the missing source + album_id. "
        "Got records: " + repr([r.message for r in caplog.records])
    )


def test_source_provided_does_not_warn(import_match_client, caplog):
    """When source IS provided (the common path), no warning fires.
    Catches regression where the warning becomes noisy from firing on
    every legit request."""
    fake_payload = {'success': True, 'album': {}, 'matches': [], 'unmatched_files': []}
    with caplog.at_level(logging.WARNING, logger='soulsync'):
        with patch(
            'web_server.build_album_import_match_payload',
            return_value=fake_payload,
        ):
            resp = import_match_client.post(
                '/api/import/album/match',
                json={
                    'album_id': '1234567890',
                    'source': 'deezer',
                    'album_name': 'Test Album',
                    'album_artist': 'Test Artist',
                },
            )

    assert resp.status_code == 200
    missing_source_warnings = [
        r for r in caplog.records if "Missing 'source'" in r.message
    ]
    assert not missing_source_warnings, (
        "When source is supplied, no missing-source warning should fire. "
        f"Got: {[r.message for r in missing_source_warnings]}"
    )


def test_source_passed_through_to_payload_builder(import_match_client):
    """Verify the endpoint actually forwards source to the underlying
    payload builder. Without this, we'd be logging the warning correctly
    but still doing the wrong lookup."""
    fake_payload = {'success': True, 'album': {}, 'matches': [], 'unmatched_files': []}
    with patch(
        'web_server.build_album_import_match_payload',
        return_value=fake_payload,
    ) as mock_builder:
        import_match_client.post(
            '/api/import/album/match',
            json={
                'album_id': 'abc123',
                'source': 'spotify',
                'album_name': 'X',
                'album_artist': 'Y',
            },
        )

    mock_builder.assert_called_once()
    call_kwargs = mock_builder.call_args.kwargs
    assert call_kwargs['source'] == 'spotify'
    assert call_kwargs['album_name'] == 'X'
    assert call_kwargs['album_artist'] == 'Y'
