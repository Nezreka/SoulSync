"""#2217: an import writes what the library knows, and one orchestra is one
credit.

carlosjfcasero imported from the Import folder and the import's own tag check
filed 167 Library Re-tag findings at once: "Genre: empty -> Pop", "Disc total:
empty -> 3", and "Anne Dudley, Chamber Orchestra of London" -> the same plus
"The Chamber Orchestra Of London"."""

from __future__ import annotations

import json
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from mutagen.flac import FLAC

from core.library2.schema import ensure_library_v2_schema
from core.library2.tag_cache import persist_tag_cache, read_tag_snapshot
from tests.lib2_seed import RowDb

ORCHESTRA = "Chamber Orchestra of London"
THE_ORCHESTRA = "The Chamber Orchestra Of London"


@pytest.fixture
def settings():
    return {'metadata_enhancement.embed_album_art': False,
            'metadata_enhancement.cover_art_download': False}


@pytest.fixture
def library(tmp_path, monkeypatch, settings):
    """a disc-1 track of a 3-disc release, tagged the way the import left it:
    no genre, a disc number and no disc total"""
    path = tmp_path / '01 - Suite from Poldark.flac'
    path.write_bytes(b'fLaC\x80\x00\x00\x22\x00\x10\x00\x10' + b'\x00' * 6
                     + b'\x0a\xc4\x42\xf0' + b'\x00' * 20)
    audio = FLAC(path)
    for key, value in {'title': 'Suite from Poldark', 'artist': f'Anne Dudley, {ORCHESTRA}',
                       'albumartist': 'Anne Dudley', 'album': 'Poldark', 'tracknumber': '1',
                       'tracktotal': '2', 'discnumber': '1', 'date': '2019'}.items():
        audio[key] = [value]
    audio.save()
    config = SimpleNamespace(get=lambda key, default=None: settings.get(key, default))
    monkeypatch.setattr('core.metadata.common.get_config_manager', lambda: config)
    db = RowDb(str(tmp_path / 'library.db'))
    with closing(db._get_connection()) as conn, conn:
        ensure_library_v2_schema(conn)
        conn.executemany("INSERT INTO lib2_artists(id,name) VALUES(?,?)",
                         [(1, 'Anne Dudley'), (2, ORCHESTRA), (3, THE_ORCHESTRA)])
        conn.execute("INSERT INTO lib2_albums(id,primary_artist_id,title,year,genres,expected_track_count,image_url) "
                     "VALUES(1,1,'Poldark',2019,'[\"Pop\"]',2,'https://example.test/cover')")
        conn.executemany("INSERT INTO lib2_tracks(id,album_id,title,track_number,disc_number) VALUES(?,1,?,?,?)",
                         [(1, 'Suite from Poldark', 1, 1), (2, 'Two', 2, 1), (3, 'Three', 1, 2),
                          (4, 'Four', 1, 3)])
        # credited to both spellings of the orchestra: two artist rows
        conn.executemany("INSERT INTO lib2_track_artists(track_id,artist_id,role,position) VALUES(1,?,?,?)",
                         [(1, 'primary', 0), (2, 'featured', 1), (3, 'featured', 2)])
        conn.execute("INSERT INTO lib2_track_files(id,track_id,path,format) VALUES(1,1,?,'flac')", (str(path),))
        conn.execute("INSERT INTO lib2_release_editions(id,release_group_id,track_count,disc_count) VALUES(1,1,4,3)")
        conn.executemany('INSERT INTO lib2_recordings(id,title) VALUES(?,?)', [(i, f'T{i}') for i in range(1, 5)])
        conn.executemany('INSERT INTO lib2_release_tracks(release_edition_id,recording_id,track_id,track_number,disc_number) '
                         'VALUES(1,?,?,?,?)', [(1, 1, 1, 1), (2, 2, 2, 1), (3, 3, 1, 2), (4, 4, 1, 3)])
        conn.execute("CREATE TABLE repair_findings(id INTEGER PRIMARY KEY, job_id TEXT, finding_type TEXT, severity TEXT, "
                     "status TEXT DEFAULT 'pending', entity_type TEXT, entity_id TEXT, file_path TEXT, title TEXT, "
                     "description TEXT, details_json TEXT, user_action TEXT, resolved_at TEXT, "
                     "updated_at TEXT DEFAULT CURRENT_TIMESTAMP, last_error TEXT, fix_claimed_at TEXT)")
    return db, path, config


