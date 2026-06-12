"""Watchlist roster export builder (corruption's request) — JSON / CSV / txt,
optional external links, deterministic columns."""

from __future__ import annotations

import csv
import io
import json

from core.exports.artist_export import build_artist_export, export_mime_and_ext


_ARTISTS = [
    {'artist_name': 'Rob Zombie', 'spotify_artist_id': 'sp1',
     'musicbrainz_artist_id': 'mb1', 'deezer_artist_id': 'dz1'},
    {'artist_name': 'Nobody IDs', 'spotify_artist_id': None},
]


def test_txt_is_names_one_per_line():
    out = build_artist_export(_ARTISTS, fmt='txt')
    assert out == 'Rob Zombie\nNobody IDs'


def test_json_includes_present_ids_only():
    out = json.loads(build_artist_export(_ARTISTS, fmt='json'))
    assert out[0]['name'] == 'Rob Zombie'
    assert out[0]['spotify_artist_id'] == 'sp1' and out[0]['musicbrainz_artist_id'] == 'mb1'
    assert 'deezer_artist_id' in out[0]
    assert out[1] == {'name': 'Nobody IDs'}          # null id dropped, no links key


def test_json_links_when_requested():
    out = json.loads(build_artist_export(_ARTISTS, fmt='json', include_links=True))
    assert out[0]['links']['spotify'] == 'https://open.spotify.com/artist/sp1'
    assert out[0]['links']['musicbrainz'] == 'https://musicbrainz.org/artist/mb1'
    assert 'links' not in out[1]                     # no ids → no links


def test_csv_header_and_rows():
    out = build_artist_export(_ARTISTS, fmt='csv')
    rows = list(csv.reader(io.StringIO(out)))
    assert rows[0][0] == 'name' and 'spotify_artist_id' in rows[0]
    assert rows[1][0] == 'Rob Zombie'
    assert rows[2][0] == 'Nobody IDs'


def test_csv_adds_url_columns_with_links():
    out = build_artist_export(_ARTISTS, fmt='csv', include_links=True)
    header = next(csv.reader(io.StringIO(out)))
    assert 'spotify_url' in header and 'discogs_url' in header


def test_empty_and_bad_format():
    assert build_artist_export([], fmt='txt') == ''
    assert build_artist_export(None, fmt='json') == '[]'
    assert build_artist_export(_ARTISTS, fmt='nonsense').startswith('[')  # falls back to json


def test_mime_and_ext():
    assert export_mime_and_ext('csv') == ('text/csv', 'csv')
    assert export_mime_and_ext('txt') == ('text/plain', 'txt')
    assert export_mime_and_ext('weird') == ('application/json', 'json')


# ── endpoint wiring (empty watchlist → valid shapes + headers) ──────────────
import os, tempfile  # noqa: E402
os.environ['DATABASE_PATH'] = os.path.join(tempfile.mkdtemp(prefix='soulsync-testdb-wlexp-'), 'w.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'
import pytest  # noqa: E402
web_server = pytest.importorskip('web_server')


@pytest.fixture
def client():
    return web_server.app.test_client()


def test_export_endpoint_wiring(client):
    # Don't assume an empty DB (a shared test run may have rows) — just verify the
    # endpoint returns a valid JSON array + the right headers/columns.
    r = client.get('/api/watchlist/export?format=json')
    assert r.status_code == 200
    assert isinstance(json.loads(r.data.decode()), list)
    assert r.headers.get('X-Export-Ext') == 'json'

    r2 = client.get('/api/watchlist/export?format=csv&links=1')
    assert r2.status_code == 200 and r2.headers.get('X-Export-Ext') == 'csv'
    assert 'spotify_url' in r2.data.decode().splitlines()[0]   # header row with links


# ── library-side: extra services + extra_fields passthrough ─────────────────
_LIB = [{
    'name': 'Rob Zombie', 'spotify_artist_id': 'sp1', 'tidal_artist_id': 'td1',
    'qobuz_artist_id': 'qz1', 'lastfm_url': 'https://last.fm/x', 'soul_id': 'soul_abc',
    'album_count': 10, 'track_count': 159,
}]


def test_tidal_qobuz_links_and_extra_fields_json():
    out = json.loads(build_artist_export(_LIB, fmt='json', include_links=True,
                                         extra_fields=['lastfm_url', 'soul_id', 'album_count', 'track_count']))
    a = out[0]
    assert a['tidal_artist_id'] == 'td1' and a['qobuz_artist_id'] == 'qz1'
    assert a['links']['tidal'] == 'https://tidal.com/artist/td1'
    assert a['links']['qobuz'] == 'https://www.qobuz.com/artist/qz1'
    assert a['lastfm_url'] == 'https://last.fm/x' and a['soul_id'] == 'soul_abc'
    assert a['album_count'] == 10 and a['track_count'] == 159


def test_extra_fields_become_csv_columns():
    out = build_artist_export(_LIB, fmt='csv', extra_fields=['album_count', 'track_count'])
    header = next(csv.reader(io.StringIO(out)))
    assert 'album_count' in header and 'track_count' in header
    assert 'tidal_artist_id' in header        # new service column present


def test_library_export_endpoint_wiring(client):
    # Robust to a shared DB that may already hold artist rows.
    r = client.get('/api/library/artists/export?format=json&contents=1&links=1')
    assert r.status_code == 200
    assert isinstance(json.loads(r.data.decode()), list)
    assert r.headers.get('X-Export-Ext') == 'json'
    r2 = client.get('/api/library/artists/export?format=csv&contents=1')
    header = r2.data.decode().splitlines()[0]
    assert 'album_count' in header and 'track_count' in header
