"""discord (SeadogsBooty): a track fixed by hand from the import inbox landed
in "Album/Rise" instead of "Album/[2013] Rise".

the itunes and discogs clients return an already-normalized album with a
'release_date', but the typed converters read the raw api fields
('releaseDate', 'year'), so the matcher's album and the single-file import
both came out with no date and $year rendered empty.
"""

from __future__ import annotations

from core.imports.album import build_album_import_context
from core.imports.resolution import _build_single_import_context_payload
from core.metadata.album_tracks import _build_album_info

# what ItunesClient.get_album returns
ITUNES_ALBUM = {
    'id': '123', 'name': 'Rise', 'images': [{'url': 'x'}],
    'artists': [{'name': 'A Skylit Drive', 'id': '9'}],
    'release_date': '2013-07-02', 'total_tracks': 11, 'album_type': 'album',
    '_source': 'itunes',
}
# what DiscogsClient.get_album returns
DISCOGS_ALBUM = {
    'id': '5', 'name': 'Identity On Fire', 'artist': 'A Skylit Drive',
    'artists': ['A Skylit Drive'], 'release_date': '2016',
    'total_tracks': 12, 'album_type': 'album', 'image_url': '', 'images': [],
}


def test_matcher_album_keeps_the_itunes_date():
    assert _build_album_info(ITUNES_ALBUM, '123', source='itunes')['release_date'] == '2013-07-02'


def test_matcher_album_keeps_the_discogs_date():
    assert _build_album_info(DISCOGS_ALBUM, '5', source='discogs')['release_date'] == '2016'


def test_raw_shapes_still_convert():
    raw_itunes = {'collectionId': 1, 'collectionName': 'Rise', 'artistName': 'A', 'releaseDate': '2013-07-02T07:00:00Z'}
    assert _build_album_info(raw_itunes, '1', source='itunes')['release_date'] == '2013-07-02'


def test_matched_album_reaches_the_import_context():
    album = _build_album_info(ITUNES_ALBUM, '123', source='itunes')
    ctx = build_album_import_context(album, {'name': 'Rise', 'track_number': 1}, source='itunes')
    assert ctx['album']['release_date'] == '2013-07-02'


def test_single_import_keeps_the_album_date():
    track = {'id': 't1', 'name': 'Rise', 'artists': [{'name': 'A Skylit Drive'}], 'album': ITUNES_ALBUM}
    payload = _build_single_import_context_payload(track, 'itunes', ['itunes'], requested_title='Rise')
    assert payload['context']['album']['release_date'] == '2013-07-02'