def _checks(library):
    db, path, config = library
    with closing(db._get_connection()) as conn, conn:
        persist_tag_cache(conn, 1, read_tag_snapshot(str(path)), config)
        row = conn.execute('SELECT tags_json FROM lib2_track_files WHERE id=1').fetchone()
    return json.loads(row[0])['_validation']['checks']


# --- (c) one orchestra, one credit -------------------------------------------

def test_two_spellings_of_one_artist_are_one_credit(library):
    from core.library2.retag import _credited_artists
    db, _path, _config = library
    with closing(db._get_connection()) as conn:
        assert _credited_artists(conn, 1) == ['Anne Dudley', ORCHESTRA]


def test_the_file_credit_is_not_filed_for_the_duplicate(library):
    assert _checks(library)['artist'] == 'correct'


def test_a_leading_the_and_case_do_not_make_another_artist():
    from core.tag_writer import _same_artist_names
    assert _same_artist_names(f'Anne Dudley, {ORCHESTRA}', f'Anne Dudley; {THE_ORCHESTRA}')
    # a different name is still a different credit
    assert not _same_artist_names(ORCHESTRA, 'London Symphony Orchestra')


def test_a_source_credit_for_the_other_spelling_is_not_added(tmp_path):
    from core.library2.provider_credits import link_credited_artists
    from database.music_database import MusicDatabase
    from tests.support.catalogue_seed import seed_album, seed_artist, seed_track

    db = MusicDatabase(str(tmp_path / 'music.db'))
    conn = db._get_connection()
    anne = seed_artist(conn, server_id='a', name='Anne Dudley', server_source='soulsync')
    tagged = seed_artist(conn, server_id='o', name=ORCHESTRA, server_source='soulsync')
    sourced = seed_artist(conn, server_id='t', name=THE_ORCHESTRA, server_source='soulsync')
    guest = seed_artist(conn, server_id='g', name='Guest Singer', server_source='soulsync')
    conn.execute("UPDATE lib2_artists SET spotify_id='sp-the-orch' WHERE id=?", (sourced,))
    conn.execute("UPDATE lib2_artists SET spotify_id='sp-guest' WHERE id=?", (guest,))
    album = seed_album(conn, server_id='p', title='Poldark', artist_id=anne, server_source='soulsync')
    track = seed_track(conn, server_id='s', title='Suite', album_id=album, artist_id=anne,
                       server_source='soulsync')
    conn.execute("INSERT INTO lib2_track_artists(track_id,artist_id,role,position) VALUES(?,?,'featured',1)",
                 (track, tagged))
    conn.commit()

    added = link_credited_artists(conn, 'track', track, 'spotify',
                                  [{'id': 'sp-anne'}, {'id': 'sp-the-orch'}, {'id': 'sp-guest'}])

    credited = {r[0] for r in conn.execute(
        'SELECT artist_id FROM lib2_track_artists WHERE track_id=?', (track,))}
    assert sourced not in credited
    assert guest in credited and added == 1


# --- (a) (b) the import fills what the library knows --------------------------

def test_the_import_fills_the_blank_genre_and_disc_total(library):
    from core.library2.validation import fill_known_blanks
    db, path, config = library
    before = _checks(library)
    assert before['genre'] == 'missing' and before['total_discs'] == 'missing'

    assert fill_known_blanks(db, [1], config) == 1

    audio = FLAC(path)
    assert audio['genre'] == ['Pop']
    assert audio['disctotal'] == ['3'] and audio['discnumber'] == ['1']
    # nothing the file already said changed
    assert audio['artist'] == [f'Anne Dudley, {ORCHESTRA}'] and audio['date'] == ['2019']
    after = _checks(library)
    assert not {k for k, v in after.items() if v in ('missing', 'mismatch')}


