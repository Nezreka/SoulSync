"""#2169: a Cover Art finding says what is in the library, the files and the
folder, where the found cover came from, and apply writes that cover.

The scan asks no source when the library already has a cover, and only
suggests an artist photo for an artist with none. A finding apply can only
half fill (no cover anywhere to save to the library) still closes. Apply
embeds the exact cover it showed, and the live state endpoint lists what
Apply Art does in each place under the user's settings.
"""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

from tests import test_missing_cover_art as base  # noqa: F401 - stubs spotipy/core.settings first
from core.library2.provider_adapters import ArtworkProviderResult
from core.repair_jobs import cover_art_state as cas
from core.repair_jobs import missing_cover_art as mca


@pytest.fixture(autouse=True)
def _no_name_search(monkeypatch):
    monkeypatch.setattr(mca, '_artist_art_by_name', lambda *a, **k: (None, None))


def _scan(monkeypatch, *, album_image, artist_image='https://artist/thumb',
          embedded=False, sidecar=False, album=None, artist=None):
    conn = base._make_db(album_image=album_image, file_path='/music/Album/01.flac')
    conn.execute("UPDATE lib2_artists SET image_url=?", (artist_image,))
    conn.commit()
    context = base._make_context(conn)
    base._patch_disk(monkeypatch, resolved=lambda raw: raw, embedded=embedded, sidecar=sidecar)
    calls = []
    base._patch_artwork(monkeypatch, album=album, artist=artist, calls=calls)
    result = mca.MissingCoverArtJob().scan(context)
    return result, context.findings, [kind for kind, _ in calls]


# ── the scan ──

def test_a_library_cover_is_copied_not_searched_for(monkeypatch):
    result, findings, asked = _scan(
        monkeypatch, album_image='https://has/thumb', embedded=False, sidecar=False,
        album=base._found())
    assert 'album' not in asked
    assert result.findings_created == 1
    details = findings[0]['details']
    assert details['artwork_source'] == 'library'
    assert details['found_artwork_url'] is None
    assert details['library_has_cover'] is True
    assert "library's own cover" in findings[0]['description']


def test_no_library_cover_still_asks_the_sources(monkeypatch):
    _result, findings, asked = _scan(monkeypatch, album_image='', album=base._found())
    assert 'album' in asked
    assert findings[0]['details']['artwork_source'] == 'spotify'


def test_an_artist_with_a_photo_gets_no_suggested_photo(monkeypatch):
    other = ArtworkProviderResult(kind='artist', source='deezer',
                                  provider_entity_id='a', url='https://other/photo')
    _result, findings, asked = _scan(monkeypatch, album_image='', album=base._found(), artist=other)
    assert 'artist' not in asked
    assert findings[0]['details']['found_artist_url'] is None


def test_an_artist_with_no_photo_gets_one_suggested(monkeypatch):
    other = ArtworkProviderResult(kind='artist', source='deezer',
                                  provider_entity_id='a', url='https://other/photo')
    _result, findings, asked = _scan(monkeypatch, album_image='', artist_image='',
                                     album=base._found(), artist=other)
    assert 'artist' in asked
    assert findings[0]['details']['found_artist_url'] == 'https://other/photo'


# ── closing a finding apply can only half fill ──

def _verified(details, checks):
    from core.library2.validation import finding_verified
    file = {'tags_json': json.dumps({'_validation': {'checks': checks}})}
    return finding_verified(None, {'finding_type': 'missing_cover_art', 'details': details}, [file])


def test_nothing_found_closes_once_files_and_folder_have_a_cover():
    checks = {'artwork_database': 'missing', 'cover': 'correct', 'artwork_sidecar': 'correct'}
    assert _verified({'db_missing': True, 'found_artwork_url': None}, checks) is True


def test_a_found_cover_must_reach_the_library():
    checks = {'artwork_database': 'missing', 'cover': 'correct', 'artwork_sidecar': 'correct'}
    assert _verified({'db_missing': True, 'found_artwork_url': 'https://x'}, checks) is False


def test_a_library_cover_that_went_missing_is_not_waved_through():
    checks = {'artwork_database': 'missing', 'cover': 'correct', 'artwork_sidecar': 'correct'}
    assert _verified({'db_missing': False}, checks) is False


# ── apply writes the cover it showed ──

