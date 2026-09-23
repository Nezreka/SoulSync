"""a hand-tagged download is tagged exactly as typed.

runs the real enhance_file_metadata and the real art embed against a real
FLAC. only the outside world is stubbed: config, source-metadata extraction,
verification. the source-id lookup and the preferred-art lookup are spies,
because the point is that a manual file never reaches them.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("mutagen")
from mutagen.flac import FLAC  # noqa: E402

import core.metadata.artwork as artwork  # noqa: E402
import core.metadata.enrichment as enrichment  # noqa: E402
from tests.test_enrichment_art_preservation import _PNG, _Cfg, _make_flac_with_art  # noqa: E402

USER_COVER = _PNG + b'user-cover'


@pytest.fixture
def flac_path(tmp_path):
    path = str(tmp_path / 'lucky.flac')
    _make_flac_with_art(path)
    return path


@pytest.fixture
def cover_file(tmp_path):
    path = tmp_path / 'cover.png'
    path.write_bytes(USER_COVER)
    return str(path)


def _run(flac_path, context):
    source_ids = MagicMock()
    preferred = MagicMock(return_value='http://studio-cover')
    metadata = {'title': 'Lucky', 'artist': 'Radiohead', 'album': 'Live at Glastonbury 2003',
                'album_artist': 'Radiohead', 'date': '2003-06-28', 'track_number': 4,
                'album_art_url': ''}
    with patch.object(enrichment, 'get_config_manager', return_value=_Cfg()), \
            patch.object(artwork, 'get_config_manager', return_value=_Cfg()), \
            patch.object(enrichment, 'strip_all_non_audio_tags'), \
            patch.object(enrichment, 'extract_source_metadata', return_value=dict(metadata)), \
            patch.object(enrichment, 'embed_source_ids', source_ids), \
            patch.object(enrichment, 'verify_metadata_written', return_value=True), \
            patch('core.metadata.art_lookup.select_preferred_art_url', preferred), \
            patch.object(artwork, '_fetch_art_bytes', return_value=(b'studio', 'image/jpeg')):
        enrichment.enhance_file_metadata(flac_path, context=context, artist={'name': 'Radiohead'}, album_info={})
    return source_ids, preferred


def test_a_manual_file_gets_the_users_cover_and_no_lookups(flac_path, cover_file):
    context = {'track_info': {'_manual_metadata': True, '_manual_cover_path': cover_file}}
    source_ids, preferred = _run(flac_path, context)

    source_ids.assert_not_called()      # no studio ids, no musicbrainz date
    preferred.assert_not_called()       # no studio cover by artist + album name
    audio = FLAC(flac_path)
    assert audio.pictures and audio.pictures[0].data == USER_COVER
    assert audio.get('soulsync_manual') == ['1']
    assert audio.get('date') == ['2003-06-28']


def test_an_ordinary_file_still_gets_the_lookups(flac_path):
    source_ids, _preferred = _run(flac_path, {'track_info': {'name': 'Lucky'}})
    source_ids.assert_called_once()
    assert FLAC(flac_path).get('soulsync_manual') is None


def test_cover_jpg_for_a_manual_album_is_the_users_image(tmp_path, cover_file):
    preferred = MagicMock(return_value='http://studio-cover')
    context = {'track_info': {'_manual_metadata': True, '_manual_cover_path': cover_file}}
    with patch.object(artwork, 'get_config_manager', return_value=_Cfg()), \
            patch('core.metadata.art_lookup.select_preferred_art_url', preferred), \
            patch.object(artwork, '_fetch_art_bytes', return_value=(b'studio', 'image/jpeg')):
        artwork.download_cover_art({'album_name': 'Live'}, str(tmp_path), context)
    preferred.assert_not_called()
    assert (tmp_path / 'cover.jpg').read_bytes() == USER_COVER


def test_a_manual_album_with_no_cover_gets_none_rather_than_a_guess(tmp_path):
    context = {'track_info': {'_manual_metadata': True}}
    with patch.object(artwork, 'get_config_manager', return_value=_Cfg()), \
            patch.object(artwork, '_fetch_art_bytes', return_value=(b'studio', 'image/jpeg')) as fetch:
        artwork.download_cover_art({'album_name': 'Live'}, str(tmp_path), context)
    fetch.assert_not_called()
    assert not os.path.exists(tmp_path / 'cover.jpg')
