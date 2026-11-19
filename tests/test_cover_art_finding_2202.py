"""#2202: Apply Art uses the cover the finding shows, whichever tile it is,
and a finding only exists when a cover is really missing.

Shaped like the reporter's findings:
- Dua Lipa: a library cover, no cover in the files (embedding off), no
  cover file in the folder, nothing searched. Apply Art said "No artwork
  URL found in finding details".
- Stadium Arcadium: tracks in "Disc 1", cover.jpg in the release folder
  above. The scan called the folder coverless while the tile showed it.
- Listen: a saved library url that loads nothing. "The library has a cover
  to copy" over an empty tile.
"""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

from tests import test_missing_cover_art as base  # noqa: F401 - stubs spotipy/core.settings first
from core.metadata import art_apply as aa
from core.repair_jobs import cover_art_state as cas
from core.repair_jobs import missing_cover_art as mca


@pytest.fixture(autouse=True)
def _no_name_search(monkeypatch):
    monkeypatch.setattr(mca, '_artist_art_by_name', lambda *a, **k: (None, None))


def _cfg(**values):
    return SimpleNamespace(get=lambda key, default=None: values.get(key, default))


# ── the folder cover, by any of its names ──

@pytest.mark.parametrize('name', ['cover.jpg', 'Cover.JPG', 'FRONT.png', 'folder.webp',
                                  'AlbumArt.jpeg', 'album.jpg'])
def test_a_folder_cover_counts_by_any_name_and_case(tmp_path, name):
    (tmp_path / name).write_bytes(b'\xff\xd8x')
    assert aa.folder_cover_file(str(tmp_path)) == str(tmp_path / name)
    assert aa.folder_has_cover_sidecar(str(tmp_path)) is True


def test_other_pictures_are_not_a_cover(tmp_path):
    (tmp_path / 'back.jpg').write_bytes(b'x')
    (tmp_path / 'cover.txt').write_bytes(b'x')
    (tmp_path / 'cover.jpg').mkdir()
    assert aa.folder_cover_file(str(tmp_path)) is None


def test_a_disc_folder_counts_the_release_folder_cover(tmp_path):
    album = tmp_path / 'Stadium Arcadium'
    (album / 'Disc 1').mkdir(parents=True)
    (album / 'cover.jpg').write_bytes(b'\xff\xd8x')
    assert aa.folder_has_cover_sidecar(str(album / 'Disc 1')) is True
    assert aa.folder_has_cover_sidecar(str(album / 'Disc 1') + '/') is True


def test_an_ordinary_subfolder_does_not_borrow_its_parents_cover(tmp_path):
    (tmp_path / 'Artist' / 'Album').mkdir(parents=True)
    (tmp_path / 'Artist' / 'folder.jpg').write_bytes(b'x')
    assert aa.folder_has_cover_sidecar(str(tmp_path / 'Artist' / 'Album')) is False


def test_apply_writes_the_picked_cover_to_the_folder_over_the_files_own(tmp_path, monkeypatch):
    f = tmp_path / '01.mp3'
    f.write_bytes(b'')
    monkeypatch.setattr(aa, 'get_mutagen_symbols', lambda: SimpleNamespace(File=lambda p: None))
    monkeypatch.setattr(aa, 'extract_embedded_art', lambda p: b'EMBEDDED')
    monkeypatch.setattr(aa, 'download_cover_art', lambda *a, **k: pytest.fail('downloaded'))
    aa.apply_art_to_album_files([str(f)], {}, {}, folder=str(tmp_path), image_bytes=b'PICKED')
    assert (tmp_path / 'cover.jpg').read_bytes() == b'PICKED'


# ── the scan ──