def test_a_genre_the_file_has_is_kept(library):
    from core.library2.validation import fill_known_blanks
    db, path, config = library
    audio = FLAC(path)
    audio['genre'] = ['Soundtrack']
    audio.save()
    fill_known_blanks(db, [1], config)
    assert FLAC(path)['genre'] == ['Soundtrack']


def test_no_tag_writing_means_no_fill(library, settings):
    from core.library2.validation import fill_known_blanks
    db, path, config = library
    settings['metadata_enhancement.enabled'] = False
    assert fill_known_blanks(db, [1], config) == 0
    assert 'genre' not in FLAC(path)


def test_the_import_refresh_fills_before_it_checks(library, monkeypatch):
    """the import's own read of the published file: it used to file the
    blanks as findings; now they are written first and nothing is filed"""
    from core.library2 import validation
    db, path, config = library
    monkeypatch.setattr('core.library2.paths.resolve_lib2_path',
                        lambda p, config_manager=None: p)
    monkeypatch.setattr('core.library2.paths.missing_path_root_is_healthy',
                        lambda *_a, **_k: True)
    validation.refresh_imported_metadata(db, [str(path)], fill_blanks=True)
    assert FLAC(path)['genre'] == ['Pop']
    with closing(db._get_connection()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM repair_findings WHERE status='pending'").fetchone()[0] == 0


def test_a_reindex_never_writes_a_file(library, monkeypatch):
    """a full refresh and a media server's file go through the same read:
    they only look, the blanks stay findings"""
    from core.library2 import validation
    db, path, _config = library
    monkeypatch.setattr('core.library2.paths.resolve_lib2_path',
                        lambda p, config_manager=None: p)
    before = path.read_bytes()
    validation.refresh_imported_metadata(db, [str(path)])
    assert path.read_bytes() == before


def test_only_the_import_pipeline_asks_for_the_fill():
    import inspect
    from core.imports import pipeline, side_effects
    from core.downloads import post_processing
    from api import database_admin
    from core.library2 import media_server_sync
    for module in (pipeline, side_effects):
        assert 'fill_known_tags=True' in inspect.getsource(module)
    assert 'fill_blanks=True' in inspect.getsource(post_processing)
    for module in (database_admin, media_server_sync):
        assert 'fill_known_tags' not in inspect.getsource(module)


# --- (b) the import's own tag carries the disc total ---------------------------

def test_the_known_disc_total_reaches_the_import_metadata():
    from core.metadata import source as src_module
    assert src_module._known_total_discs({}, {'total_discs': 3}, 1) == 3
    assert src_module._known_total_discs({'total_discs': 2}, {}, 3) == 3   # disc 3 proves three
    assert src_module._known_total_discs(None, {'total_discs': 'junk'}, None) == 1

    cfg = MagicMock()
    cfg.get.side_effect = lambda key, default=None: {
        'metadata_enhancement.embed_album_art': False}.get(key, default)
    context = {
        "original_search_result": {"title": "Suite from Poldark", "artists": [{"name": "Anne Dudley"}],
                                   "disc_number": 1},
        "spotify_album": {"id": "1", "name": "Poldark", "artists": [{"name": "Anne Dudley"}],
                          "total_discs": 3},
        "source": "deezer",
    }
    with patch.object(src_module, "get_config_manager", return_value=cfg), \
            patch.object(src_module, "library_has_artist", lambda n: False), \
            patch.object(src_module, "library_has_identified_artist", lambda n: False):
        meta = src_module.extract_source_metadata(
            json.loads(json.dumps(context)), {"name": "Anne Dudley"}, {"album_name": "Poldark", "disc_number": 1})
        single = src_module.extract_source_metadata(
            {**context, "spotify_album": {**context["spotify_album"], "total_discs": 1}},
            {"name": "Anne Dudley"}, {"album_name": "Poldark", "disc_number": 1})
    assert meta["total_discs"] == 3
    # a one-disc release writes no total, as before
    assert "total_discs" not in single
