"""Regression tests for ``MusicDatabase.get_artist_full_detail`` source-ID
resolution.

Bug (reported by Boulder, 2026-05-28): opening a library artist from a
*non-library* search result (e.g. a MusicBrainz hit) leaves the artist-detail
page holding the source ID — the MBID — not the catalogue's own id. The
standard /api/artist-detail route resolves that via
``find_library_artist_for_source``, but the **enhanced-view** endpoint
(``/api/library/artist/<id>/enhanced``) and the quality-analysis endpoint call
``get_artist_full_detail`` directly with whatever ID the page holds. With only
a ``WHERE id = ?`` lookup, that 404'd ("Artist with ID <mbid> not found") and
the enhanced view failed to load.

Fix: when the direct lookup misses, resolve against any provider id the row
carries — two dedicated columns plus the ``external_ids`` bucket.

These are isolated DB-method tests — no Flask, no route layer — so the SQL
fallback itself is exercised.
"""

import pytest

from database.music_database import MusicDatabase
from tests.support.catalogue_seed import seed_album, seed_artist, seed_track


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


def _seed_kendrick(db, *, spotify_id=None, musicbrainz_id=None, external_ids=None):
    """A Kendrick Lamar library artist with one album, carrying whichever
    provider ids the test needs. Returns the catalogue id."""
    conn = db._get_connection()
    artist_id = seed_artist(conn, server_id='ar-kdot', name='Kendrick Lamar')
    conn.execute(
        "UPDATE lib2_artists SET spotify_id=?, musicbrainz_id=?,"
        "                        external_ids=COALESCE(?, external_ids) WHERE id=?",
        (spotify_id, musicbrainz_id, external_ids, artist_id))
    seed_album(conn, server_id='alb-1', title='DAMN.', artist_id=artist_id, year=2017)
    conn.commit()
    return artist_id


def test_direct_id_lookup_still_works(db):
    """The primary path — the catalogue's own id — must be unaffected by the
    new fallback."""
    artist_id = _seed_kendrick(db, musicbrainz_id='381086ea-mbid')

    result = db.get_artist_full_detail(artist_id)

    assert result['success'] is True
    assert result['artist']['name'] == 'Kendrick Lamar'
    assert [a['title'] for a in result['albums']] == ['DAMN.']


def test_resolves_by_musicbrainz_id(db):
    """The exact bug: page holds the MBID, not the catalogue id. Must resolve
    and return the library artist + albums instead of 404ing."""
    _seed_kendrick(db, musicbrainz_id='381086ea-mbid')

    result = db.get_artist_full_detail('381086ea-mbid')

    assert result['success'] is True
    assert result['artist']['name'] == 'Kendrick Lamar'
    assert [a['title'] for a in result['albums']] == ['DAMN.']


def test_resolves_by_spotify_id(db):
    """Resolution isn't MusicBrainz-specific — Spotify has a column too."""
    _seed_kendrick(db, spotify_id='sp-kdot')

    result = db.get_artist_full_detail('sp-kdot')

    assert result['success'] is True
    assert result['artist']['name'] == 'Kendrick Lamar'


def test_resolves_by_a_provider_without_a_column(db):
    """Deezer and the rest live in `external_ids`; a click from a Deezer
    search result has to resolve just as well as one from Spotify."""
    _seed_kendrick(db, external_ids='{"deezer": "dz-4321"}')

    result = db.get_artist_full_detail('dz-4321')

    assert result['success'] is True
    assert result['artist']['name'] == 'Kendrick Lamar'


def test_unknown_id_returns_not_found(db):
    """An ID that matches neither the catalogue id nor any provider id 404s."""
    _seed_kendrick(db, musicbrainz_id='381086ea-mbid')

    result = db.get_artist_full_detail('totally-unknown-id')

    assert result['success'] is False
    assert 'not found' in result['error']


def test_provider_ids_keep_the_names_the_page_reads(db):
    """The discography modal reads `spotify_artist_id` / `deezer_id` straight
    off the response; the catalogue's storage shape stays hidden."""
    _seed_kendrick(db, spotify_id='sp-kdot', external_ids='{"deezer": "dz-4321"}')

    artist = db.get_artist_full_detail('sp-kdot')['artist']

    assert artist['spotify_artist_id'] == 'sp-kdot'
    assert artist['deezer_id'] == 'dz-4321'
    assert artist['thumb_url'] is None       # legacy's name for the image


def test_alias_rows_contribute_their_releases(db):
    """§40: the same artist under a second, unlinked provider identity is its
    own row pointing at the canonical one. Legacy could only guess at this
    with "same name on the same server"."""
    conn = db._get_connection()
    canonical = _seed_kendrick(db)
    alias = seed_artist(conn, server_id='ar-kdot-alias', name='K. Lamar')
    conn.execute("UPDATE lib2_artists SET canonical_artist_id=? WHERE id=?",
                 (canonical, alias))
    seed_album(conn, server_id='alb-2', title='Mr. Morale', artist_id=alias, year=2022)
    conn.commit()

    titles = [a['title'] for a in db.get_artist_full_detail(canonical)['albums']]

    assert titles == ['Mr. Morale', 'DAMN.']     # year DESC
    # ... and asking via the alias row answers with the same family.
    assert [a['title'] for a in db.get_artist_full_detail(alias)['albums']] == titles


def test_tracks_carry_the_file_the_quality_scan_needs(db):
    """The quality-analysis endpoint reads `file_path` and `bitrate` off each
    track. In the catalogue those belong to the file row (ADR-03), and a
    deleted file must not present itself as a track the user still has."""
    conn = db._get_connection()
    artist_id = _seed_kendrick(db)
    album_id = conn.execute("SELECT id FROM lib2_albums WHERE title='DAMN.'").fetchone()[0]
    seed_track(conn, server_id='tr-1', title='HUMBLE.', album_id=album_id,
               artist_id=artist_id, track_number=1,
               file_path='/music/humble.flac', bitrate=1000)
    gone = seed_track(conn, server_id='tr-2', title='LOYALTY.', album_id=album_id,
                      artist_id=artist_id, track_number=2,
                      file_path='/music/loyalty.flac', bitrate=1000)
    conn.execute("UPDATE lib2_track_files SET file_state='deleted' WHERE track_id=?",
                 (gone,))
    conn.commit()

    tracks = db.get_artist_full_detail(artist_id)['albums'][0]['tracks']

    assert [(t['title'], t['file_path'], t['bitrate']) for t in tracks] == [
        ('HUMBLE.', '/music/humble.flac', 1000),
        ('LOYALTY.', None, None),
    ]


def test_record_type_comes_from_the_catalogue(db):
    """Legacy sniffed the title for "single"/"ep" when its column was empty.
    v2's `album_type` is NOT NULL and is what scan, import and providers set."""
    conn = db._get_connection()
    artist_id = _seed_kendrick(db)
    single = seed_album(conn, server_id='alb-single', title='The Heart Part 5',
                        artist_id=artist_id, year=2022)
    conn.execute("UPDATE lib2_albums SET album_type='single' WHERE id=?", (single,))
    conn.commit()

    by_title = {a['title']: a for a in db.get_artist_full_detail(artist_id)['albums']}

    assert by_title['The Heart Part 5']['record_type'] == 'single'
    assert by_title['DAMN.']['record_type'] == 'album'
