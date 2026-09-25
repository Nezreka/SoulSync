"""The seam: what actually lands in the library when a compilation imports.

The helper's own tests only prove it returns the right string. This drives
``record_soulsync_library_entry`` against a REAL database and reads the rows
back, because the bug being fixed was never in the decision — it was in which
artist the album row was written under.

Pinned here: the album moves to Various Artists AND the track keeps the artist
who actually recorded it. Moving both would trade one wrong answer for
another — Don Felder really is the artist of his track on that soundtrack.
"""

from __future__ import annotations

import pytest

from database.music_database import MusicDatabase


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / 'music.db'))


@pytest.fixture
def imported(db, tmp_path, monkeypatch):
    """Run one import and hand back a reader for the rows it wrote."""
    import core.imports.side_effects as se

    class _Cfg:
        def get_active_media_server(self):
            return 'soulsync'

        def get(self, *a, **k):
            return k.get('default') if k else (a[1] if len(a) > 1 else None)

    monkeypatch.setattr(se, '_get_config_manager', lambda: _Cfg())
    monkeypatch.setattr(se, 'get_database', lambda: db)

    def _run(album_ctx, *, download_artist='Don Felder', track_artist='Don Felder',
             track_title='Heavy Metal (Takin a Ride)'):
        # a distinct file per import: the track row is keyed on file path, so
        # reusing one path updates the first track instead of adding a second
        audio = tmp_path / f'{len(list(tmp_path.glob("*.flac")))}-{track_title}.flac'
        audio.write_bytes(b'fLaC')
        context = {
            'source': 'spotify',
            'artist': {'name': download_artist},
            'album': album_ctx,
            'track_info': {
                'name': track_title,
                'artists': [{'name': track_artist}],
                'track_number': 3,
                'duration_ms': 210000,
            },
            '_final_processed_path': str(audio),
        }
        se.record_soulsync_library_entry(
            context, {'name': download_artist}, {'album_name': album_ctx.get('name', '')})

        with db._get_connection() as conn:
            cur = conn.cursor()
            # the catalogue is Library v2 on this branch
            cur.execute("""
                SELECT ar.name, al.title, t.title, t.track_artist
                FROM lib2_tracks t
                JOIN lib2_albums al ON t.album_id = al.id
                JOIN lib2_artists ar ON al.primary_artist_id = ar.id
                ORDER BY t.id
            """)
            return cur.fetchall()

    return _run


SOUNDTRACK = {
    'name': 'Shrek 2 (Original Motion Picture Soundtrack)',
    'album_type': 'compilation',
    'artists': [{'name': 'Various Artists'}],
}

REAL_ALBUM = {
    'name': 'Airborne',
    'album_type': 'album',
    'artists': [{'name': 'Don Felder'}],
}


def test_the_soundtrack_lands_under_various_artists_not_the_contributor(imported):
    rows = imported(SOUNDTRACK)

    assert rows, 'the import wrote nothing at all'
    album_artist, album_title, _track_title, _track_artist = rows[0]
    assert album_artist == 'Various Artists'
    assert album_title == 'Shrek 2 (Original Motion Picture Soundtrack)'


def test_the_track_still_credits_the_artist_who_recorded_it(imported):
    """Don Felder must not vanish — he is the artist OF THE TRACK."""
    rows = imported(SOUNDTRACK)

    _album_artist, _album_title, _track_title, track_artist = rows[0]
    assert track_artist == 'Don Felder'


def test_an_ordinary_album_still_lands_under_its_artist(imported):
    """The guard against over-firing: normal releases are untouched."""
    rows = imported(REAL_ALBUM, track_title='Bad Girls')

    album_artist, album_title, _track_title, _track_artist = rows[0]
    assert album_artist == 'Don Felder'
    assert album_title == 'Airborne'


def test_two_soundtracks_share_one_various_artists_row(imported):
    """The reported shape was 45 albums stacked on one artist. They should
    stack on Various Artists instead of minting a row per soundtrack."""
    imported(SOUNDTRACK)
    rows = imported({
        'name': 'Shrek The Third',
        'album_type': 'compilation',
        'artists': [{'name': 'Various Artists'}],
    }, track_title='Immigrant Song')

    artists = {r[0] for r in rows}
    albums = {r[1] for r in rows}
    assert artists == {'Various Artists'}
    assert len(albums) == 2, 'two distinct soundtracks, one artist'
