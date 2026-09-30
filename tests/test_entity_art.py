"""art for cached entities that were stored without any (sept 29 2026).

the genre browser showed 🎵 and 🎤 almost everywhere: 36k of 55k cached
deezer tracks and all 12k itunes artists had no image_url. the art was mostly
recoverable: deezer payloads carry md5_image, and itunes artists have imaged
twins or a library thumbnail.
"""

import json
import sqlite3

import pytest

from core.metadata import entity_art as art

MD5 = '861fe0af7ae4a2fc078dac334b0547e4'


def test_a_deezer_track_gets_its_cover_from_md5_image():
    assert art.art_from_raw({'title': 'x', 'md5_image': MD5}) == (
        f'https://cdn-images.dzcdn.net/images/cover/{MD5}/500x500-000000-80-0-0.jpg')


def test_sized_covers_win_over_the_md5():
    raw = {'album': {'cover_xl': 'https://cdn-images.dzcdn.net/images/cover/abc/1000x1000.jpg',
                     'md5_image': MD5}}
    assert art.art_from_raw(json.dumps(raw)).endswith('/abc/1000x1000.jpg')


def test_spotify_and_itunes_payloads():
    assert art.art_from_raw({'album': {'images': [{'url': 'https://i.scdn.co/image/a'}]}}) == (
        'https://i.scdn.co/image/a')
    assert art.art_from_raw({'artworkUrl100': 'https://is1.mzstatic.com/x/100x100bb.jpg'}) == (
        'https://is1.mzstatic.com/x/600x600bb.jpg')


def test_deezers_blank_md5_is_not_a_cover():
    assert art.art_from_raw({'md5_image': 'd41d8cd98f00b204e9800998ecf8427e'}) is None
    assert art.art_from_raw('not json') is None
    assert art.art_from_raw(None) is None


@pytest.fixture
def cur():
    c = sqlite3.connect(':memory:')
    c.executescript("""
        CREATE TABLE metadata_cache_entities (source TEXT, entity_type TEXT, entity_id TEXT,
            name TEXT, image_url TEXT, followers INTEGER, raw_json TEXT);
        CREATE TABLE artists (name TEXT, thumb_url TEXT);
    """)
    return c.cursor()


def test_rows_without_art_are_filled_from_their_payload(cur):
    cur.execute("INSERT INTO metadata_cache_entities VALUES ('deezer','track','1','Telstar','',0,?)",
                (json.dumps({'md5_image': MD5}),))
    rows = [{'name': 'Telstar', 'source': 'deezer', 'entity_id': '1', 'image_url': ''},
            {'name': 'Kept', 'source': 'deezer', 'entity_id': '2', 'image_url': 'https://x/y.jpg'}]
    assert art.fill_from_payloads(cur, rows, 'track') == 1
    assert MD5 in rows[0]['image_url']
    assert rows[1]['image_url'] == 'https://x/y.jpg'


def test_an_artist_takes_its_twins_photo_then_the_library_thumb(cur, monkeypatch):
    monkeypatch.setattr('core.metadata.normalize_image_url', lambda u: '/api/image-cache/lib')
    cur.execute("INSERT INTO metadata_cache_entities VALUES "
                "('deezer','artist','9','Boards Of Canada','https://cdn/boc.jpg',5,'{}')")
    cur.execute("INSERT INTO artists VALUES ('Orr', '/library/metadata/1/thumb')")
    artists = [{'name': 'Boards of Canada', 'image_url': ''},
               {'name': 'Orr', 'image_url': None},
               {'name': 'Nobody', 'image_url': ''}]
    assert art.fill_artist_photos(cur, artists) == 2
    assert artists[0]['image_url'] == 'https://cdn/boc.jpg'
    assert artists[1]['image_url'] == '/api/image-cache/lib'
    assert artists[2]['image_url'] == ''
