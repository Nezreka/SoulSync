"""Redownload builds full album_data from the primary source (#915).

iTunes/Deezer single-track redownloads used to carry a lean album_data ({'name': ...}),
dropping the $year folder. _album_data_from_source mirrors the Spotify branch so the real
release_date / album_type / total_tracks come through.
"""

from __future__ import annotations

from core.library.redownload import _album_data_from_source


def test_builds_full_album_data_from_source():
    full = {'id': 'it-1', 'name': 'Big OST', 'release_date': '2024-04-17',
            'album_type': 'album', 'total_tracks': 70, 'image_url': 'http://x/cover.jpg'}
    out = _album_data_from_source(full, 'it-1', 'fallback')
    assert out['release_date'] == '2024-04-17'   # real date, not YYYY-01-01
    assert out['album_type'] == 'album'
    assert out['total_tracks'] == 70
    assert out['id'] == 'it-1'
    assert out['name'] == 'Big OST'
    assert out['image_url'] == 'http://x/cover.jpg'


def test_image_url_falls_back_to_images_array():
    full = {'name': 'A', 'release_date': '2020-01-01', 'images': [{'url': 'http://x/img.jpg'}]}
    out = _album_data_from_source(full, 'a1', 'fb')
    assert out['image_url'] == 'http://x/img.jpg'


def test_defaults_when_fields_missing():
    out = _album_data_from_source({}, 'a1', 'Fallback Album')
    assert out['id'] == 'a1'              # falls back to the queried id
    assert out['name'] == 'Fallback Album'
    assert out['album_type'] == 'album'   # default
    assert out['total_tracks'] == 0
    assert out['release_date'] == ''
    assert out['image_url'] == ''
