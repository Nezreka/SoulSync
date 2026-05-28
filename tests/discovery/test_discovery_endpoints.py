"""Tests for the lifted, source-agnostic discovery route helpers in
``core.discovery.endpoints``.

These pin the exact behavior the per-source ``convert_<source>_results_to_spotify_tracks``
functions had in web_server.py, so the lift is provably 1:1. Each input shape
the originals handled is exercised here.
"""

from __future__ import annotations

from core.discovery.endpoints import convert_results_to_spotify_tracks


# ---------------------------------------------------------------------------
# spotify_data (manual-fix) shape
# ---------------------------------------------------------------------------

def test_spotify_data_shape_basic():
    results = [{
        'spotify_data': {
            'id': 'sp1', 'name': 'Song', 'artists': ['A'], 'album': 'Alb',
            'duration_ms': 1234,
        }
    }]
    assert convert_results_to_spotify_tracks(results, 'Tidal') == [{
        'id': 'sp1', 'name': 'Song', 'artists': ['A'], 'album': 'Alb',
        'duration_ms': 1234,
    }]


def test_spotify_data_duration_defaults_to_zero():
    results = [{'spotify_data': {'id': 'x', 'name': 'n', 'artists': [], 'album': 'a'}}]
    out = convert_results_to_spotify_tracks(results, 'Deezer')
    assert out[0]['duration_ms'] == 0


def test_spotify_data_includes_track_and_disc_number_when_present():
    results = [{'spotify_data': {
        'id': 'x', 'name': 'n', 'artists': [], 'album': 'a',
        'track_number': 5, 'disc_number': 2,
    }}]
    out = convert_results_to_spotify_tracks(results, 'Qobuz')
    assert out[0]['track_number'] == 5
    assert out[0]['disc_number'] == 2


def test_spotify_data_omits_track_disc_when_absent_or_falsy():
    # track_number/disc_number of 0 are falsy -> omitted, matching original.
    results = [{'spotify_data': {
        'id': 'x', 'name': 'n', 'artists': [], 'album': 'a',
        'track_number': 0, 'disc_number': 0,
    }}]
    out = convert_results_to_spotify_tracks(results, 'YouTube')
    assert 'track_number' not in out[0]
    assert 'disc_number' not in out[0]


# ---------------------------------------------------------------------------
# spotify_track + status_class == 'found' (auto-discovery) shape
# ---------------------------------------------------------------------------

def test_auto_discovery_shape_full():
    results = [{
        'spotify_track': 'Track', 'status_class': 'found',
        'spotify_id': 'id9', 'spotify_artist': 'Artist', 'spotify_album': 'Album',
    }]
    assert convert_results_to_spotify_tracks(results, 'ListenBrainz') == [{
        'id': 'id9', 'name': 'Track', 'artists': ['Artist'], 'album': 'Album',
        'duration_ms': 0,
    }]


def test_auto_discovery_defaults_when_fields_missing():
    results = [{'spotify_track': 'T', 'status_class': 'found'}]
    out = convert_results_to_spotify_tracks(results, 'Spotify Public')
    assert out == [{
        'id': 'unknown', 'name': 'T', 'artists': ['Unknown Artist'],
        'album': 'Unknown Album', 'duration_ms': 0,
    }]


def test_auto_discovery_empty_artist_yields_unknown_artist():
    results = [{
        'spotify_track': 'T', 'status_class': 'found', 'spotify_artist': '',
    }]
    out = convert_results_to_spotify_tracks(results, 'Tidal')
    assert out[0]['artists'] == ['Unknown Artist']


# ---------------------------------------------------------------------------
# skip / mixed / empty
# ---------------------------------------------------------------------------

def test_auto_discovery_requires_found_status():
    # spotify_track present but status_class != 'found' -> skipped.
    results = [{'spotify_track': 'T', 'status_class': 'not_found'}]
    assert convert_results_to_spotify_tracks(results, 'Tidal') == []


def test_result_matching_neither_shape_is_skipped():
    results = [{'irrelevant': True}, {'spotify_track': 'T'}]  # 2nd has no status_class
    assert convert_results_to_spotify_tracks(results, 'Tidal') == []


def test_mixed_results_preserve_order():
    results = [
        {'spotify_data': {'id': '1', 'name': 'a', 'artists': [], 'album': ''}},
        {'irrelevant': True},
        {'spotify_track': 'b', 'status_class': 'found', 'spotify_id': '2'},
    ]
    out = convert_results_to_spotify_tracks(results, 'Tidal')
    assert [t['id'] for t in out] == ['1', '2']


def test_empty_input():
    assert convert_results_to_spotify_tracks([], 'Tidal') == []


def test_spotify_data_takes_precedence_over_auto_fields():
    # A result carrying both shapes uses spotify_data (the if-branch wins),
    # matching the original if/elif ordering.
    results = [{
        'spotify_data': {'id': 'D', 'name': 'd', 'artists': [], 'album': ''},
        'spotify_track': 'IGNORED', 'status_class': 'found', 'spotify_id': 'A',
    }]
    out = convert_results_to_spotify_tracks(results, 'Tidal')
    assert out[0]['id'] == 'D'
