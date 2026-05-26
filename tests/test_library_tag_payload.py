from __future__ import annotations

from web_server import _build_library_tag_db_data


def test_library_tag_payload_preserves_track_artist_and_artists_list():
    payload = _build_library_tag_db_data(
        {
            'title': 'Duet',
            'artist_name': 'Album Artist',
            'track_artist': 'Artist A; Artist B',
            'album_title': 'Compilation',
            'year': 2026,
            'track_number': 3,
            'disc_number': 1,
            'bpm': 124,
            'track_count': 12,
            'album_thumb_url': 'https://example.test/cover.jpg',
            'artist_thumb_url': 'https://example.test/artist.jpg',
        },
        ['Electronic', 'Dance'],
    )

    assert payload['artist_name'] == 'Album Artist'
    assert payload['track_artist'] == 'Artist A; Artist B'
    assert payload['artists_list'] == ['Artist A', 'Artist B']
    assert payload['genres'] == ['Electronic', 'Dance']
    assert payload['thumb_url'] == 'https://example.test/cover.jpg'


def test_library_tag_payload_falls_back_without_track_artist():
    payload = _build_library_tag_db_data(
        {
            'title': 'Solo',
            'artist_name': 'Solo Artist',
            'track_artist': None,
            'album_title': 'Solo Album',
            'artist_thumb_url': 'https://example.test/artist.jpg',
        }
    )

    assert payload['track_artist'] is None
    assert 'artists_list' not in payload
    assert payload['artist_name'] == 'Solo Artist'
    assert payload['thumb_url'] == 'https://example.test/artist.jpg'
