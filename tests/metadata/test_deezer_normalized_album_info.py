"""Regression tests for the Deezer album-type/count corruption.

Verified seam (Sep 28, 2026): ``_build_album_info`` in
``core/metadata/album_tracks.py`` applies the typed ``Album.from_deezer_dict``
converter to already-normalized dicts (the shape the Deezer client's
``get_album()`` returns). The converter only read the RAW keys
(``record_type``/``nb_tracks``), so a normalized ``{'album_type': 'ep',
'total_tracks': 6}`` came out as ``{'album_type': 'album',
'total_tracks': 0}`` — the EP lost both its type signal and its count.

That corrupted dict flows into ``download_discography``
(``result['album']['album_type']``), the enriched album download, and the
album import matcher.
"""
from core.metadata.album_tracks import _build_album_info
from core.metadata.types import Album


def _normalized_deezer_ep():
    # Shape returned by the Deezer client's get_album() — already normalized,
    # NOT the raw /album/{id} API payload.
    return {
        'id': '81827',
        'name': 'Collision Course',
        'album_type': 'ep',
        'total_tracks': 6,
        'artists': [{'name': 'Jay-Z'}],
        'release_date': '2004-11-30',
    }


def test_build_album_info_preserves_normalized_deezer_ep():
    info = _build_album_info(
        _normalized_deezer_ep(), '81827',
        album_name='Collision Course', artist_name='Jay-Z', source='deezer',
    )
    assert info['album_type'] == 'ep'
    assert info['total_tracks'] == 6


def test_build_album_info_preserves_normalized_deezer_single():
    d = _normalized_deezer_ep()
    d['album_type'] = 'single'
    d['total_tracks'] = 2
    info = _build_album_info(
        d, '81827', album_name='Collision Course',
        artist_name='Jay-Z', source='deezer',
    )
    assert info['album_type'] == 'single'
    assert info['total_tracks'] == 2


def test_deezer_converter_still_handles_raw_api_shape():
    raw = {
        'id': 81827,
        'title': 'Collision Course',
        'record_type': 'ep',
        'nb_tracks': 6,
        'artist': {'id': 412, 'name': 'Linkin Park'},
        'release_date': '2004-11-30',
    }
    album = Album.from_deezer_dict(raw)
    assert album.album_type == 'ep'
    assert album.total_tracks == 6


def test_deezer_converter_raw_shape_bare_album_verified_by_count():
    # Raw shape with no usable type still infers from the count.
    raw = {
        'id': 999,
        'title': 'Some EP',
        'record_type': 'album',
        'nb_tracks': 5,
        'artist': {'id': 1, 'name': 'Someone'},
    }
    album = Album.from_deezer_dict(raw)
    assert album.album_type == 'ep'
    assert album.total_tracks == 5


def test_deezer_converter_normalized_shape_direct():
    album = Album.from_deezer_dict(_normalized_deezer_ep())
    assert album.album_type == 'ep'
    assert album.total_tracks == 6