def test_given_bytes_are_embedded_as_they_are(tmp_path, monkeypatch):
    from core.metadata import art_apply as aa
    import core.metadata.artwork as artwork
    f = tmp_path / '01.flac'
    f.write_bytes(b'')
    audio = SimpleNamespace(pictures=[], tags=None, add_tags=lambda: None, save=lambda: None)
    monkeypatch.setattr(aa, 'get_mutagen_symbols', lambda: SimpleNamespace(
        File=lambda p: audio, ID3=type('ID3', (), {}), MP4=type('MP4', (), {}),
        FLAC=type('FLAC', (), {})))
    monkeypatch.setattr(aa, 'embed_album_art_metadata',
                        lambda *a, **k: pytest.fail('looked the cover up again'))
    written = []
    monkeypatch.setattr(artwork, '_write_embedded_art',
                        lambda a, data, mime, symbols: written.append((data, mime)) or True)
    monkeypatch.setattr(aa, 'extract_embedded_art', lambda p: None)
    monkeypatch.setattr(aa, 'download_cover_art', lambda *a, **k: pytest.fail('downloaded again'))

    res = aa.apply_art_to_album_files([str(f)], {}, {}, folder=str(tmp_path), image_bytes=b'\xff\xd8shown')

    assert written == [(b'\xff\xd8shown', 'image/jpeg')]
    assert res['embedded'] == 1
    assert (tmp_path / 'cover.jpg').read_bytes() == b'\xff\xd8shown'


