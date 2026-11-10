"""#2003: a split single became two albums on two artist pages.

"3OH!3 versus Innerpartysystem" is one release, side A by 3OH!3 and side B by
Innerpartysystem. the folder went under 3OH!3, but the library album row took
the artist the download was for, so side B minted its own album under
Innerpartysystem. both sides must land on one album, under the lead.
"""

from __future__ import annotations

import pytest

from core.imports.collab_artist import album_lead_artist
from database.music_database import MusicDatabase

SPLIT = {
    'name': '3OH!3 versus Innerpartysystem',
    'album_type': 'single',
    'artists': [{'name': '3OH!3'}, {'name': 'Innerpartysystem'}],
}


def test_the_lead_is_the_first_credited_artist():
    assert album_lead_artist(SPLIT, 'Innerpartysystem') == '3OH!3'
    assert album_lead_artist(SPLIT, 'innerpartysystem') == '3OH!3'


def test_nothing_changes_for_the_lead_a_solo_album_or_a_stranger():
    assert album_lead_artist(SPLIT, '3OH!3') == ''
    assert album_lead_artist({'artists': [{'name': 'Drake'}]}, 'Future') == ''
    # a download for someone the album does not credit is not ours to move
    assert album_lead_artist(SPLIT, 'Katy Perry') == ''
    assert album_lead_artist(None, 'x') == ''
    assert album_lead_artist({'artists': ['3OH!3', 'Innerpartysystem']}, 'Innerpartysystem') == '3OH!3'


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / 'music.db'))


def _import(se, db, tmp_path, download_artist, track_title, track_artist):
    audio = tmp_path / f'{track_title}.flac'
    audio.write_bytes(b'fLaC')
    context = {
        'source': 'spotify',
        'artist': {'name': download_artist},
        'album': SPLIT,
        'track_info': {
            'name': track_title,
            'artists': [{'name': track_artist}],
            'track_number': 1,
            'duration_ms': 200000,
        },
        '_final_processed_path': str(audio),
    }
    se.record_soulsync_library_entry(context, {'name': download_artist},
                                     {'album_name': SPLIT['name']})


def test_both_sides_land_on_one_album_under_the_lead(db, tmp_path, monkeypatch):
    import core.imports.side_effects as se

    class _Cfg:
        def get_active_media_server(self):
            return 'soulsync'

        def get(self, *a, **k):
            return k.get('default') if k else (a[1] if len(a) > 1 else None)

    monkeypatch.setattr(se, '_get_config_manager', lambda: _Cfg())
    monkeypatch.setattr(se, 'get_database', lambda: db)

    _import(se, db, tmp_path, '3OH!3', "Don't Trust Me (Innerpartysystem remix)", '3OH!3')
    _import(se, db, tmp_path, 'Innerpartysystem', "Don't Stop (3OH!3 remix)", 'Innerpartysystem')

    with db._get_connection() as conn:
        rows = conn.execute("""
            SELECT ar.name, al.id, t.track_artist
              FROM lib2_tracks t
              JOIN lib2_albums al ON t.album_id = al.id
              JOIN lib2_artists ar ON al.primary_artist_id = ar.id
             ORDER BY t.id""").fetchall()
    assert len(rows) == 2
    assert {r[0] for r in rows} == {'3OH!3'}
    assert len({r[1] for r in rows}) == 1, 'one release, one album row'
    # side B still credits who recorded it
    assert rows[1][2] == 'Innerpartysystem'