def _findings_table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS repair_findings (
        id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, finding_type TEXT NOT NULL,
        severity TEXT DEFAULT 'info', status TEXT NOT NULL DEFAULT 'pending', entity_type TEXT,
        entity_id TEXT, file_path TEXT, title TEXT, description TEXT, details_json TEXT DEFAULT '{}',
        user_action TEXT, resolved_at TIMESTAMP, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    conn.execute("INSERT INTO repair_findings (job_id, finding_type, entity_type, entity_id, title) "
                 "VALUES ('missing_cover_art', 'missing_cover_art', 'album', 'lib2:1', 'old')")
    conn.commit()


def _status(conn):
    return conn.execute("SELECT status, user_action FROM repair_findings WHERE entity_id='lib2:1'").fetchone()


def _stadium_arcadium(tmp_path, monkeypatch, *, release_cover=True):
    album = tmp_path / 'Red Hot Chili Peppers' / 'Stadium Arcadium'
    (album / 'Disc 1').mkdir(parents=True)
    song = album / 'Disc 1' / '01 - Snow ((Hey Oh)).mp3'
    song.write_bytes(b'x')
    if release_cover:
        (album / 'cover.jpg').write_bytes(b'\xff\xd8x')
    conn = base._make_db(album_image='https://plex/thumb', file_path=str(song))
    _findings_table(conn)
    # embedding off, as in the reporter's settings
    context = base._make_context(conn, **{'metadata_enhancement.embed_album_art': False})
    # the real folder check runs; only the tag read is canned
    monkeypatch.setattr('core.tag_writer.read_file_tags', lambda p: {'has_cover_art': False})
    asked = []
    base._patch_artwork(monkeypatch, album=None, calls=asked)
    monkeypatch.setattr(mca, 'library_picture', lambda *a, **k: True)
    return conn, context


def test_a_two_disc_album_with_a_release_cover_is_not_flagged(tmp_path, monkeypatch):
    conn, context = _stadium_arcadium(tmp_path, monkeypatch)
    result = mca.MissingCoverArtJob().scan(context)
    assert result.findings_created == 0 and context.findings == []
    # the finding the old check filed closes
    assert tuple(_status(conn)) == ('resolved', 'obsolete')


def test_a_two_disc_album_with_no_cover_file_still_is(tmp_path, monkeypatch):
    conn, context = _stadium_arcadium(tmp_path, monkeypatch, release_cover=False)
    mca.MissingCoverArtJob().scan(context)
    assert len(context.findings) == 1
    details = context.findings[0]['details']
    assert (details['db_missing'], details['sidecar_missing']) == (False, True)
    assert _status(conn)[0] == 'pending'


def test_a_stopped_scan_closes_nothing(tmp_path, monkeypatch):
    conn, context = _stadium_arcadium(tmp_path, monkeypatch)
    calls = iter([False, True])
    context.check_stop = lambda: next(calls, True)
    # one album: let it be judged, then stop before the end of the walk
    conn.execute("INSERT INTO lib2_albums (id, primary_artist_id, title) VALUES (2, 1, 'Other')")
    conn.execute("INSERT INTO lib2_tracks (id, album_id, title, track_number) VALUES (2, 2, 'T', 1)")
    conn.execute("INSERT INTO lib2_track_files (track_id, path, format, is_primary) "
                 "VALUES (2, '/nowhere/x.flac', 'flac', 1)")
    conn.commit()
    mca.MissingCoverArtJob().scan(context)
    assert _status(conn)[0] == 'pending'


def _listen(monkeypatch, *, picture, found=None, embedded=False, sidecar=False):
    conn = base._make_db(album_image='/library/metadata/77/thumb/1', file_path='/music/Listen/01.mp3')
    context = base._make_context(conn, **{'metadata_enhancement.embed_album_art': False})
    base._patch_disk(monkeypatch, resolved=lambda raw: raw, embedded=embedded, sidecar=sidecar)
    asked = []
    base._patch_artwork(monkeypatch, album=found, calls=asked)
    monkeypatch.setattr(mca, 'library_picture', lambda *a, **k: picture)
    result = mca.MissingCoverArtJob().scan(context)
    return result, context.findings, [kind for kind, _ in asked]


def test_a_saved_cover_that_does_not_load_is_no_cover(monkeypatch):
    _result, findings, asked = _listen(monkeypatch, picture=False, found=base._found())
    assert 'album' in asked
    details = findings[0]['details']
    assert details['db_missing'] is True and details['library_cover_broken'] is True
    assert details['library_has_cover'] is False
    assert details['found_artwork_url'] == 'https://img/found'
    assert 'no longer loads' in findings[0]['description']


def test_nothing_to_fill_with_files_nothing(monkeypatch):
    result, findings, _asked = _listen(monkeypatch, picture=False, found=None)
    assert findings == [] and result.findings_created == 0


def test_a_loading_library_cover_is_still_copied(monkeypatch):
    _result, findings, asked = _listen(monkeypatch, picture=True)
    assert 'album' not in asked
    assert findings[0]['details']['library_has_cover'] is True
    assert findings[0]['details']['library_cover_broken'] is False


def test_a_folder_cover_fills_files_without_one(monkeypatch):
    conn = base._make_db(album_image='', file_path='/music/A/01.flac')
    context = base._make_context(conn)
    base._patch_disk(monkeypatch, resolved=lambda raw: raw, embedded=False, sidecar=True)
    base._patch_artwork(monkeypatch, album=None)
    mca.MissingCoverArtJob().scan(context)
    details = context.findings[0]['details']
    assert details['embed_missing'] is True and details['artwork_source'] == 'folder'


# ── is the library cover a picture ──

class _DB:
    def __init__(self, path, image_url=None, locked=0):
        from core.library2.schema import ensure_library_v2_schema
        self.path = str(path)
        self.database_path = str(path)
        conn = self._get_connection()
        ensure_library_v2_schema(conn)
        conn.execute("INSERT INTO lib2_artists (id, name, sort_name) VALUES (1, 'Sam Martin', 'Sam Martin')")
        conn.execute("INSERT INTO lib2_albums (id, primary_artist_id, title, image_url, art_locked) "
                     "VALUES (1, 1, 'Listen', ?, ?)", (image_url, locked))
        conn.execute("INSERT INTO lib2_tracks (id, album_id, title, track_number) VALUES (1, 1, 'Dangerous', 1)")
        conn.execute("INSERT INTO lib2_tracks (id, album_id, title, track_number) VALUES (2, 1, 'Listen', 2)")
        conn.execute("""CREATE TABLE repair_findings (id INTEGER PRIMARY KEY, job_id TEXT,
                        finding_type TEXT, details_json TEXT)""")
        conn.commit()
        conn.close()

    def _get_connection(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def add_file(self, track_id, path, has_cover):
        conn = self._get_connection()
        conn.execute("INSERT INTO lib2_track_files (track_id, path, format, is_primary, tags_json) "
                     "VALUES (?, ?, 'mp3', 1, ?)",
                     (track_id, str(path), json.dumps({'has_cover_art': has_cover})))
        conn.commit()
        conn.close()

    def image_url(self):
        conn = self._get_connection()
        try:
            return conn.execute("SELECT image_url FROM lib2_albums WHERE id=1").fetchone()[0]
        finally:
            conn.close()


def test_library_picture_reads_the_cache_then_builds(tmp_path, monkeypatch):
    from core.library2 import artwork
    db = _DB(tmp_path / 'm.db', image_url='/library/metadata/77/thumb/1')
    built = []
    monkeypatch.setattr(artwork, 'build_artwork', lambda *a, **k: built.append(1) or None)
    assert cas.library_picture(db, _cfg(), 1, '/library/metadata/77/thumb/1') is False
    assert built == [1]
    # a cover in the library's own cache is a picture, no build needed
    monkeypatch.setattr(artwork, 'is_cached_jpeg', lambda p: True)
    artwork.artwork_file(db, 'album', 1).write_bytes(b'\xff\xd8x')
    assert cas.library_picture(db, _cfg(), 1, '/library/metadata/77/thumb/1') is True
    assert built == [1]


def test_a_placeholder_or_blank_is_no_picture(tmp_path):
    db = _DB(tmp_path / 'm.db')
    assert cas.library_picture(db, _cfg(), 1, '') is False
    assert cas.library_picture(
        db, _cfg(), 1, 'https://lastfm.freetls.fastly.net/i/u/300x300/2a96cbd8b46e442fc41c2b86b821562f.png') is False


# ── what the finding offers ──

def _album(tmp_path, *, folder_cover=False, files_cover=False):
    album = tmp_path / 'Album'
    album.mkdir()
    for name in ('01.mp3', '02.mp3'):
        (album / name).write_bytes(b'x')
    if folder_cover:
        (album / 'Folder.JPG').write_bytes(b'FOLDER')
    return album


def test_every_tile_with_a_picture_is_a_pick_best_first(tmp_path, monkeypatch):
    album = _album(tmp_path, folder_cover=True)
    db = _DB(tmp_path / 'm.db', image_url='https://lib/x')
    db.add_file(1, album / '01.mp3', True)
    db.add_file(2, album / '02.mp3', False)
    monkeypatch.setattr('core.library2.paths.resolve_lib2_path', lambda p, **k: p)
    monkeypatch.setattr(cas, 'library_picture', lambda *a, **k: True)
    state = cas.cover_state(db, _cfg(), 4, {'album_id': 'lib2:1', 'found_artwork_url': 'https://deezer/x',
                                            'artwork_source': 'deezer'})
    assert state['choices'] == ['library', 'folder', 'files', 'found']
    assert state['pick'] == 'library'
    assert set(state['plans']) == {'library', 'folder', 'files', 'found'}
    found = {step['place']: step for step in state['plans']['found']}
    # the found cover picked over the library's own replaces it
    assert found['library'] == {'place': 'library', 'action': 'write', 'why': 'found', 'replace': True}
    folder = {step['place']: step for step in state['plans']['folder']}
    assert folder['library']['action'] == 'keep'
    assert folder['files'] == {'place': 'files', 'action': 'write', 'why': 'folder', 'count': 1}


def test_a_library_cover_that_does_not_load_is_shown_as_broken(tmp_path, monkeypatch):
    album = _album(tmp_path)
    db = _DB(tmp_path / 'm.db', image_url='/library/metadata/77/thumb/1')
    db.add_file(1, album / '01.mp3', False)
    monkeypatch.setattr('core.library2.paths.resolve_lib2_path', lambda p, **k: p)
    monkeypatch.setattr(cas, 'library_picture', lambda *a, **k: False)
    state = cas.cover_state(db, _cfg(**{'metadata_enhancement.embed_album_art': False}), 4,
                            {'album_id': 'lib2:1'})
    assert state['library'] == {'has': False, 'locked': False, 'broken': True, 'url': None}
    assert state['choices'] == [] and state['pick'] is None
    plan = {step['place']: step for step in state['plan']}
    assert plan['library'] == {'place': 'library', 'action': 'skip', 'why': 'broken'}
    assert plan['folder']['why'] == 'nothing_found'


# ── Apply Art ──

def _worker(db, cfg=None):
    from core.repair_worker import RepairWorker
    w = RepairWorker.__new__(RepairWorker)
    w.db = db
    w.transfer_folder = '.'
    w._config_manager = cfg or _cfg()
    return w


def _capture_apply(monkeypatch):
    got = []
    monkeypatch.setattr('core.library2.paths.resolve_lib2_path', lambda p, **k: p)
    monkeypatch.setattr(aa, 'apply_art_to_album_files',
                        lambda paths, m, info, folder=None, image_bytes=None, **k:
                        got.append(image_bytes) or {'cover_written': True})
    return got


def _tiles(monkeypatch, **pictures):
    """each tile's picture, as cover_image and the found download give it"""
    monkeypatch.setattr(cas, 'library_picture', lambda *a, **k: 'library' in pictures)
    monkeypatch.setattr(cas, 'cover_image', lambda db, cfg, details, place: pictures.get(place))
    from core.library2 import artwork
    monkeypatch.setattr(artwork, '_download_remote_artwork', lambda url: pictures.get('found'))


DUA_LIPA = {'album_id': 'lib2:1', 'db_missing': False, 'embed_missing': False,
            'sidecar_missing': True, 'sidecar_from_embedded': False,
            'found_artwork_url': None, 'artwork_source': 'library', 'library_has_cover': True}


def test_apply_copies_the_library_cover_with_no_found_url(tmp_path, monkeypatch):
    album = _album(tmp_path)
    db = _DB(tmp_path / 'm.db', image_url='https://plex/cover')
    db.add_file(1, album / '01.mp3', False)
    _tiles(monkeypatch, library=b'LIBRARY')
    got = _capture_apply(monkeypatch)
    res = _worker(db)._fix_missing_cover_art('album', 'lib2:1', None, dict(DUA_LIPA, _fix_action='album'))
    assert res['success'] is True, res
    assert got == [b'LIBRARY']
    assert "library's cover" in res['message']


def test_apply_uses_the_picked_tile(tmp_path, monkeypatch):
    album = _album(tmp_path)
    db = _DB(tmp_path / 'm.db', image_url='https://plex/cover')
    db.add_file(1, album / '01.mp3', False)
    _tiles(monkeypatch, library=b'LIBRARY', files=b'EMBEDDED', folder=b'FOLDER')
    got = _capture_apply(monkeypatch)
    details = dict(DUA_LIPA, _fix_action='album:files')
    res = _worker(db)._fix_missing_cover_art('album', 'lib2:1', None, details)
    assert res['success'] is True and got == [b'EMBEDDED']
    # the library keeps its own cover
    assert db.image_url() == 'https://plex/cover'


def test_picking_the_found_cover_replaces_the_library_one(tmp_path, monkeypatch):
    album = _album(tmp_path)
    db = _DB(tmp_path / 'm.db', image_url='https://plex/cover')
    db.add_file(1, album / '01.mp3', False)
    _tiles(monkeypatch, library=b'LIBRARY', found=b'DEEZER')
    got = _capture_apply(monkeypatch)
    picked = []
    from core.library2 import artwork
    monkeypatch.setattr(artwork, 'apply_manual_artwork',
                        lambda database, conn, kind, entity_id, url, profile_id=1:
                        picked.append((kind, entity_id, url)) or True)
    details = dict(DUA_LIPA, found_artwork_url='https://deezer/x', artwork_source='deezer',
                   _fix_action='album:found')
    res = _worker(db)._fix_missing_cover_art('album', 'lib2:1', None, details)
    assert res['success'] is True
    assert picked == [('album', 1, 'https://deezer/x')]
    assert got == [b'DEEZER']


def test_a_found_cover_replaces_a_saved_one_that_does_not_load(tmp_path, monkeypatch):
    album = _album(tmp_path)
    db = _DB(tmp_path / 'm.db', image_url='/library/metadata/77/thumb/1')
    db.add_file(1, album / '01.mp3', False)
    _tiles(monkeypatch, found=b'DEEZER')
    got = _capture_apply(monkeypatch)
    from core.library2 import artwork
    monkeypatch.setattr(artwork, 'invalidate_artwork', lambda *a, **k: 0)
    details = dict(DUA_LIPA, db_missing=True, found_artwork_url='https://deezer/x',
                   artwork_source='deezer', library_cover_broken=True, _fix_action='album')
    res = _worker(db)._fix_missing_cover_art('album', 'lib2:1', None, details)
    assert res['success'] is True and got == [b'DEEZER']
    assert db.image_url() == 'https://deezer/x'


def test_nothing_to_copy_says_so_and_points_at_change_cover(tmp_path, monkeypatch):
    album = _album(tmp_path)
    db = _DB(tmp_path / 'm.db', image_url='/library/metadata/77/thumb/1')
    db.add_file(1, album / '01.mp3', False)
    _tiles(monkeypatch)
    res = _worker(db)._fix_missing_cover_art('album', 'lib2:1', None, dict(DUA_LIPA, _fix_action='album'))
    assert res['success'] is False
    assert 'No artwork URL' not in res['error'] and 'Change cover' in res['error']


def test_a_files_pick_is_judged_on_the_files_and_folder_only(tmp_path, monkeypatch):
    """the library has no saved cover and none is picked for it: the check
    after the fix must not wait for one"""
    album = _album(tmp_path)
    db = _DB(tmp_path / 'm.db', image_url=None)
    db.add_file(1, album / '01.mp3', True)
    _tiles(monkeypatch, files=b'EMBEDDED', found=b'DEEZER')
    _capture_apply(monkeypatch)
    details = dict(DUA_LIPA, db_missing=True, found_artwork_url='https://deezer/x',
                   _fix_action='album:files')
    res = _worker(db)._fix_missing_cover_art('album', 'lib2:1', None, details)
    assert res['success'] is True
    assert details['found_artwork_url'] is None and details['db_missing'] is True
    assert db.image_url() is None