class _DB:
    def __init__(self, path, image_url=None, locked=0):
        from core.library2.schema import ensure_library_v2_schema
        self.path = str(path)
        conn = self._get_connection()
        ensure_library_v2_schema(conn)
        conn.execute("INSERT INTO lib2_artists (id, name, sort_name) VALUES (1, 'A', 'A')")
        conn.execute("INSERT INTO lib2_albums (id, primary_artist_id, title, image_url, art_locked) "
                     "VALUES (1, 1, 'B', ?, ?)", (image_url, locked))
        conn.execute("INSERT INTO lib2_tracks (id, album_id, title, track_number) VALUES (1, 1, 'S', 1)")
        conn.execute("INSERT INTO lib2_tracks (id, album_id, title, track_number) VALUES (2, 1, 'T', 2)")
        conn.commit()
        conn.close()

    def _get_connection(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def add_file(self, track_id, path, has_cover):
        conn = self._get_connection()
        conn.execute("INSERT INTO lib2_track_files (track_id, path, format, is_primary, tags_json) "
                     "VALUES (?, ?, 'flac', 1, ?)",
                     (track_id, str(path), json.dumps({'has_cover_art': has_cover})))
        conn.commit()
        conn.close()


def _worker(db, cfg=None):
    from core.repair_worker import RepairWorker
    w = RepairWorker.__new__(RepairWorker)
    w.db = db
    w.transfer_folder = '.'
    w._config_manager = cfg
    return w


def test_a_cover_saved_since_the_scan_is_kept(tmp_path, monkeypatch):
    """db_missing was true when the scan ran; a cover picked since then
    must not be overwritten by the found one"""
    db = _DB(tmp_path / 'm.db', image_url='https://picked/since')
    w = _worker(db)
    monkeypatch.setattr(w, '_cover_for_apply', lambda *a: ('library', b'LIB'))
    w._fix_missing_cover_art('album', 'lib2:1', None, {
        'album_id': 'lib2:1', 'db_missing': True, 'found_artwork_url': 'https://found/x'})
    conn = db._get_connection()
    assert conn.execute("SELECT image_url FROM lib2_albums WHERE id=1").fetchone()[0] == 'https://picked/since'
    conn.close()


def test_apply_hands_the_library_cover_to_the_files(tmp_path, monkeypatch):
    db = _DB(tmp_path / 'm.db', image_url='https://library/cover')
    song = tmp_path / '01.flac'
    song.write_bytes(b'x')
    db.add_file(1, song, False)
    w = _worker(db)
    asked = []
    monkeypatch.setattr(w, '_cover_for_apply',
                        lambda album_id, wanted, found: asked.append((wanted, found)) or ('library', b'LIB'))
    monkeypatch.setattr('core.library2.paths.resolve_lib2_path', lambda p, **k: p)
    got = {}
    import core.metadata.art_apply as aa
    monkeypatch.setattr(aa, 'apply_art_to_album_files',
                        lambda paths, m, info, folder=None, image_bytes=None, **k:
                        got.update(image_bytes=image_bytes) or {'embedded': 1})
    w._fix_missing_cover_art('album', 'lib2:1', None, {
        'album_id': 'lib2:1', 'db_missing': False, 'found_artwork_url': 'https://found/x'})
    assert asked == [('', 'https://found/x')]
    assert got['image_bytes'] == b'LIB'


# ── what the finding shows ──

def _cfg(**values):
    return SimpleNamespace(get=lambda key, default=None: values.get(key, default))


def test_state_reads_each_place_and_plans_from_the_found_cover(tmp_path, monkeypatch):
    album = tmp_path / 'Album'
    album.mkdir()
    db = _DB(tmp_path / 'm.db', image_url=None)
    db.add_file(1, album / '01.flac', True)
    db.add_file(2, album / '02.flac', False)
    (album / '01.flac').write_bytes(b'x')
    (album / '02.flac').write_bytes(b'x')
    monkeypatch.setattr('core.library2.paths.resolve_lib2_path', lambda p, **k: p)
    state = cas.cover_state(db, _cfg(), 7, {
        'album_id': 'lib2:1', 'found_artwork_url': 'https://deezer/x', 'artwork_source': 'deezer'})

    assert state['library'] == {'has': False, 'locked': False, 'broken': False, 'url': None}
    assert (state['files']['with'], state['files']['without']) == (1, 1)
    assert state['files']['url'] == '/api/repair/findings/7/cover-image?place=files'
    assert state['folder']['file'] is None and state['folder']['exists'] is True
    assert state['found'] == {'url': 'https://deezer/x', 'source': 'deezer'}
    # #2202: the cover the album already has comes first, the found one is
    # a pick away
    assert state['choices'] == ['files', 'found'] and state['pick'] == 'files'
    plan = {step['place']: step for step in state['plan']}
    assert plan['library'] == {'place': 'library', 'action': 'skip', 'why': 'local'}
    assert plan['files'] == {'place': 'files', 'action': 'write', 'why': 'files', 'count': 1}
    assert plan['folder'] == {'place': 'folder', 'action': 'write', 'why': 'files'}
    found = {step['place']: step for step in state['plans']['found']}
    assert found['library'] == {'place': 'library', 'action': 'write', 'why': 'found'}
    assert found['files'] == {'place': 'files', 'action': 'write', 'why': 'found', 'count': 1}
    assert found['folder'] == {'place': 'folder', 'action': 'write', 'why': 'found'}


def test_state_names_the_folder_cover_and_honours_settings(tmp_path, monkeypatch):
    album = tmp_path / 'Album'
    album.mkdir()
    (album / 'Folder.JPG').write_bytes(b'\xff\xd8x')
    db = _DB(tmp_path / 'm.db', image_url='https://lib/x', locked=1)
    db.add_file(1, album / '01.flac', False)
    (album / '01.flac').write_bytes(b'x')
    monkeypatch.setattr('core.library2.paths.resolve_lib2_path', lambda p, **k: p)
    state = cas.cover_state(db, _cfg(**{'metadata_enhancement.embed_album_art': False}), 3,
                            {'album_id': 'lib2:1'})

    assert state['folder']['file'] == 'Folder.JPG'
    assert state['library']['locked'] is True
    plan = {step['place']: step for step in state['plan']}
    assert plan['library']['why'] == 'chosen' and plan['library']['action'] == 'keep'
    assert plan['files'] == {'place': 'files', 'action': 'skip', 'why': 'setting_off'}
    assert plan['folder']['action'] == 'keep' and plan['folder']['file'] == 'Folder.JPG'
    assert cas.cover_image(db, _cfg(), {'album_id': 'lib2:1'}, 'folder') == b'\xff\xd8x'


def test_nothing_anywhere_plans_nothing():
    state = {'library': {'has': False, 'locked': False}, 'files': {'with': 0, 'without': 2},
             'folder': {'file': None, 'exists': True}, 'found': {'url': None},
             'settings': {'embed': True, 'sidecar': True}}
    assert [step['action'] for step in cas.apply_plan(state)] == ['skip', 'skip', 'skip']


def test_state_endpoint_only_answers_cover_findings(tmp_path):
    db = _DB(tmp_path / 'm.db')
    conn = db._get_connection()
    conn.execute("""CREATE TABLE IF NOT EXISTS repair_findings (id INTEGER PRIMARY KEY,
                    job_id TEXT, finding_type TEXT, details_json TEXT)""")
    conn.execute("INSERT INTO repair_findings VALUES (1, 'x', 'missing_lyrics', '{\"album_id\": \"lib2:1\"}')")
    conn.commit()
    conn.close()
    assert _worker(db, _cfg()).cover_finding_state(1) is None
